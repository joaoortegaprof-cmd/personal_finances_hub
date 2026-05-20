"""
Schemas Pydantic para dados de mercado (cotações, histórico e fundamentalistas).
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


class FundamentalsOut(BaseModel):
    ticker: str
    pe_ratio: Decimal | None        # P/L — Preço / Lucro
    pb_ratio: Decimal | None        # P/VP — Preço / Valor Patrimonial
    dividend_yield: Decimal | None  # DY % (já multiplicado por 100)
    roe: Decimal | None             # ROE % (já multiplicado por 100)
    net_margin: Decimal | None      # Margem Líquida % (já multiplicado por 100)
    ev_ebitda: Decimal | None       # EV/EBITDA
    fetched_at: datetime
