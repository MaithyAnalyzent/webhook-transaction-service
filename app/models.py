import enum
from datetime import datetime

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TransactionStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class Transaction(Base):
    __tablename__ = "transactions"

    # transaction_id is the primary key (not a separate surrogate id) so the
    # database itself enforces idempotency via a uniqueness constraint —
    # a second insert with the same id is rejected at the DB layer, not
    # just caught in application code.
    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_account: Mapped[str] = mapped_column(String, nullable=False)
    destination_account: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=TransactionStatus.PROCESSING.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
