from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction, TransactionStatus
from app.schemas import WebhookPayload


def _insert_stmt(engine_name: str):
    """Pick the right dialect's INSERT ... ON CONFLICT DO NOTHING builder.

    This is what actually enforces idempotency: the uniqueness check and
    the write happen as one atomic statement on the database, so two
    webhook deliveries racing each other can't both "see no existing row"
    and both insert — the DB resolves the race, not our Python code.
    """
    if engine_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    return insert


async def create_transaction_if_new(
    session: AsyncSession, payload: WebhookPayload
) -> bool:
    """Insert the transaction in PROCESSING state if it doesn't exist yet.

    Returns True if this call created a new row (caller should queue
    background processing), False if the transaction_id was already
    present (duplicate webhook — do nothing further).
    """
    insert = _insert_stmt(session.bind.dialect.name)
    stmt = (
        insert(Transaction)
        .values(
            transaction_id=payload.transaction_id,
            source_account=payload.source_account,
            destination_account=payload.destination_account,
            amount=payload.amount,
            currency=payload.currency,
            status=TransactionStatus.PROCESSING.value,
            created_at=datetime.now(timezone.utc),
            processed_at=None,
        )
        .on_conflict_do_nothing(index_elements=["transaction_id"])
    )
    result = await session.execute(stmt)
    await session.commit()
    # rowcount is 1 if the insert happened, 0 if ON CONFLICT DO NOTHING fired.
    return result.rowcount > 0


async def mark_processed(session: AsyncSession, transaction_id: str) -> None:
    txn = await session.get(Transaction, transaction_id)
    if txn is None:
        return
    txn.status = TransactionStatus.PROCESSED.value
    txn.processed_at = datetime.now(timezone.utc)
    await session.commit()


async def mark_failed(session: AsyncSession, transaction_id: str) -> None:
    txn = await session.get(Transaction, transaction_id)
    if txn is None:
        return
    txn.status = TransactionStatus.FAILED.value
    await session.commit()


async def get_transaction(
    session: AsyncSession, transaction_id: str
) -> Transaction | None:
    return await session.get(Transaction, transaction_id)


async def get_stuck_processing(session: AsyncSession) -> list[Transaction]:
    """Transactions still PROCESSING from before the last restart — these
    are the ones at risk of being silently lost if we don't re-queue them.
    """
    result = await session.execute(
        select(Transaction).where(
            Transaction.status == TransactionStatus.PROCESSING.value
        )
    )
    return list(result.scalars().all())
