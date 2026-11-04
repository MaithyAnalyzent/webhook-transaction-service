from datetime import datetime

from pydantic import BaseModel, Field


class WebhookPayload(BaseModel):
    transaction_id: str = Field(..., min_length=1)
    source_account: str = Field(..., min_length=1)
    destination_account: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)


class TransactionOut(BaseModel):
    transaction_id: str
    source_account: str
    destination_account: str
    amount: float
    currency: str
    status: str
    created_at: datetime
    processed_at: datetime | None

    class Config:
        from_attributes = True


class HealthOut(BaseModel):
    status: str
    current_time: str
