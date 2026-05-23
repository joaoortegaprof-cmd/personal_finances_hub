"""
Testes unitários do LiquidityService.

Cobertura:
  - D+0: saldo de conta corrente aparece em d0_value
  - D+2: ações (PETR4) aparecem em d2_value
  - Vencimento (MATURITY): ativos sem liquidez antecipada
  - total_liquid: soma de d0+d1+d2 (sem maturity)
  - Posição zero: ativo totalmente vendido não aparece no breakdown
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.account import Account, AccountType
from backend.models.asset import Asset, AssetIndexer, AssetPosition, AssetType, LiquidityWindow, OperationType
from backend.services.liquidity_service import LiquidityService


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

async def _create_asset(
    db: AsyncSession,
    ticker: str,
    asset_type: AssetType,
    liquidity: LiquidityWindow,
    indexer: AssetIndexer | None = None,
) -> Asset:
    asset = Asset(
        ticker=ticker,
        name=f"Ativo {ticker}",
        asset_type=asset_type,
        currency="BRL",
        liquidity=liquidity,
        indexer=indexer,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    return asset


async def _buy(db: AsyncSession, asset: Asset, qty: Decimal, price: Decimal) -> None:
    pos = AssetPosition(
        asset_id=asset.id,
        operation_type=OperationType.BUY,
        operation_date=date(2026, 1, 10),
        quantity=qty,
        unit_price=price,
        fees=Decimal("0.00"),
    )
    db.add(pos)
    await db.flush()


async def _sell(db: AsyncSession, asset: Asset, qty: Decimal, price: Decimal) -> None:
    pos = AssetPosition(
        asset_id=asset.id,
        operation_type=OperationType.SELL,
        operation_date=date(2026, 2, 10),
        quantity=-qty,
        unit_price=price,
        fees=Decimal("0.00"),
    )
    db.add(pos)
    await db.flush()


# ─────────────────────────────────────────────────────────────────
# Testes
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_d0_from_account_balance(db_session: AsyncSession, sample_account: Account):
    """
    Conta com R$5.000 de saldo inicial → d0_value deve ser R$5.000.
    Sem investimentos → d1, d2, maturity = 0.
    """
    service = LiquidityService(db_session)
    breakdown = await service.get_liquidity_breakdown()

    assert breakdown.d0_value == Decimal("5000.00")
    assert breakdown.d1_value == Decimal("0.00")
    assert breakdown.d2_value == Decimal("0.00")
    assert breakdown.maturity_value == Decimal("0.00")
    assert breakdown.total_liquid == Decimal("5000.00")


@pytest.mark.asyncio
async def test_d2_from_stock(db_session: AsyncSession, sample_account: Account, sample_asset: Asset):
    """
    Ação PETR4 (D+2): 100 cotas × R$35 = R$3.500 em d2_value.
    """
    await _buy(db_session, sample_asset, Decimal("100"), Decimal("35.00"))

    service = LiquidityService(db_session)
    breakdown = await service.get_liquidity_breakdown()

    assert breakdown.d2_value == Decimal("3500.00")
    assert breakdown.d0_value == Decimal("5000.00")  # conta continua
    assert breakdown.total_liquid == Decimal("8500.00")  # d0 + d2


@pytest.mark.asyncio
async def test_d1_from_treasury_selic(db_session: AsyncSession, sample_account: Account):
    """
    Tesouro Selic (D+1): R$10.000 investidos aparecem em d1_value.
    """
    tesouro = await _create_asset(db_session, "SELIC2027", AssetType.TREASURY, LiquidityWindow.D1,
                                  indexer=AssetIndexer.SELIC)
    await _buy(db_session, tesouro, Decimal("1"), Decimal("10000.00"))

    service = LiquidityService(db_session)
    breakdown = await service.get_liquidity_breakdown()

    assert breakdown.d1_value == Decimal("10000.00")
    assert breakdown.total_liquid == Decimal("15000.00")  # 5000 (d0) + 10000 (d1)


@pytest.mark.asyncio
async def test_maturity_asset_not_in_total_liquid(db_session: AsyncSession, sample_account: Account):
    """
    Ativo com liquidez MATURITY (CDB sem liquidez) NÃO entra em total_liquid.
    Entra apenas em total_portfolio.
    """
    cdb = await _create_asset(db_session, "CDB2028", AssetType.FIXED_INCOME, LiquidityWindow.MATURITY,
                              indexer=AssetIndexer.CDI)
    await _buy(db_session, cdb, Decimal("1"), Decimal("20000.00"))

    service = LiquidityService(db_session)
    breakdown = await service.get_liquidity_breakdown()

    assert breakdown.maturity_value == Decimal("20000.00")
    # maturity NÃO compõe total_liquid
    assert breakdown.total_liquid == Decimal("5000.00")  # só conta
    # mas compõe total_portfolio
    assert breakdown.total_portfolio == Decimal("25000.00")


@pytest.mark.asyncio
async def test_zero_position_not_included(db_session: AsyncSession, sample_account: Account):
    """
    Ativo totalmente vendido (posição líquida = 0) não deve aparecer no breakdown.
    """
    asset = await _create_asset(db_session, "ZERO3", AssetType.STOCK, LiquidityWindow.D2,
                                indexer=None)
    await _buy(db_session, asset, Decimal("100"), Decimal("20.00"))
    await _sell(db_session, asset, Decimal("100"), Decimal("25.00"))

    service = LiquidityService(db_session)
    breakdown = await service.get_liquidity_breakdown()

    # O ativo vendido não deve aparecer nos items
    tickers_in_breakdown = [item.ticker for item in breakdown.items]
    assert "ZERO3" not in tickers_in_breakdown
    # d2 deve ser zero (sem posição)
    assert breakdown.d2_value == Decimal("0.00")


@pytest.mark.asyncio
async def test_negative_account_balance_excluded(db_session: AsyncSession):
    """
    Conta com saldo negativo (cheque especial) não conta como liquidez.
    Saldo negativo representa dívida, não ativo.
    """
    account_neg = Account(
        name="Conta Negativa",
        bank_name="Banco X",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Decimal("-500.00"),   # saldo negativo
        initial_balance_date=date(2026, 1, 1),
    )
    db_session.add(account_neg)
    await db_session.flush()

    service = LiquidityService(db_session)
    breakdown = await service.get_liquidity_breakdown()

    # Conta negativa não entra em d0
    assert breakdown.d0_value == Decimal("0.00")


@pytest.mark.asyncio
async def test_emergency_fund_coverage(db_session: AsyncSession, sample_account: Account):
    """
    Conta com R$5.000 de saldo.
    Despesa mensal R$2.000, meta 6 meses = R$12.000.
    Cobertura = 5000 / 2000 = 2.5 meses.
    Progresso = 5000 / 12000 × 100 = 41.7%.
    """
    service = LiquidityService(db_session)
    result = await service.get_emergency_fund_coverage(
        monthly_expense=Decimal("2000.00"),
        target_months=6,
    )

    assert result["current_liquid"] == Decimal("5000.00")
    assert result["target_value"] == Decimal("12000.00")
    assert result["coverage_months"] == 2.5
    assert result["is_sufficient"] is False
