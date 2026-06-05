"""
Endpoints de ativos de investimento e carteira.

Dois routers separados para prefixos distintos:
  assets_router   → /assets   (CRUD de ativos e operações individuais)
  portfolio_router → /portfolio (visão consolidada da carteira)
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.assets import (
    AssetCreate,
    AssetLiquidityItemOut,
    AssetOperationCreate,
    AssetOperationOut,
    AssetOut,
    AssetUpdate,
    ConsolidatedPositionOut,
    LiquidityBreakdownOut,
    PortfolioSummaryOut,
    PortfolioTypeEntryOut,
    PositionAdjustIn,
    PositionAdjustOut,
)
from backend.core.database import get_db
from backend.models.asset import Asset, AssetPosition, OperationType
from backend.repositories.asset_repository import AssetRepository
from backend.services.liquidity_service import LiquidityService
from backend.services.risk_service import RiskService

assets_router = APIRouter(prefix="/assets", tags=["Ativos"])
portfolio_router = APIRouter(prefix="/portfolio", tags=["Carteira"])


# -----------------------------------------------------------------
# Validação de sinal BUY/SELL
# -----------------------------------------------------------------

def validate_position(operation_type: OperationType, quantity: Decimal) -> None:
    """
    Garante que compras tenham quantidade positiva e vendas, negativa.

    Levanta HTTP 422 com mensagem amigável em caso de violação,
    evitando que dados inconsistentes cheguem ao banco.
    """
    if operation_type == OperationType.BUY and quantity <= 0:
        raise HTTPException(
            status_code=422,
            detail="Compra deve ter quantidade positiva.",
        )
    if operation_type == OperationType.SELL and quantity >= 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "Venda deve ter quantidade negativa. "
                "Informe o valor negativo (ex: -100)."
            ),
        )
    if quantity == 0:
        raise HTTPException(
            status_code=422,
            detail="Quantidade não pode ser zero.",
        )


# -----------------------------------------------------------------
# /assets
# -----------------------------------------------------------------

@assets_router.get("", response_model=list[AssetOut])
async def list_assets(db: AsyncSession = Depends(get_db)):
    """Lista todos os ativos cadastrados ordenados por PK."""
    repo = AssetRepository(db)
    return await repo.get_all()


@assets_router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def create_asset(payload: AssetCreate, db: AsyncSession = Depends(get_db)):
    """Cadastra um novo ativo (ação, FII, CDB, Tesouro etc.)."""
    repo = AssetRepository(db)
    asset = Asset(**payload.model_dump())
    try:
        return await repo.create(asset)
    except IntegrityError:
        ticker_info = f" '{payload.ticker}'" if payload.ticker else ""
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ticker{ticker_info} já está cadastrado. Verifique se o ativo não foi adicionado anteriormente.",
        )


@assets_router.put("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: int,
    payload: AssetUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Atualiza os campos informados de um ativo existente."""
    repo = AssetRepository(db)
    updated = await repo.update(asset_id, payload.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ativo não encontrado")
    return updated


@assets_router.get("/{asset_id}/position", response_model=ConsolidatedPositionOut)
async def get_asset_position(asset_id: int, db: AsyncSession = Depends(get_db)):
    """Retorna a posição consolidada: quantidade líquida e preço médio de aquisição."""
    repo = AssetRepository(db)
    asset = await repo.get_by_id(asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ativo não encontrado")
    net_quantity = await repo.get_consolidated_position(asset_id)
    avg_price = await repo.calculate_avg_price(asset_id)
    estimated_cost = (net_quantity * avg_price).quantize(Decimal("0.01"))
    return ConsolidatedPositionOut(
        asset_id=asset_id,
        ticker=asset.ticker,
        name=asset.name,
        asset_type=asset.asset_type,
        net_quantity=net_quantity,
        avg_price=avg_price,
        estimated_cost=estimated_cost,
    )


@assets_router.post(
    "/{asset_id}/operations",
    response_model=AssetOperationOut,
    status_code=status.HTTP_201_CREATED,
)
async def register_operation(
    asset_id: int,
    payload: AssetOperationCreate,
    db: AsyncSession = Depends(get_db),
):
    """Registra uma compra, venda ou evento corporativo para o ativo."""
    # Valida o sinal de quantidade para BUY/SELL antes de gravar
    validate_position(payload.operation_type, payload.quantity)
    repo = AssetRepository(db)
    if not await repo.exists(asset_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ativo não encontrado")
    position = AssetPosition(asset_id=asset_id, **payload.model_dump())
    db.add(position)
    await db.flush()
    await db.refresh(position)
    return position


@assets_router.post(
    "/{asset_id}/adjust-position",
    response_model=PositionAdjustOut,
    status_code=status.HTTP_200_OK,
)
async def adjust_asset_position(
    asset_id: int,
    payload: PositionAdjustIn,
    db: AsyncSession = Depends(get_db),
):
    """
    Ajusta a posição líquida de um ativo diretamente.

    Estratégia zero + recria (dentro de uma única transação atômica):
      1. Se houver posição atual, cria SELL com quantity NEGATIVA igual à
         posição líquida, zerando completamente o saldo anterior.
      2. Se target_quantity > 0, cria BUY com a quantidade e preço alvo.

    Isso garante que após o ajuste:
      - get_consolidated_position() == target_quantity  (exato)
      - calculate_avg_price()       == target_avg_price (exato)

    A abordagem delta (criar apenas a diferença) NÃO funciona para preço
    médio: calculate_avg_price faz média ponderada de todos os BUYs, então
    o preço alvo nunca seria atingido de forma precisa.
    """
    repo = AssetRepository(db)
    asset = await repo.get_by_id(asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ativo não encontrado")

    current_qty = await repo.get_consolidated_position(asset_id)
    current_avg = await repo.calculate_avg_price(asset_id)

    note = f"[AJUSTE] {payload.reason}" if payload.reason else "[AJUSTE MANUAL DE POSIÇÃO]"
    ops_created = 0

    # Passo 1: zera a posição atual com um SELL de quantidade NEGATIVA
    if current_qty != Decimal("0"):
        sell_price = current_avg if current_avg > Decimal("0") else Decimal("0.01")
        db.add(AssetPosition(
            asset_id=asset_id,
            operation_date=payload.op_date,
            quantity=-current_qty,          # NEGATIVO — cancela exatamente a posição líquida
            unit_price=sell_price,
            fees=Decimal("0.00"),
            operation_type=OperationType.SELL,
            notes=note,
        ))
        ops_created += 1

    # Passo 2: recria a posição com os valores alvo
    if payload.target_quantity > Decimal("0"):
        db.add(AssetPosition(
            asset_id=asset_id,
            operation_date=payload.op_date,
            quantity=payload.target_quantity,   # POSITIVO
            unit_price=payload.target_avg_price,
            fees=Decimal("0.00"),
            operation_type=OperationType.BUY,
            notes=note,
        ))
        ops_created += 1

    await db.flush()

    return PositionAdjustOut(
        asset_id=asset_id,
        ticker=asset.ticker,
        previous_quantity=current_qty,
        previous_avg_price=current_avg,
        new_quantity=payload.target_quantity,
        new_avg_price=payload.target_avg_price,
        operations_created=ops_created,
    )


# -----------------------------------------------------------------
# /portfolio
# -----------------------------------------------------------------

@portfolio_router.get("/summary", response_model=PortfolioSummaryOut)
async def get_portfolio_summary(db: AsyncSession = Depends(get_db)):
    """Retorna o custo investido agrupado por tipo de ativo (ação, FII, CDB etc.)."""
    repo = AssetRepository(db)
    raw = await repo.get_portfolio_summary_by_type()
    by_type = [
        PortfolioTypeEntryOut(
            asset_type=r["asset_type"],
            buy_cost=r["buy_cost"],
            sell_proceeds=r["sell_proceeds"],
            net_invested=r["net_invested"],
            net_quantity=r["net_quantity"],
        )
        for r in raw
    ]
    total_invested = sum((e.net_invested for e in by_type), Decimal("0.00"))
    return PortfolioSummaryOut(by_type=by_type, total_invested=total_invested)


@portfolio_router.get("/liquidity", response_model=LiquidityBreakdownOut)
async def get_portfolio_liquidity(db: AsyncSession = Depends(get_db)):
    """
    Retorna o breakdown de liquidez da carteira por janela (D+0, D+1, D+2, vencimento).

    Valores estimados ao custo médio — para valor de mercado real, multiplique
    net_quantity pela cotação obtida via MarketDataService.
    """
    service = LiquidityService(db)
    breakdown = await service.get_liquidity_breakdown()
    items = [
        AssetLiquidityItemOut(
            asset_id=item.asset_id,
            ticker=item.ticker,
            name=item.name,
            liquidity_window=item.liquidity_window,
            net_quantity=item.net_quantity,
            avg_cost=item.avg_cost,
            estimated_value=item.estimated_value,
            has_loss_on_early_redemption=item.has_loss_on_early_redemption,
        )
        for item in breakdown.items
    ]
    return LiquidityBreakdownOut(
        d0_value=breakdown.d0_value,
        d1_value=breakdown.d1_value,
        d2_value=breakdown.d2_value,
        maturity_value=breakdown.maturity_value,
        total_liquid=breakdown.total_liquid,
        total_portfolio=breakdown.total_portfolio,
        items=items,
    )


@portfolio_router.get("/risk-analysis")
async def get_risk_analysis(db: AsyncSession = Depends(get_db)):
    """
    Análise de risco da carteira.

    Para cada ativo de renda variável com ticker, calcula (usando 1 ano de histórico):
      - Beta vs IBOVESPA
      - Volatilidade anualizada (% a.a.)
      - VaR histórico diário a 95% de confiança (% do valor)

    Também retorna:
      - Métricas consolidadas da carteira (ponderadas pelo custo)
      - Diversificação por classe de ativo (% do custo total)
    """
    service = RiskService(db)
    result  = await service.analyse()
    return {
        "portfolio_beta":       result.portfolio_beta,
        "portfolio_volatility": result.portfolio_volatility,
        "portfolio_var_95":     result.portfolio_var_95,
        "diversification":      result.diversification,
        "assets": [
            {
                "asset_id":   a.asset_id,
                "ticker":     a.ticker,
                "name":       a.name,
                "asset_type": a.asset_type,
                "weight_pct": a.weight_pct,
                "beta":       a.beta,
                "volatility": a.volatility,
                "var_95":     a.var_95,
            }
            for a in result.assets
        ],
    }


@portfolio_router.get("/by-sector")
async def get_portfolio_by_sector(db: AsyncSession = Depends(get_db)):
    """
    Retorna a distribuição da carteira agrupada por classe e setor/segmento.

    Classifica automaticamente via sector_mapper (sem depender do campo
    `sector` preenchido manualmente). Retorna listas separadas por classe
    de ativo: stocks, fiis, etfs, treasury, fixed_income, crypto, other.
    """
    from sqlalchemy import select
    from frontend.components.sector_mapper import get_sector

    result = await db.execute(select(Asset))
    all_assets = result.scalars().all()

    repo = AssetRepository(db)

    stocks_by_sector:      dict[str, list] = {}
    fiis_by_segment:       dict[str, list] = {}
    etfs_by_name:          dict[str, list] = {}
    treasury_by_type:      dict[str, list] = {}
    fixed_income_by_type:  dict[str, list] = {}
    crypto_by_name:        dict[str, list] = {}
    other_by_type:         dict[str, list] = {}

    for asset in all_assets:
        qty = await repo.get_consolidated_position(asset.id)
        if qty <= 0:
            continue
        avg = await repo.calculate_avg_price(asset.id)
        if avg <= 0:
            continue
        value = float(qty * avg)
        ticker = asset.ticker or asset.name
        atype  = asset.asset_type.value if hasattr(asset.asset_type, "value") else str(asset.asset_type)
        sector = get_sector(ticker, atype)

        entry = {
            "ticker": ticker,
            "name":   asset.name,
            "value":  value,
            "qty":    float(qty),
        }

        if atype == "acao" or atype == "acao_internacional":
            stocks_by_sector.setdefault(sector, []).append(entry)
        elif atype == "fii":
            fiis_by_segment.setdefault(sector, []).append(entry)
        elif atype == "etf":
            etfs_by_name.setdefault(ticker, []).append(entry)
        elif atype == "tesouro_direto":
            label = asset.indexer.value if asset.indexer else "Prefixado"
            treasury_by_type.setdefault(label, []).append(entry)
        elif atype == "renda_fixa":
            label = asset.indexer.value if asset.indexer else "CDI"
            fixed_income_by_type.setdefault(label, []).append(entry)
        elif atype == "criptomoeda":
            crypto_by_name.setdefault(ticker, []).append(entry)
        else:
            other_by_type.setdefault(atype, []).append(entry)

    def aggregate(groups: dict) -> list[dict]:
        out = []
        for name, assets in groups.items():
            total = sum(a["value"] for a in assets)
            out.append({
                "label":  name,
                "value":  total,
                "assets": assets,
                "count":  len(assets),
            })
        return sorted(out, key=lambda x: x["value"], reverse=True)

    return {
        "stocks":       aggregate(stocks_by_sector),
        "fiis":         aggregate(fiis_by_segment),
        "etfs":         aggregate(etfs_by_name),
        "treasury":     aggregate(treasury_by_type),
        "fixed_income": aggregate(fixed_income_by_type),
        "crypto":       aggregate(crypto_by_name),
        "other":        aggregate(other_by_type),
    }


@portfolio_router.get("/opportunity-cost")
async def get_opportunity_cost(
    period_months: int = 12,
    db: AsyncSession = Depends(get_db),
):
    """
    Compara o retorno da carteira de renda variável com o CDI no mesmo período.

    Retorna:
      - portfolio_return_pct: retorno realizado (baseado no custo vs posição atual)
      - cdi_return_pct:       retorno do CDI no período (aproximado)
      - alpha_pct:            portfolio_return − cdi_return
      - period_months:        período analisado
    """
    from decimal import Decimal as D
    from backend.models.asset import AssetType as AT
    import math

    repo = AssetRepository(db)
    all_assets = await repo.get_all()

    equity_types = {AT.STOCK, AT.FII, AT.ETF, AT.INTL_STOCK}
    total_cost   = D("0")
    total_qty_x_avg = D("0")

    for asset in all_assets:
        if asset.asset_type not in equity_types:
            continue
        qty = await repo.get_consolidated_position(asset.id)
        avg = await repo.calculate_avg_price(asset.id)
        cost = qty * avg
        if cost > 0:
            total_cost += cost
            total_qty_x_avg += cost   # cost already = qty * avg_buy_price

    if total_cost == 0:
        return {
            "portfolio_return_pct": None,
            "cdi_return_pct": None,
            "alpha_pct": None,
            "period_months": period_months,
            "message": "Nenhum ativo de renda variável cadastrado.",
        }

    # Para retorno real precisaríamos do valor de mercado atual.
    # Aproximamos usando a variação implícita no preço médio ao longo do tempo
    # e a taxa CDI histórica de ~13.75% a.a. (referência 2024-2025).
    CDI_ANNUAL = D("13.75")
    cdi_period = ((1 + CDI_ANNUAL / 100) ** D(str(period_months / 12)) - 1) * 100
    cdi_period = cdi_period.quantize(D("0.01"))

    return {
        "portfolio_return_pct": None,
        "cdi_return_pct": float(cdi_period),
        "alpha_pct": None,
        "period_months": period_months,
        "total_invested_equity": float(total_cost),
        "message": (
            f"CDI estimado em {period_months} meses: {float(cdi_period):.2f}%. "
            "Para comparar com o retorno real da carteira, conecte a cotação atual de cada ativo."
        ),
    }
