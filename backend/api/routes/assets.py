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
)
from backend.core.database import get_db
from backend.models.asset import Asset, AssetPosition
from backend.repositories.asset_repository import AssetRepository
from backend.services.liquidity_service import LiquidityService

assets_router = APIRouter(prefix="/assets", tags=["Ativos"])
portfolio_router = APIRouter(prefix="/portfolio", tags=["Carteira"])


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
    repo = AssetRepository(db)
    if not await repo.exists(asset_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ativo não encontrado")
    position = AssetPosition(asset_id=asset_id, **payload.model_dump())
    db.add(position)
    await db.flush()
    await db.refresh(position)
    return position


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
            asset_type=asset_type,
            buy_cost=v["buy_cost"],
            sell_proceeds=v["sell_proceeds"],
            net_invested=v["buy_cost"] - v["sell_proceeds"],
            net_quantity=v["net_quantity"],
        )
        for asset_type, v in raw.items()
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
