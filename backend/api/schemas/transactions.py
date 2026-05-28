"""
Schemas Pydantic para transações financeiras.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.transaction import (
    CATEGORY_TO_NATURE,
    ExpenseNature,
    TransactionCategory,
    TransactionType,
)


class TransactionCreate(BaseModel):
    description: str = Field(max_length=255)
    amount: Decimal = Field(gt=0)
    transaction_date: date
    transaction_type: TransactionType
    category: TransactionCategory = TransactionCategory.OTHER
    expense_nature: ExpenseNature | None = None
    account_id: int | None = None
    credit_card_id: int | None = None
    invoice_id: int | None = None
    notes: str | None = None
    tags: str | None = Field(default=None, max_length=500)
    is_recurring: bool = False
    is_emergency_fund: bool = False
    # Campos opcionais para criação automática de posição de carteira
    asset_id: int | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    fees: Decimal | None = None

    def model_post_init(self, __context) -> None:
        # Auto-classifica a natureza se não informada
        if self.expense_nature is None:
            if self.transaction_type == TransactionType.INVESTMENT:
                object.__setattr__(self, "expense_nature", ExpenseNature.INVESTMENT)
            elif self.transaction_type == TransactionType.INVOICE_PAYMENT:
                object.__setattr__(self, "expense_nature", ExpenseNature.TRANSFER)
            elif self.transaction_type in (
                TransactionType.DEBIT_EXPENSE,
                TransactionType.CREDIT_EXPENSE,
            ):
                nature = CATEGORY_TO_NATURE.get(self.category)
                if nature is not None:
                    object.__setattr__(self, "expense_nature", nature)


class TransactionUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    amount: Decimal | None = Field(default=None, gt=0)
    transaction_date: date | None = None
    transaction_type: TransactionType | None = None
    category: TransactionCategory | None = None
    expense_nature: ExpenseNature | None = None
    account_id: int | None = None
    credit_card_id: int | None = None
    invoice_id: int | None = None
    notes: str | None = None
    tags: str | None = Field(default=None, max_length=500)
    is_recurring: bool | None = None
    is_emergency_fund: bool | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    amount: Decimal
    transaction_date: date
    transaction_type: TransactionType
    category: TransactionCategory
    expense_nature: ExpenseNature | None
    account_id: int | None
    credit_card_id: int | None
    invoice_id: int | None
    notes: str | None
    tags: str | None
    is_recurring: bool
    is_emergency_fund: bool
    created_at: datetime
    updated_at: datetime


class MonthlySummaryOut(BaseModel):
    income: Decimal
    expense: Decimal
    balance: Decimal
    savings_rate: Decimal
    reference_month: str  # "MM/AAAA"


class EmergencyFundOut(BaseModel):
    saldo_total: Decimal         # soma de lançamentos is_emergency_fund=True
    media_gastos_6m: Decimal     # média mensal de despesas dos últimos 6 meses
    meses_cobertos: Decimal      # saldo_total / media_gastos_6m
