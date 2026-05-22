"""
Serviço de análise de risco da carteira de investimentos.

Calcula, por ativo (renda variável com ticker) e para a carteira consolidada:
  - Beta vs IBOVESPA (^BVSP)
  - Volatilidade anualizada (desvio padrão dos retornos diários × √252)
  - VaR histórico diário a 95% de confiança (em % do valor)
  - Diversificação por classe de ativo (peso em % do valor estimado)

Lógica:
  1. Busca posições consolidadas do repositório de ativos
  2. Para cada ticker de renda variável, busca 1 ano de histórico via yfinance
  3. Computa retornos diários logarítmicos
  4. Calcula métricas individuais e consolida em nível de carteira
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.asset import AssetType
from backend.repositories.asset_repository import AssetRepository
from backend.services.market_data_service import MarketDataService

_EQUITY_TYPES = {AssetType.STOCK, AssetType.FII, AssetType.ETF, AssetType.INTL_STOCK}
_IBOV = "^BVSP"
_LOOKBACK_DAYS = 365


@dataclass
class AssetRisk:
    asset_id:   int
    ticker:     str
    name:       str
    asset_type: str
    weight_pct: float          # % do valor total da carteira
    beta:       float | None   # vs IBOVESPA; None se histórico insuficiente
    volatility: float | None   # % a.a.
    var_95:     float | None   # % diário (perda máxima esperada 95% conf)


@dataclass
class PortfolioRisk:
    assets:               list[AssetRisk] = field(default_factory=list)
    portfolio_beta:       float | None = None  # média ponderada
    portfolio_volatility: float | None = None  # média ponderada (simplificado)
    portfolio_var_95:     float | None = None  # média ponderada
    diversification:      list[dict] = field(default_factory=list)  # [{type, pct}]


class RiskService:

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo    = AssetRepository(session)
        self._market  = MarketDataService()

    async def analyse(self) -> PortfolioRisk:
        all_assets = await self._repo.get_all()
        if not all_assets:
            return PortfolioRisk()

        # Build (asset, net_qty, avg_cost, estimated_cost) tuples; skip zero positions
        position_data: list[tuple] = []
        for asset in all_assets:
            net_qty  = await self._repo.get_consolidated_position(asset.id)
            avg_cost = await self._repo.calculate_avg_price(asset.id)
            est_cost = (net_qty * avg_cost).quantize(Decimal("0.01"))
            if est_cost > 0:
                position_data.append((asset, net_qty, avg_cost, est_cost))

        if not position_data:
            return PortfolioRisk()

        total_cost = sum(float(d[3]) for d in position_data) or 1.0

        # Histórico de mercado (benchmark)
        ibov_returns = await self._get_returns(_IBOV)

        # Diversificação por tipo de ativo
        type_totals: dict[str, float] = {}
        for asset, _, _, est_cost in position_data:
            t = asset.asset_type.value
            type_totals[t] = type_totals.get(t, 0.0) + float(est_cost)
        diversification = [
            {"type": t, "pct": round(v / total_cost * 100, 2)}
            for t, v in sorted(type_totals.items(), key=lambda x: -x[1])
        ]

        assets: list[AssetRisk] = []
        for asset, _, _, est_cost in position_data:
            weight = float(est_cost) / total_cost * 100

            if asset.asset_type not in _EQUITY_TYPES or not asset.ticker:
                assets.append(AssetRisk(
                    asset_id=asset.id,
                    ticker=asset.ticker or "—",
                    name=asset.name,
                    asset_type=asset.asset_type.value,
                    weight_pct=round(weight, 2),
                    beta=None,
                    volatility=None,
                    var_95=None,
                ))
                continue

            yf_ticker = self._market.to_yfinance_ticker(asset.ticker)
            asset_returns = await self._get_returns(yf_ticker)

            beta, vol, var95 = _calc_metrics(asset_returns, ibov_returns)
            assets.append(AssetRisk(
                asset_id=asset.id,
                ticker=asset.ticker,
                name=asset.name,
                asset_type=asset.asset_type.value,
                weight_pct=round(weight, 2),
                beta=beta,
                volatility=vol,
                var_95=var95,
            ))

        # Métricas de carteira (ponderadas pelo custo)
        portfolio_beta = _weighted_avg([a.beta       for a in assets], [a.weight_pct for a in assets])
        portfolio_vol  = _weighted_avg([a.volatility  for a in assets], [a.weight_pct for a in assets])
        portfolio_var  = _weighted_avg([a.var_95      for a in assets], [a.weight_pct for a in assets])

        return PortfolioRisk(
            assets=assets,
            portfolio_beta=portfolio_beta,
            portfolio_volatility=portfolio_vol,
            portfolio_var_95=portfolio_var,
            diversification=diversification,
        )

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    async def _get_returns(self, ticker: str) -> list[float]:
        """Retorna lista de retornos diários logarítmicos para os últimos 365 dias."""
        start = date.today() - timedelta(days=_LOOKBACK_DAYS)
        try:
            history = await self._market.get_price_history(ticker, start, interval="1d")
            prices = [float(p) for p in history.prices if p and float(p) > 0]
            if len(prices) < 10:
                return []
            return [
                math.log(prices[i] / prices[i - 1])
                for i in range(1, len(prices))
            ]
        except Exception:
            return []


def _calc_metrics(
    returns: list[float],
    market_returns: list[float],
) -> tuple[float | None, float | None, float | None]:
    """Calcula Beta, Volatilidade anualizada (%) e VaR 95% diário (%)."""
    n = min(len(returns), len(market_returns))
    if n < 10:
        return None, None, None

    r = returns[-n:]
    m = market_returns[-n:]

    mean_r = sum(r) / n
    mean_m = sum(m) / n

    cov = sum((r[i] - mean_r) * (m[i] - mean_m) for i in range(n)) / (n - 1)
    var_m = sum((m[i] - mean_m) ** 2 for i in range(n)) / (n - 1)

    beta = round(cov / var_m, 3) if var_m > 0 else None

    std = math.sqrt(sum((x - mean_r) ** 2 for x in r) / (n - 1))
    volatility = round(std * math.sqrt(252) * 100, 2)

    sorted_r = sorted(r)
    var95_idx = max(0, int(n * 0.05) - 1)
    var_95 = round(abs(sorted_r[var95_idx]) * 100, 3)  # % positivo = perda máxima

    return beta, volatility, var_95


def _weighted_avg(values: list[float | None], weights: list[float]) -> float | None:
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None]
    if not pairs:
        return None
    total_w = sum(w for _, w in pairs)
    if total_w <= 0:
        return None
    return round(sum(v * w for v, w in pairs) / total_w, 3)
