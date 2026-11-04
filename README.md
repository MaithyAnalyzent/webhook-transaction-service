# Transaction Webhook Service

A FastAPI service that receives transaction webhooks (modeled on something like
RazorPay), acknowledges them immediately, and processes them reliably in the
background — without losing or double-processing a transaction, even across
duplicate deliveries or a server restart mid-processing.

**Live API:** https://webhook-transaction-service-hgxw.onrender.com
**Health check:** https://webhook-transaction-service-hgxw.onrender.com/

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Health check |
| `POST` | `/v1/webhooks/transactions` | Receive a transaction webhook (returns `202` immediately) |
| `GET` | `/v1/transactions/{transaction_id}` | Check a transaction's current status (for testing) |

## Quick start — Docker Compose (recommended)

This spins up the API plus a real Postgres instance, which is the same shape
as production.

```bash
docker compose up --build
```

Then, in another terminal:

```bash
curl http://localhost:8000/

curl -X POST http://localhost:8000/v1/webhooks/transactions \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "txn_abc123def456",
    "source_account": "acc_user_789",
    "destination_account": "acc_merchant_456",
    "amount": 1500,
    "currency": "INR"
  }'

# immediately after -> status "PROCESSING"
curl http://localhost:8000/v1/transactions/txn_abc123def456

# wait ~30s, then -> status "PROCESSED", processed_at populated
curl http://localhost:8000/v1/transactions/txn_abc123def456
```

Send the same webhook again (same `transaction_id`) as many times as you
like — it will keep returning `202`, but only one row and one background
processing run ever happens for that id.

## Quick start — without Docker

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

With no `DATABASE_URL` set, the service falls back to a local SQLite file
(`local.db`) automatically — no external database needed to try it out.

## Running the tests

```bash
docker build -t webhook-service-test .
docker run --rm -v "$(pwd)/tests:/app/tests" -v "$(pwd)/pytest.ini:/app/pytest.ini" webhook-service-test pytest -v
```

