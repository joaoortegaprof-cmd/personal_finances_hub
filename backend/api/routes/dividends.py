"""
Endpoints de histórico de proventos recebidos.

Rotas:
    GET  /dividends              — lista com filtro de período
    POST /dividends              — registrar provento recebido
    GET  /dividends/summary      — resumo por ativo e por mês
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.dividends import DividendCreate, DividendOut, DividendSummaryOut
from backend.core.database import get_db
from backend.models.asset import Asset
from backend.models.dividend import Dividend

router = APIRouter(prefix="/dividends", tags=["Proventos"])


def _to_out(d: Dividend) -> DividendOut:
    ticker = None
    asset_name = None
    if d.asset is not None:
        ticker = d.asset.ticker
        asset_name = d.asset.name
    return DividendOut(
        id=d.id,
        asset_id=d.asset_id,
        ticker=ticker,
        asset_name=asset_name,
        payment_date=d.payment_date,
        record_date=d.record_date,
        amount_per_unit=d.amount_per_unit,
        total_amount=d.total_amount,
        dividend_type=d.dividend_type,
        is_taxable=d.is_taxable,
        tax_withheld=d.tax_withheld,
        created_at=d.created_at,
    )


@router.get("/summary", response_model=DividendSummaryOut)
async def get_dividends_summary(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Resumo de proventos recebidos no período (padrão: últimos 12 meses).

    Retorna total, distribuição por ativo, série mensal e média mensal.
    """
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=365)

    stmt = (
        select(Dividend)
        .where(Dividend.payment_date >= start_date)
        .where(Dividend.payment_date <= end_date)
        .order_by(Dividend.payment_date)
    )
    result = await db.execute(stmt)
    dividends = result.scalars().all()

    total_received = Decimal("0")
    by_asset: dict[str, Decimal] = defaultdict(Decimal)
    by_month: dict[str, Decimal] = defaultdict(Decimal)

    for d in dividends:
        total_received += d.total_amount
        ticker = (d.asset.ticker if d.asset and d.asset.ticker else str(d.asset_id))
        by_asset[ticker] += d.total_amount
        month_key = d.payment_date.strftime("%Y-%m")
        by_month[month_key] += d.total_amount

    # Preenche meses sem provento com zero para os últimos 12 meses
    full_months: dict[str, Decimal] = {}
    cursor = start_date.replace(day=1)
    end_month = end_date.replace(day=1)
    while cursor <= end_month:
        key = cursor.strftime("%Y-%m")
        full_months[key] = by_month.get(key, Decimal("0"))
        # Avança para o próximo mês
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)

    n_months = len(full_months) or 1
    average_monthly = (total_received / n_months).quantize(Decimal("0.01"))

    biggest_ticker = max(by_asset, key=lambda k: by_asset[k]) if by_asset else None
    biggest_total = by_asset[biggest_ticker] if biggest_ticker else None

    return DividendSummaryOut(
        total_received=total_received,
        by_asset=dict(sorted(by_asset.items(), key=lambda x: x[1], reverse=True)),
        by_month=full_months,
        average_monthly=average_monthly,
        biggest_payer_ticker=biggest_ticker,
        biggest_payer_total=biggest_total,
    )


@router.get("/total/{asset_id}")
async def get_dividends_total_for_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Retorna total de proventos recebidos e yield on cost para um ativo."""
    from decimal import Decimal
    from sqlalchemy import func, select as _sel
    from backend.models.dividend import Dividend
    from backend.repositories.asset_repository import AssetRepository

    result = await db.execute(
        _sel(func.coalesce(func.sum(Dividend.total_amount), Decimal("0")))
        .where(Dividend.asset_id == asset_id)
    )
    total_dividends = result.scalar() or Decimal("0")

    repo = AssetRepository(db)
    qty = await repo.get_consolidated_position(asset_id)
    avg = await repo.calculate_avg_price(asset_id)
    cost = qty * avg

    yoc = float(total_dividends / cost * 100) if cost > 0 else 0.0

    return {
        "asset_id":        asset_id,
        "total_dividends": float(total_dividends),
        "yield_on_cost":   yoc,
    }


@router.get("", response_model=list[DividendOut])
async def list_dividends(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Lista proventos registrados, opcionalmente filtrados por período."""
    stmt = select(Dividend).order_by(Dividend.payment_date.desc())
    if start_date:
        stmt = stmt.where(Dividend.payment_date >= start_date)
    if end_date:
        stmt = stmt.where(Dividend.payment_date <= end_date)

    result = await db.execute(stmt)
    return [_to_out(d) for d in result.scalars().all()]


