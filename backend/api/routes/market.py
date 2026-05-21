"""
Endpoints de dados de mercado — cotações e histórico de preços.

Usa MarketDataService (singleton por processo) com cache interno de 5 min.

Rotas:
    GET /market/quote/{ticker}             - cotação atual
    GET /market/history/{ticker}?period=   - histórico (1m, 3m, 6m, 1y, 5y)
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from backend.api.schemas.market import (
    BenchmarkComparisonOut,
    FundamentalsOut,
    NormalizedPoint,
    PriceHistoryOut,
    PricePointOut,
    QuoteOut,
)
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


@router.get("/fundamentals/{ticker}", response_model=FundamentalsOut)
async def get_fundamentals(ticker: str):
    """
    Retorna indicadores fundamentalistas de um ativo via yfinance.

    Indicadores retornados: P/L, P/VP, Dividend Yield, ROE, Margem Líquida, EV/EBITDA.
    DY, ROE e Margem Líquida já vêm em % (multiplicados por 100).
    Campos não disponíveis para o ativo retornam null.
    """
    yf_ticker = _market_service.to_yfinance_ticker(ticker.upper())
    try:
        data = await _market_service.get_fundamentals(yf_ticker)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Não foi possível obter fundamentalistas para '{ticker}': {exc}",
        )
    return FundamentalsOut(
        ticker=ticker.upper(),
        pe_ratio=data.get("pe_ratio"),
        pb_ratio=data.get("pb_ratio"),
        dividend_yield=data.get("dividend_yield"),
        roe=data.get("roe"),
        net_margin=data.get("net_margin"),
        ev_ebitda=data.get("ev_ebitda"),
        fetched_at=data["fetched_at"],
    )


# Mapeamento de período → dias de lookback (reusa _PERIOD_DAYS + 3y)
_BENCHMARK_PERIOD_DAYS: dict[str, int] = {
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "3y": 1095,
}


@router.get("/benchmark-comparison", response_model=BenchmarkComparisonOut)
async def get_benchmark_comparison(
    ticker: Annotated[str, Query(description="Ticker do ativo (ex: PETR4)")],
    period: Annotated[
        Literal["1m", "3m", "6m", "1y", "3y"],
        Query(description="Período: 1m, 3m, 6m, 1y, 3y"),
    ] = "1y",
):
    """
    Compara o retorno de um ativo com CDI, IBOVESPA e IPCA no período.

    Retorna retornos percentuais simples e série histórica normalizada em base 100
    para plotar todas as linhas no mesmo gráfico de comparação.
    """
    days = _BENCHMARK_PERIOD_DAYS[period]
    start_date = date.today() - timedelta(days=days)
    end_date = date.today()
    yf_ticker = _market_service.to_yfinance_ticker(ticker.upper())

    # Busca histórico do ativo
    try:
        asset_hist = await _market_service.get_price_history(yf_ticker, start_date)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sem dados para '{ticker}': {exc}",
        )

    if not asset_hist.prices:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sem histórico de preços para '{ticker}' no período {period}.",
        )

    # Retorno do ativo
    def _pct_return(prices: list) -> Decimal:
        if len(prices) < 2 or prices[0] == 0:
            return Decimal("0.00")
        return ((prices[-1] / prices[0] - 1) * 100).quantize(Decimal("0.01"))

    asset_return = _pct_return(asset_hist.prices)

    # Retornos dos benchmarks (falha silenciosa → 0.00)
    try:
        ibov_hist = await _market_service.get_price_history("^BVSP", start_date)
        ibov_return = _pct_return(ibov_hist.prices)
    except Exception:
        ibov_hist = None
        ibov_return = Decimal("0.00")

    try:
        cdi_return = await _market_service._get_macro_benchmark_return("CDI", start_date, end_date)
    except Exception:
        cdi_return = Decimal("0.00")

    try:
        ipca_return = await _market_service._get_macro_benchmark_return("IPCA", start_date, end_date)
    except Exception:
        ipca_return = Decimal("0.00")

    alpha_cdi = (asset_return - cdi_return).quantize(Decimal("0.01"))
    alpha_ibov = (asset_return - ibov_return).quantize(Decimal("0.01"))

    # Série normalizada em base 100
    # Para o ativo usamos as datas reais do histórico como eixo X
    asset_dates = asset_hist.dates
    asset_prices = asset_hist.prices
    base_asset = asset_prices[0] if asset_prices else Decimal("1")

    # Monta lookup {date: normalized_price} para IBOV
    ibov_map: dict = {}
    if ibov_hist and ibov_hist.prices:
        base_ibov = ibov_hist.prices[0]
        for d, p in zip(ibov_hist.dates, ibov_hist.prices):
            ibov_map[d] = ((p / base_ibov) * 100).quantize(Decimal("0.01"))

    # CDI e IPCA são retornos acumulados lineares distribuídos pelo período
    n_days = len(asset_dates)
    chart_data: list[NormalizedPoint] = []
    for i, (d, p) in enumerate(zip(asset_dates, asset_prices)):
        frac = i / max(n_days - 1, 1)
        asset_norm = ((p / base_asset) * 100).quantize(Decimal("0.01"))

        # Interpolação linear dos benchmarks macro para cada ponto do eixo X
        cdi_norm = (100 + cdi_return * Decimal(str(frac))).quantize(Decimal("0.01"))
        ipca_norm = (100 + ipca_return * Decimal(str(frac))).quantize(Decimal("0.01"))
        ibov_norm = ibov_map.get(d)

        chart_data.append(NormalizedPoint(
            date=d,
            asset=asset_norm,
            cdi=cdi_norm,
            ibov=ibov_norm,
            ipca=ipca_norm,
        ))

    actual_start = asset_dates[0] if asset_dates else start_date
    actual_end = asset_dates[-1] if asset_dates else end_date

    return BenchmarkComparisonOut(
        ticker=ticker.upper(),
        period=period,
        period_start=actual_start,
        period_end=actual_end,
        asset_return=asset_return,
        cdi_return=cdi_return,
        ibov_return=ibov_return,
        ipca_return=ipca_return,
        alpha_cdi=alpha_cdi,
        alpha_ibov=alpha_ibov,
        chart_data=chart_data,
    )
