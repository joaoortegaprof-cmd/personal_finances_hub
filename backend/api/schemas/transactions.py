"""
Schemas Pydantic para transações financeiras.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.transaction import TransactionCategory, TransactionType


class TransactionCreate(BaseModel):
    description: str = Field(max_length=255)
    amount: Decimal = Field(gt=0)
    transaction_date: date
    transaction_type: TransactionType
    category: TransactionCategory = TransactionCategory.OTHER
    account_id: int | None = None
    credit_card_id: int | None = None
    invoice_id: int | None = None
    notes: str | None = None
    tags: str | None = Field(default=None, max_length=500)
    is_recurring: bool = False


class TransactionUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    amount: Decimal | None = Field(default=None, gt=0)
    transaction_date: date | None = None
    transaction_type: TransactionType | None = None
    category: TransactionCategory | None = None
    account_id: int | None = None
    credit_card_id: int | None = None
    invoice_id: int | None = None
    notes: str | None = None
    tags: str | None = Field(default=None, max_length=500)
    is_recurring: bool | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    amount: Decimal
    transaction_date: date
    transaction_type: TransactionType
    category: TransactionCategory
    account_id: int | None
    credit_card_id: int | None
    invoice_id: int | None
    notes: str | None
    tags: str | None
    is_recurring: bool
    created_at: datetime
    updated_at: datetime


class MonthlySummaryOut(BaseModel):
    income: Decimal
    expense: Decimal
    balance: Decimal
    savings_rate: Decimal
    reference_month: str  # "MM/AAAA"
