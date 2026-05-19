"""
Schemas Pydantic para contas bancárias e cartões de crédito.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.account import AccountType


class AccountCreate(BaseModel):
    name: str = Field(max_length=100)
    bank_name: str = Field(max_length=100)
    account_type: AccountType
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    initial_balance: Decimal = Decimal("0.00")
    initial_balance_date: date


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    bank_name: str | None = Field(default=None, max_length=100)
    account_type: AccountType | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    initial_balance: Decimal | None = None
    initial_balance_date: date | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    bank_name: str
    account_type: AccountType
    currency: str
    initial_balance: Decimal
    initial_balance_date: date
    created_at: datetime
    updated_at: datetime


class BalanceOut(BaseModel):
    account_id: int
    balance: Decimal


class CreditCardCreate(BaseModel):
    name: str = Field(max_length=100)
    bank_name: str = Field(max_length=100)
    last_four_digits: str = Field(min_length=4, max_length=4)
    credit_limit: Decimal = Field(gt=0)
    closing_day: int = Field(ge=1, le=28)
    due_day: int = Field(ge=1, le=28)
    payment_account_id: int | None = None


class CreditCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    bank_name: str
    last_four_digits: str
    credit_limit: Decimal
    closing_day: int
    due_day: int
    payment_account_id: int | None
    created_at: datetime
    updated_at: datetime
