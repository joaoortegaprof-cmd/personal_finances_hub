"""
Schemas Pydantic para dados de mercado (cotações e histórico).
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class QuoteOut(BaseModel):
    ticker: str
    price: Decimal
    currency: str
    change_pct: Decimal | None
    volume: int | None
    fetched_at: datetime


class PricePointOut(BaseModel):
    date: date
    price: Decimal


class PriceHistoryOut(BaseModel):
    ticker: str
    currency: str
    interval: str
    points: list[PricePointOut]
