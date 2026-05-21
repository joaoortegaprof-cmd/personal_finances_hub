"""Schemas Pydantic para o endpoint de proventos (/dividends)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from backend.models.dividend import DividendType


class DividendCreate(BaseModel):
    asset_id: int
    payment_date: date
    record_date: date | None = None
    amount_per_unit: Decimal = Field(ge=0, decimal_places=6)
    total_amount: Decimal = Field(ge=0, decimal_places=2)
    dividend_type: DividendType = DividendType.DIVIDENDO
    is_taxable: bool = False
    tax_withheld: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)

    @model_validator(mode="after")
    def jcp_must_be_taxable(self) -> "DividendCreate":
        if self.dividend_type == DividendType.JCP and not self.is_taxable:
            self.is_taxable = True
        return self


class DividendOut(BaseModel):
    id: int
    asset_id: int
    ticker: str | None = None
    asset_name: str | None = None
    payment_date: date
    record_date: date | None
    amount_per_unit: Decimal
    total_amount: Decimal
    dividend_type: DividendType
    is_taxable: bool
    tax_withheld: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}


class DividendSummaryOut(BaseModel):
    total_received: Decimal
    by_asset: dict[str, Decimal]        # ticker → total
    by_month: dict[str, Decimal]        # YYYY-MM → total (últimos 12 meses)
    average_monthly: Decimal
    biggest_payer_ticker: str | None
    biggest_payer_total: Decimal | None
