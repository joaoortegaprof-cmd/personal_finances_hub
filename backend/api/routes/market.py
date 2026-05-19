"""
Endpoints de dados de mercado — cotações e histórico de preços.

Usa MarketDataService (singleton por processo) com cache interno de 5 min.

Rotas:
    GET /market/quote/{ticker}             - cotação atual
    GET /market/history/{ticker}?period=   - histórico (1m, 3m, 6m, 1y, 5y)
"""

from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from backend.api.schemas.market import PriceHistoryOut, PricePointOut, QuoteOut
from backend.services.market_data_service import MarketDataService

router = APIRouter(prefix="/market", tags=["Mercado"])

# Singleton compartilhado entre requisições — maximiza hits de cache
_market_service = MarketDataService()

# Mapeamento de período legível → dias de lookback
_PERIOD_DAYS: dict[str, int] = {
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "5y": 1825,
}


@router.get("/quote/{ticker}", response_model=QuoteOut)
async def get_quote(ticker: str):
    """
    Retorna a cotação atual de um ativo.

    O ticker deve estar no formato do yfinance. Para ativos da B3, informe
    apenas o código (ex: PETR4) — o serviço adiciona o sufixo ".SA"
    automaticamente. Para índices use "^BVSP", "^GSPC" etc.
    """
    yf_ticker = _market_service.to_yfinance_ticker(ticker.upper())
    try:
        quote = await _market_service.get_quote(yf_ticker)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Não foi possível obter cotação para '{ticker}': {exc}",
        )
    return QuoteOut(
        ticker=ticker.upper(),
        price=quote.price,
        currency=quote.currency,
        change_pct=quote.change_pct,
        volume=quote.volume,
        fetched_at=quote.fetched_at,
    )


@router.get("/history/{ticker}", response_model=PriceHistoryOut)
async def get_history(
    ticker: str,
    period: Annotated[
        Literal["1m", "3m", "6m", "1y", "5y"],
        Query(description="Período de histórico: 1m, 3m, 6m, 1y ou 5y"),
    ] = "6m",
    interval: Annotated[
        Literal["1d", "1wk", "1mo"],
        Query(description="Intervalo das velas: 1d (diário), 1wk (semanal), 1mo (mensal)"),
    ] = "1d",
):
    """
    Retorna a série histórica de preços de fechamento ajustados.

    auto_adjust=True no yfinance já corrige splits e dividendos —
    o retorno calculado a partir desses dados é comparável ao longo do tempo.
    """
    days = _PERIOD_DAYS[period]
    start_date = date.today() - timedelta(days=days)
    yf_ticker = _market_service.to_yfinance_ticker(ticker.upper())

    try:
        history = await _market_service.get_price_history(
            yf_ticker, start_date, interval=interval
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Não foi possível obter histórico para '{ticker}': {exc}",
        )

    points = [
        PricePointOut(date=d, price=p)
        for d, p in zip(history.dates, history.prices)
    ]
    return PriceHistoryOut(
        ticker=ticker.upper(),
        currency=history.currency,
        interval=interval,
        points=points,
    )
