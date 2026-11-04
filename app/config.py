import os

# Falls back to a local SQLite file when no DATABASE_URL is set, so the
# service runs with zero external setup for local development. Render
# injects a real Postgres DATABASE_URL in production.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./local.db")

# Simulates the latency of a downstream processor call. Overridable so
# tests don't have to burn 30 real seconds per case.
PROCESSING_DELAY_SECONDS = float(os.getenv("PROCESSING_DELAY_SECONDS", "30"))

# On boot, re-queues any transaction that was left in PROCESSING when the
# process last stopped (crash, deploy restart, etc.) instead of losing it.
RECOVER_STUCK_ON_STARTUP = os.getenv("RECOVER_STUCK_ON_STARTUP", "true").lower() == "true"
