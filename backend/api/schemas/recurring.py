from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.recurring import Periodicity


class RecurringExpenseCreate(BaseModel):
    name: str = Field(max_length=150)
    amount: Decimal = Field(gt=0)
    category: str = Field(default="outros", max_length=50)
    periodicity: Periodicity = Periodicity.MONTHLY
    next_due_date: date
    notes: str | None = None


class RecurringExpenseUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    amount: Decimal | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, max_length=50)
    periodicity: Periodicity | None = None
    next_due_date: date | None = None
    notes: str | None = None
    is_active: bool | None = None


class RecurringExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    amount: Decimal
    category: str
    periodicity: Periodicity
    next_due_date: date
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RecurringSummaryOut(BaseModel):
    monthly_total: Decimal
    annual_total: Decimal
    active_count: int
    upcoming: list[RecurringExpenseOut]   # vencendo em 7 dias
    overdue: list[RecurringExpenseOut]    # vencidas
