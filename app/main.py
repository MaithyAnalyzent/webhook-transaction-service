import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RECOVER_STUCK_ON_STARTUP
from app.crud import create_transaction_if_new, get_stuck_processing, get_transaction
from app.database import get_session, init_models
from app.schemas import HealthOut, TransactionOut, WebhookPayload
from app.worker import process_transaction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    if RECOVER_STUCK_ON_STARTUP:
        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            stuck = await get_stuck_processing(session)
        for txn in stuck:
            logger.info(
                "re-queuing transaction left PROCESSING from before restart",
                extra={"transaction_id": txn.transaction_id},
            )
            import asyncio

            asyncio.create_task(process_transaction(txn.transaction_id))
    yield


app = FastAPI(title="Transaction Webhook Service", lifespan=lifespan)


@app.get("/", response_model=HealthOut)
async def health_check():
    return HealthOut(
        status="HEALTHY",
        current_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@app.post("/v1/webhooks/transactions", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(
    payload: WebhookPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    # Single atomic DB write decides idempotency (see crud.create_transaction_if_new).
    # Everything below is intentionally cheap so the handler stays well
    # under the 500ms budget regardless of how long downstream processing
    # (the 30s simulated delay) takes.
    is_new = await create_transaction_if_new(session, payload)
    if is_new:
        background_tasks.add_task(process_transaction, payload.transaction_id)
    else:
        logger.info(
            "duplicate webhook ignored",
            extra={"transaction_id": payload.transaction_id},
        )
    return {"transaction_id": payload.transaction_id, "accepted": True}


@app.get("/v1/transactions/{transaction_id}", response_model=list[TransactionOut])
async def get_transaction_status(
    transaction_id: str, session: AsyncSession = Depends(get_session)
):
    txn = await get_transaction(session, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    # Response is wrapped in a list to match the exact contract given in
    # the assessment spec, even though this endpoint returns a single
    # resource by id.
    return [txn]
