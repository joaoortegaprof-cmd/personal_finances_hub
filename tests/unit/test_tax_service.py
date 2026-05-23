"""
Testes unitários do serviço de IR (tax_service).

Cobertura:
  - Isenção: vendas <= R$20.000 → tax = 0
  - Tributável: vendas > R$20.000 com lucro → tax = 15%
  - Operação no prejuízo: tax = 0 mesmo acima do limite
  - Compensação de perdas: prejuízo de mês anterior abate lucro futuro
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.asset import Asset, AssetPosition, AssetType, LiquidityWindow, OperationType
from backend.services.tax_service import calculate_monthly_stock_sales, get_ir_summary


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

async def _create_stock(db: AsyncSession, ticker: str = "TEST4") -> Asset:
    """Cria e persiste um ativo do tipo STOCK para testes."""
    asset = Asset(
        ticker=ticker,
        name=f"Ativo {ticker}",
        asset_type=AssetType.STOCK,
        currency="BRL",
        liquidity=LiquidityWindow.D2,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    return asset


async def _buy(
    db: AsyncSession,
    asset: Asset,
    qty: Decimal,
    price: Decimal,
    op_date: date,
    fees: Decimal = Decimal("0.00"),
) -> AssetPosition:
    pos = AssetPosition(
        asset_id=asset.id,
        operation_type=OperationType.BUY,
        operation_date=op_date,
        quantity=qty,
        unit_price=price,
        fees=fees,
    )
    db.add(pos)
    await db.flush()
    return pos


async def _sell(
    db: AsyncSession,
    asset: Asset,
    qty: Decimal,
    price: Decimal,
    op_date: date,
    fees: Decimal = Decimal("0.00"),
) -> AssetPosition:
    pos = AssetPosition(
        asset_id=asset.id,
        operation_type=OperationType.SELL,
        operation_date=op_date,
        quantity=-qty,   # negativo indica saída de posição
        unit_price=price,
        fees=fees,
    )
    db.add(pos)
    await db.flush()
    return pos


# ─────────────────────────────────────────────────────────────────
# Testes: isenção (vendas <= R$20.000)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exempt_when_sales_below_20k(db_session: AsyncSession):
    """
    Vendas totais de R$15.000 → isento, IR = 0.

    Limite de isenção para ações: R$20.000/mês.
    """
    asset = await _create_stock(db_session, "ISNT4")

    # Compra: 1000 ações a R$10 = R$10.000 de custo
    await _buy(db_session, asset, Decimal("1000"), Decimal("10.00"),
               date(2026, 1, 5))

    # Venda: 1000 ações a R$15 = R$15.000 de receita (< R$20k)
    await _sell(db_session, asset, Decimal("1000"), Decimal("15.00"),
                date(2026, 1, 20))

    result = await calculate_monthly_stock_sales(db_session, 2026, 1)

    assert result["is_exempt"] is True
    assert result["total_sales"] == Decimal("15000.00")
    assert result["tax_before_compensation"] == Decimal("0")


@pytest.mark.asyncio
async def test_exempt_at_exact_limit(db_session: AsyncSession):
    """
    Vendas totais exatamente R$20.000 → ainda isento (limite inclusive).
    """
    asset = await _create_stock(db_session, "EXCT4")
    await _buy(db_session, asset, Decimal("1000"), Decimal("10.00"), date(2026, 2, 5))
    await _sell(db_session, asset, Decimal("1000"), Decimal("20.00"), date(2026, 2, 20))

    result = await calculate_monthly_stock_sales(db_session, 2026, 2)

    assert result["is_exempt"] is True
    assert result["total_sales"] == Decimal("20000.00")


# ─────────────────────────────────────────────────────────────────
# Testes: tributação (vendas > R$20.000 com lucro)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_taxable_above_20k(db_session: AsyncSession):
    """
    Vendas R$30.000, custo R$15.000, lucro R$15.000.
    IR = 15% × 15.000 = R$2.250.
    """
    asset = await _create_stock(db_session, "TAXB4")

    # Compra: 1000 ações a R$15 = R$15.000 de custo
    await _buy(db_session, asset, Decimal("1000"), Decimal("15.00"), date(2026, 3, 5))

    # Venda: 1000 ações a R$30 = R$30.000 de receita (> R$20k)
    await _sell(db_session, asset, Decimal("1000"), Decimal("30.00"), date(2026, 3, 25))

    result = await calculate_monthly_stock_sales(db_session, 2026, 3)

    assert result["is_exempt"] is False
    assert result["total_sales"] == Decimal("30000.00")
    assert result["total_profit"] == Decimal("15000.00")
    assert result["tax_before_compensation"] == Decimal("2250.00")  # 15% × 15000


@pytest.mark.asyncio
async def test_no_tax_when_loss_above_20k(db_session: AsyncSession):
    """
    Vendas > R$20.000 mas com prejuízo → IR = 0 (não há lucro tributável).
    """
    asset = await _create_stock(db_session, "LOSS4")

    # Compra cara, vende barato
    await _buy(db_session, asset, Decimal("1000"), Decimal("40.00"), date(2026, 4, 5))
    await _sell(db_session, asset, Decimal("1000"), Decimal("25.00"), date(2026, 4, 25))

    result = await calculate_monthly_stock_sales(db_session, 2026, 4)

    assert result["is_exempt"] is False          # vendas > 20k
    assert result["total_profit"] < Decimal("0") # prejuízo
    assert result["tax_before_compensation"] == Decimal("0")


# ─────────────────────────────────────────────────────────────────
# Testes: compensação de perdas (get_ir_summary)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_loss_carryforward(db_session: AsyncSession):
    """
    Prejuízo de jan/2026 abate lucro de fev/2026.

    Jan: vende R$30k com prejuízo R$5.000 → acumula perda R$5.000
    Fev: vende R$30k com lucro R$10.000 → lucro tributável = 10000 - 5000 = R$5.000
         IR = 15% × 5000 = R$750 (não R$1.500)
    """
    asset = await _create_stock(db_session, "CARRY4")

    # Janeiro: compra a 35, vende a 30 (prejuízo de R$5 por ação × 1000 = -R$5k)
    await _buy(db_session, asset, Decimal("1000"), Decimal("35.00"), date(2026, 1, 5))
    await _sell(db_session, asset, Decimal("1000"), Decimal("30.00"), date(2026, 1, 25))

    # Fevereiro: compra a 20, vende a 30 (lucro de R$10 por ação × 1000 = +R$10k)
    await _buy(db_session, asset, Decimal("1000"), Decimal("20.00"), date(2026, 2, 5))
    await _sell(db_session, asset, Decimal("1000"), Decimal("30.00"), date(2026, 2, 25))

    summary = await get_ir_summary(db_session, 2026)

    jan = next(m for m in summary["months"] if m["month"] == 1)
    fev = next(m for m in summary["months"] if m["month"] == 2)

    # Janeiro: prejuízo, sem IR
    assert jan["tax_due"] == Decimal("0")
    assert jan["total_profit"] < 0

    # Fevereiro: lucro de 10k, mas compensa 5k → IR sobre 5k = 750
    assert fev["tax_due"] == Decimal("750.00")


@pytest.mark.asyncio
async def test_no_tax_in_empty_month(db_session: AsyncSession):
    """
    Mês sem vendas → isenção, IR = 0.
    """
    result = await calculate_monthly_stock_sales(db_session, 2025, 6)

    assert result["is_exempt"] is True
    assert result["total_sales"] == Decimal("0")
    assert result["tax_before_compensation"] == Decimal("0")
