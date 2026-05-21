"""
Schemas Pydantic para dívidas e pagamentos.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.debt import DebtType


class DebtCreate(BaseModel):
    name: str = Field(max_length=200)
    institution: str = Field(max_length=200)
    total_amount: Decimal = Field(gt=0)
    remaining_amount: Decimal = Field(ge=0)
    interest_rate: Decimal = Field(gt=0, description="Taxa mensal em % (ex: 1.5 = 1,5% a.m.)")
    installment_amount: Decimal = Field(gt=0)
    total_installments: int = Field(gt=0)
    paid_installments: int = Field(ge=0, default=0)
    start_date: date
    due_day: int = Field(ge=1, le=31)
    debt_type: DebtType
    is_active: bool = True


class DebtUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    institution: str | None = Field(default=None, max_length=200)
    remaining_amount: Decimal | None = Field(default=None, ge=0)
    interest_rate: Decimal | None = Field(default=None, gt=0)
    installment_amount: Decimal | None = Field(default=None, gt=0)
    paid_installments: int | None = Field(default=None, ge=0)
    due_day: int | None = Field(default=None, ge=1, le=31)
    is_active: bool | None = None


class DebtOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    institution: str
    total_amount: Decimal
    remaining_amount: Decimal
    interest_rate: Decimal
    installment_amount: Decimal
    total_installments: int
    paid_installments: int
    start_date: date
    due_day: int
    debt_type: DebtType
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DebtPaymentCreate(BaseModel):
    payment_date: date
    amount_paid: Decimal = Field(gt=0)
    principal_paid: Decimal = Field(ge=0)
    interest_paid: Decimal = Field(ge=0)
    remaining_after: Decimal = Field(ge=0)


class DebtPaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    debt_id: int
    payment_date: date
    amount_paid: Decimal
    principal_paid: Decimal
    interest_paid: Decimal
    remaining_after: Decimal
    created_at: datetime


class ScheduleInstallment(BaseModel):
    """Uma parcela do cronograma de amortização projetado (Tabela Price)."""
    installment_number: int
    due_date: date
    installment_amount: Decimal
    interest: Decimal
    principal: Decimal
    remaining_balance: Decimal


class DebtScheduleOut(BaseModel):
    debt_id: int
    debt_name: str
    interest_rate_monthly: Decimal
    remaining_installments: int
    schedule: list[ScheduleInstallment]
