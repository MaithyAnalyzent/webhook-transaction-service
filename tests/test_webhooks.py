import asyncio
import time
import uuid

import pytest


def make_payload() -> dict:
    # Unique transaction_id per call so tests can't leak into each other
    # via a still-in-flight background task from a previous test.
    return {
        "transaction_id": f"txn_test_{uuid.uuid4().hex[:12]}",
        "source_account": "acc_user_789",
        "destination_account": "acc_merchant_456",
        "amount": 1500,
        "currency": "INR",
    }


@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "HEALTHY"
    assert "current_time" in body


@pytest.mark.asyncio
async def test_webhook_returns_202_fast(client):
    payload = make_payload()
    start = time.monotonic()
    resp = await client.post("/v1/webhooks/transactions", json=payload)
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    # This is the actual requirement from the assessment spec: respond
    # within 500ms regardless of processing complexity. Since this test
    # now hits a real uvicorn server over a socket (not ASGITransport),
    # elapsed time genuinely excludes the 0.5s background processing.
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_transaction_is_processing_then_processed(client):
    payload = make_payload()
    await client.post("/v1/webhooks/transactions", json=payload)

    immediate = await client.get(f"/v1/transactions/{payload['transaction_id']}")
    assert immediate.status_code == 200
    assert immediate.json()[0]["status"] == "PROCESSING"
    assert immediate.json()[0]["processed_at"] is None

    # PROCESSING_DELAY_SECONDS is shrunk to 0.5s in conftest for tests.
    await asyncio.sleep(1.0)

    later = await client.get(f"/v1/transactions/{payload['transaction_id']}")
    body = later.json()[0]
    assert body["status"] == "PROCESSED"
    assert body["processed_at"] is not None


@pytest.mark.asyncio
async def test_duplicate_webhook_is_idempotent(client):
    payload = make_payload()
    for _ in range(5):
        resp = await client.post("/v1/webhooks/transactions", json=payload)
        assert resp.status_code == 202

    await asyncio.sleep(1.0)

    result = await client.get(f"/v1/transactions/{payload['transaction_id']}")
    assert len(result.json()) == 1
    assert result.json()[0]["status"] == "PROCESSED"


@pytest.mark.asyncio
async def test_unknown_transaction_returns_404(client):
    resp = await client.get("/v1/transactions/does_not_exist")
    assert resp.status_code == 404
