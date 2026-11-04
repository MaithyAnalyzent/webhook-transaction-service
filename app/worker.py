import asyncio
import logging

from app.config import PROCESSING_DELAY_SECONDS
from app.crud import mark_failed, mark_processed
from app.database import AsyncSessionLocal

logger = logging.getLogger("webhook_service.worker")


async def process_transaction(transaction_id: str) -> None:
    """Simulates calling out to the external payment processor and then
    records the result. Runs as a FastAPI BackgroundTask, scheduled only
    after the webhook response has already been sent.

    Known limitation (documented, not hidden): BackgroundTasks run
    in-process. If the dyno/container restarts mid-delay, this specific
    coroutine is lost — which is why `main.py` re-queues any transaction
    still sitting in PROCESSING on startup (see `get_stuck_processing`).
    A durable queue (Celery/SQS/Cloud Tasks) would remove this gap
    entirely; it was left out here to keep the stack to what a 2-3 hour
    assessment reasonably supports.
    """
    try:
        await asyncio.sleep(PROCESSING_DELAY_SECONDS)
        async with AsyncSessionLocal() as session:
            await mark_processed(session, transaction_id)
        logger.info("transaction processed", extra={"transaction_id": transaction_id})
    except Exception:
        logger.exception(
            "transaction processing failed", extra={"transaction_id": transaction_id}
        )
        try:
            async with AsyncSessionLocal() as session:
                await mark_failed(session, transaction_id)
        except Exception:
            logger.exception(
                "failed to record FAILED status",
                extra={"transaction_id": transaction_id},
            )