@router.post("/import/{asset_id}")
async def import_dividends_from_market(
    asset_id: int,
    years: int = Query(2, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
):
    """
    Importa o histórico de dividendos de um ativo via yfinance.

    Para cada evento encontrado:
      - Verifica se já existe um Dividend com payment_date == ex_date (deduplicação)
      - Calcula total_amount = amount_per_unit × posição consolidada na data
      - Cria o registro Dividend se não existir

    Retorna: {'imported': N, 'skipped': N, 'errors': [...]}.
    """
    from backend.models.asset import AssetPosition, OperationType
    from backend.services.market_data_service import MarketDataService
    from decimal import Decimal as D
    from sqlalchemy import case, func

    asset_result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = asset_result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Ativo {asset_id} não encontrado.")
    if not asset.ticker:
        raise HTTPException(status_code=422, detail="Ativo não possui ticker — impossível buscar dividendos.")

    svc      = MarketDataService()
    history  = await svc.get_dividend_history(asset.ticker, years=years)

    imported = 0
    skipped  = 0
    errors: list[str] = []

    for item in history:
        ex_date_str    = item["ex_date"]
        amount_per_unit = D(str(item["amount_per_unit"]))
        try:
            ex_date = date.fromisoformat(ex_date_str)
        except ValueError:
            errors.append(f"Data inválida: {ex_date_str}")
            continue

        # Deduplicação: já existe dividend para este ativo nesta data?
        existing = await db.execute(
            select(Dividend).where(
                Dividend.asset_id   == asset_id,
                Dividend.payment_date == ex_date,
            )
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        # Posição consolidada na data do provento
        qty_result = await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            ((AssetPosition.operation_type == OperationType.BUY) & (AssetPosition.quantity > 0), AssetPosition.quantity),
                            ((AssetPosition.operation_type == OperationType.SELL) & (AssetPosition.quantity < 0), AssetPosition.quantity),
                            else_=0,
                        )
                    ),
                    D("0"),
                )
            ).where(
                AssetPosition.asset_id      == asset_id,
                AssetPosition.operation_date <= ex_date,
            )
        )
        qty = qty_result.scalar() or D("0")
        total = (amount_per_unit * qty).quantize(D("0.01"))

        try:
            div = Dividend(
                asset_id        = asset_id,
                payment_date    = ex_date,
                amount_per_unit = amount_per_unit,
                total_amount    = total if total > 0 else amount_per_unit,
            )
            db.add(div)
            await db.flush()
            imported += 1
        except Exception as exc:
            errors.append(f"{ex_date_str}: {exc}")

    return {"imported": imported, "skipped": skipped, "errors": errors}


@router.post("", response_model=DividendOut, status_code=status.HTTP_201_CREATED)
async def create_dividend(
    payload: DividendCreate,
    db: AsyncSession = Depends(get_db),
):
    """Registra um provento recebido."""
    # Verifica se o ativo existe
    asset_result = await db.execute(select(Asset).where(Asset.id == payload.asset_id))
    asset = asset_result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ativo {payload.asset_id} não encontrado.",
        )

    dividend = Dividend(**payload.model_dump())
    db.add(dividend)
    await db.flush()
    await db.refresh(dividend)
    return _to_out(dividend)
