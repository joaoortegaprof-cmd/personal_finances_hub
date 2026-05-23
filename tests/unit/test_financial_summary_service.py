"""
Testes unitários do FinancialSummaryService.

Cobertura:
  - get_net_worth: patrimônio com conta + investimento
  - get_monthly_summary: receitas, despesas, saldo e taxa de poupança
  - get_health_score: componentes savings, emergency_fund e debt
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.account import Account, AccountType
from backend.models.asset import Asset, AssetPosition, AssetType, LiquidityWindow, OperationType
from backend.models.transaction import Transaction, TransactionCategory, TransactionType
from backend.services.financial_summary_service import FinancialSummaryService


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

async def _add_transaction(
    db: AsyncSession,
    account: Account,
    amount: Decimal,
    tx_type: TransactionType,
    tx_date: date | None = None,
    category: TransactionCategory = TransactionCategory.OTHER,
) -> Transaction:
    """Cria e persiste uma transação de teste."""
    tx = Transaction(
        description="Tx de teste",
        amount=amount,
        transaction_date=tx_date or date.today(),
        transaction_type=tx_type,
        category=category,
        account_id=account.id,
    )
    db.add(tx)
    await db.flush()
    return tx


# ─────────────────────────────────────────────────────────────────
# Testes: get_net_worth
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_net_worth_only_account(db_session: AsyncSession, sample_account: Account):
    """
    Patrimônio de uma conta com saldo inicial R$5.000, sem investimentos.

    Esperado:
      account_balance   = 5000.00
      investment_cost   = 0.00
      total_assets      = 5000.00
      total_liabilities = 0.00
      net_worth         = 5000.00
    """
    service = FinancialSummaryService(db_session)
    result = await service.get_net_worth()

    assert result.account_balance == Decimal("5000.00")
    assert result.investment_cost == Decimal("0.00")
    assert result.total_assets == Decimal("5000.00")
    assert result.total_liabilities == Decimal("0.00")
    assert result.net_worth == Decimal("5000.00")


@pytest.mark.asyncio
async def test_net_worth_with_investment(
    db_session: AsyncSession,
    sample_account: Account,
    sample_asset: Asset,
):
    """
    Patrimônio com conta R$5.000 e investimento de 100 ações a R$30.

    Custo investido = 100 × 30 = R$3.000
    Total ativos    = 5000 + 3000 = R$8.000
    """
    position = AssetPosition(
        asset_id=sample_asset.id,
        operation_type=OperationType.BUY,
        operation_date=date(2026, 1, 10),
        quantity=Decimal("100"),
        unit_price=Decimal("30.00"),
        fees=Decimal("0.00"),
    )
    db_session.add(position)
    await db_session.flush()

    service = FinancialSummaryService(db_session)
    result = await service.get_net_worth()

    assert result.investment_cost == Decimal("3000.00")
    assert result.total_assets == Decimal("8000.00")
    assert result.net_worth == Decimal("8000.00")


@pytest.mark.asyncio
async def test_net_worth_reduces_after_sell(
    db_session: AsyncSession,
    sample_account: Account,
    sample_asset: Asset,
):
    """
    Custo líquido = compras - vendas.
    Compra 100 a R$30 (R$3.000), vende 50 a R$35 (R$1.750).
    investment_cost = 3000 - 1750 = R$1.250.
    """
    db_session.add(AssetPosition(
        asset_id=sample_asset.id,
        operation_type=OperationType.BUY,
        operation_date=date(2026, 1, 10),
        quantity=Decimal("100"),
        unit_price=Decimal("30.00"),
        fees=Decimal("0.00"),
    ))
    db_session.add(AssetPosition(
        asset_id=sample_asset.id,
        operation_type=OperationType.SELL,
        operation_date=date(2026, 2, 10),
        quantity=Decimal("-50"),
        unit_price=Decimal("35.00"),
        fees=Decimal("0.00"),
    ))
    await db_session.flush()

    service = FinancialSummaryService(db_session)
    result = await service.get_net_worth()

    assert result.investment_cost == Decimal("1250.00")


# ─────────────────────────────────────────────────────────────────
# Testes: get_monthly_summary
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_monthly_summary_income_and_expense(
    db_session: AsyncSession,
    sample_account: Account,
):
    """
    Receita R$6.000, despesa R$2.000 → saldo R$4.000, taxa 66.67%.
    """
    today = date.today()

    await _add_transaction(db_session, sample_account, Decimal("6000.00"),
                           TransactionType.INCOME, today)
    await _add_transaction(db_session, sample_account, Decimal("2000.00"),
                           TransactionType.DEBIT_EXPENSE, today)

    service = FinancialSummaryService(db_session)
    result = await service.get_monthly_summary(today)

    assert result.income == Decimal("6000.00")
    assert result.expense == Decimal("2000.00")
    assert result.balance == Decimal("4000.00")
    # taxa = (4000 / 6000) * 100 = 66.666... ≈ 66.67
    assert result.savings_rate == Decimal("66.67")


@pytest.mark.asyncio
async def test_monthly_summary_zero_income(
    db_session: AsyncSession,
    sample_account: Account,
):
    """
    Sem receitas → taxa de poupança é 0.00 (não divide por zero).
    """
    today = date.today()
    await _add_transaction(db_session, sample_account, Decimal("500.00"),
                           TransactionType.DEBIT_EXPENSE, today)

    service = FinancialSummaryService(db_session)
    result = await service.get_monthly_summary(today)

    assert result.income == Decimal("0.00")
    assert result.savings_rate == Decimal("0.00")


@pytest.mark.asyncio
async def test_monthly_summary_ignores_other_months(
    db_session: AsyncSession,
    sample_account: Account,
):
    """
    Transações de outros meses não devem entrar no resumo do mês corrente.
    """
    today = date.today()
    # Lançamento no mês corrente
    await _add_transaction(db_session, sample_account, Decimal("3000.00"),
                           TransactionType.INCOME, today)
    # Lançamento no mês anterior — não deve contar
    prev_month = date(today.year, today.month - 1, 1) if today.month > 1 else date(today.year - 1, 12, 1)
    await _add_transaction(db_session, sample_account, Decimal("99999.00"),
                           TransactionType.INCOME, prev_month)

    service = FinancialSummaryService(db_session)
    result = await service.get_monthly_summary(today)

    assert result.income == Decimal("3000.00")


# ─────────────────────────────────────────────────────────────────
# Testes: get_health_score
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_score_perfect(db_session: AsyncSession, sample_account: Account):
    """
    Score perfeito: receita R$10.000, despesa R$3.000, sem dívidas.

    Taxa de poupança = 70% (meta 20%) → savings_component = 40 (máximo)
    Cobertura emergência = 5000 / (3000×6) = 27.7% → emergency_component = 11
    Dívida/renda = 0 → debt_component = 20
    Total ≈ 71
    """
    today = date.today()
    await _add_transaction(db_session, sample_account, Decimal("10000.00"),
                           TransactionType.INCOME, today)
    await _add_transaction(db_session, sample_account, Decimal("3000.00"),
                           TransactionType.DEBIT_EXPENSE, today)

    service = FinancialSummaryService(db_session)
    score = await service.get_health_score()

    # savings_component deve ser máximo (70% >> 20%)
    assert score.savings_component == 40
    # sem dívidas: debt_component máximo
    assert score.debt_component == 20
    # total deve ser positivo e coerente
    assert score.total == score.savings_component + score.emergency_fund_component + score.debt_component
    assert 0 <= score.total <= 100


@pytest.mark.asyncio
async def test_health_score_no_savings(db_session: AsyncSession, sample_account: Account):
    """
    Sem renda registrada → savings_component = 0.
    """
    service = FinancialSummaryService(db_session)
    score = await service.get_health_score()

    assert score.savings_component == 0


@pytest.mark.asyncio
async def test_health_score_debt_tiers(db_session: AsyncSession, sample_account: Account):
    """
    Testa os 5 tiers de debt_to_income:
      ratio < 1  → 20 pts
      1 ≤ ratio < 2  → 15 pts
      2 ≤ ratio < 4  → 10 pts
      4 ≤ ratio < 6  →  5 pts
      ratio ≥ 6  →  0 pts

    Neste teste: sem dívidas, renda R$5.000 → ratio = 0 → 20 pts.
    """
    today = date.today()
    await _add_transaction(db_session, sample_account, Decimal("5000.00"),
                           TransactionType.INCOME, today)

    service = FinancialSummaryService(db_session)
    score = await service.get_health_score()

    # ratio = 0 / 5000 = 0 < 1 → 20 pts
    assert score.debt_component == 20
    assert score.details["debt_to_income_ratio"] == 0.0