The tests spin up a real `uvicorn` process per session and hit it over a
real socket (see the comment at the top of `tests/conftest.py` for why — in
short, httpx's in-process ASGI test transport fully awaits background tasks
before returning, which would hide the exact behavior this service exists
to prove, so an in-process test can't honestly validate it).

Covers: health check, sub-500ms webhook response time, PROCESSING → PROCESSED
transition after the delay, duplicate-webhook idempotency, and 404 on an
unknown transaction id.

## Technical choices, and why

**FastAPI + async SQLAlchemy.** Needed something that handles concurrent
webhook deliveries cheaply while a lot of transactions are mid-processing in
the background. FastAPI's `async`/`await` model plus an async Postgres driver
(`asyncpg`) keeps the webhook handler non-blocking.

**Idempotency enforced at the database, not in application code.** The
webhook handler does `INSERT ... ON CONFLICT (transaction_id) DO NOTHING`
as a single atomic statement, with `transaction_id` as the primary key.
Background processing is only queued if that insert actually created a row.
This matters because two webhook deliveries can arrive close enough together
that a naive "check if it exists, then insert" would race — both requests
could pass the check before either insert lands. Making the DB resolve the
conflict means there's no window for that race, regardless of how many
duplicate deliveries arrive concurrently.

**FastAPI `BackgroundTasks` for processing, not Celery/a message queue.**
Honest tradeoff, not an oversight: a durable task queue (Celery+Redis, SQS,
Cloud Tasks) is what a production payment system would actually want, because
it survives process crashes and supports retries with backoff out of the box.
For a 2-3 hour assessment, standing up and operating that infrastructure
seemed like the wrong thing to spend the time budget on. `BackgroundTasks`
gets the required behavior (respond first, process after) with zero extra
moving parts.

To close the obvious gap that leaves — an in-process background task dies if
the server restarts mid-processing — the service re-queues any transaction
still in `PROCESSING` state on startup (`get_stuck_processing` in `crud.py`,
wired up in `main.py`'s lifespan handler). This was tested directly: start a
transaction, kill and restart the container mid-delay, confirm it finishes
processing after the restart instead of hanging forever in `PROCESSING`.
It's not a full replacement for a durable queue (it doesn't handle multiple
replicas cleanly, and there's no retry/backoff on repeated failures), but it
means a single-instance deploy — which is what this assessment runs — doesn't
silently lose a transaction to a routine restart.

**Postgres in production, SQLite for local dev.** `DATABASE_URL` is read
from the environment and normalized to the right async driver DSN; if it's
unset, the service falls back to a local SQLite file so a reviewer can run it
with zero setup. Render provisions a managed Postgres instance and injects
`DATABASE_URL` automatically via `render.yaml`.

**`create_all()` instead of Alembic migrations.** Reasonable for a
single-table service built in a few hours; would switch to proper migrations
before adding a second table or shipping a schema change to a service with
real data in it.

**No webhook signature verification.** Real payment processors (RazorPay
included) sign webhook payloads with an HMAC secret so you can verify a
request actually came from them and wasn't forged or replayed by a third
party. The assessment spec's request body doesn't include a signature header
or shared secret to verify against, so this wasn't implemented — but it's the
first thing to add before this touched real money.

## Performance notes

Locally, against Postgres in docker-compose, the webhook endpoint responds in
~38ms consistently. On Render's free tier, two separate issues showed up
during testing, worth documenting honestly rather than glossing over:

**1. Connection pool contention (fixed).** Initial testing showed a baseline
of ~320-400ms with periodic spikes to 800-860ms on roughly every 4th
request. Hypothesis: the default SQLAlchemy async pool (5 connections, 10
overflow) was getting contended whenever a burst of background
`mark_processed` writes (from transactions finishing their 30s delay) landed
at the same time as new incoming webhook requests, both competing for a
small number of pooled connections. Widened the pool
(`pool_size=20, max_overflow=20`) in `app/database.py` for the Postgres path
specifically (SQLite, used for local dev and tests, keeps its own default
pooling). Re-testing after the change showed a lower, more consistent
baseline (~320-380ms), though occasional spikes in the 700-800ms range
persisted — which points to the second issue below rather than pool sizing
being the sole cause.

**2. Free-tier shared-CPU jitter (documented, not fully fixable from app
code).** Render's free web service runs on shared/throttled CPU, and
`asyncpg`/SQLAlchemy plus Python's single-threaded event loop occasionally
see scheduling delays under that throttling that have nothing to do with
query complexity — this single-row INSERT has no reason to take 700ms on its
own merits, and doesn't, locally. This is an inherent free-tier
characteristic; a paid Render instance (or any dedicated-CPU host) would
remove it. Noting this rather than claiming the pool fix solved everything
it didn't.

**3. Cold start after idling (mitigated).** Render spins the free web
service down after ~15 minutes of inactivity; the next request pays a
10-20+ second cold-start penalty, which would badly blow the 500ms budget on
a grader's first request if the service had been sitting idle. Added a
GitHub Actions workflow (`.github/workflows/keep-alive.yml`) that pings the
health check every 10 minutes to keep the instance warm without needing a
paid plan.

## Known limitations

- Single-instance reliability only. The startup-recovery mechanism assumes
  one process; running multiple replicas without a shared queue could cause
  more than one instance to pick up the same stuck transaction.
- No retry/backoff policy if downstream processing fails repeatedly —
  transactions move to `FAILED` once and stop there.
- No webhook signature verification (see above).
- No rate limiting or auth on the webhook endpoint.

All of the above are the right next steps for a production version of this
service, and are omitted deliberately to fit the assessment's scope rather
than overlooked.

## Project structure

```
app/
  main.py       — FastAPI app, route handlers, startup recovery
  worker.py     — background processing logic (the simulated 30s delay)
  crud.py       — idempotent insert, status updates, stuck-transaction query
  models.py     — SQLAlchemy Transaction model
  schemas.py    — Pydantic request/response models
  database.py   — async engine/session setup, SQLite<->Postgres DSN handling
  config.py     — environment-driven settings
tests/
  conftest.py   — spins up a real server per test session (see note above)
  test_webhooks.py
Dockerfile
docker-compose.yml  — API + Postgres for local dev/testing
render.yaml         — Render Blueprint: web service + managed Postgres
```

## Deployment

Deployed on [Render](https://render.com) via the `render.yaml` Blueprint in
this repo, which provisions a free-tier web service (built from the
`Dockerfile`) and a free-tier managed Postgres instance, wired together
automatically.
