"""
Endpoints de simulação financeira.

POST /simulation/retirement  — aposentadoria / independência financeira
POST /simulation/goal        — objetivo financeiro com prazo
POST /simulation/debt-payoff — quitação antecipada de dívida
GET  /simulation/fire        — número FIRE com dados reais do banco
"""

import calendar
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.simulation import (
    DebtPayoffRequest,
    FireRequest,
    GoalSimulationRequest,
    RetirementSimulationRequest,
)
from backend.core.database import get_db
from backend.repositories.transaction_repository import TransactionRepository
from backend.services.financial_summary_service import FinancialSummaryService
from backend.services.simulation_service import (
    calculate_fire_number,
    simulate_debt_payoff,
    simulate_goal,
    simulate_retirement,
)

router = APIRouter(prefix="/simulation", tags=["Simulação"])


@router.post("/retirement")
async def retirement_simulation(body: RetirementSimulationRequest):
    """Projeta crescimento patrimonial mês a mês rumo à aposentadoria."""
    return simulate_retirement(
        monthly_contribution=body.monthly_contribution,
        current_portfolio=body.current_portfolio,
        target_amount=body.target_amount,
        annual_return_rate=body.annual_return_rate,
        inflation_rate=body.inflation_rate,
        months=body.months,
    )


@router.post("/goal")
async def goal_simulation(body: GoalSimulationRequest):
    """Projeta evolução de poupança para um objetivo financeiro específico."""
    return simulate_goal(
        goal_name=body.goal_name,
        goal_amount=body.goal_amount,
        monthly_contribution=body.monthly_contribution,
        current_savings=body.current_savings,
        annual_return_rate=body.annual_return_rate,
        deadline_months=body.deadline_months,
    )


@router.post("/debt-payoff")
async def debt_payoff_simulation(body: DebtPayoffRequest):
    """Compara quitação normal vs quitação acelerada com aporte extra."""
    return simulate_debt_payoff(
        debt_amount=body.debt_amount,
        monthly_rate=body.monthly_rate,
        monthly_payment=body.monthly_payment,
        extra_payment=body.extra_payment,
    )


@router.get("/fire")
async def fire_number(
    withdrawal_rate: float = 0.04,
    db: AsyncSession = Depends(get_db),
):
    """
    Calcula o número FIRE usando dados reais do banco:
    - Despesas médias: média dos últimos 3 meses de despesas
    - Patrimônio atual: saldo total de contas + custo de investimentos
    - Aporte médio: média dos últimos 3 meses de investimentos

    withdrawal_rate: taxa de retirada anual (padrão 4% = 0.04)
    """
    tx_repo = TransactionRepository(db)
    summary_service = FinancialSummaryService(db)

    # Patrimônio atual
    net_worth_summary, _, _ = await _load_summary(summary_service)

    # Média de despesas e aportes dos últimos 3 meses
    today = date.today()
    monthly_expenses_list: list[Decimal] = []
    monthly_investments_list: list[Decimal] = []

    for delta in range(3):
        # mês = today.month - delta (com wrap para ano anterior)
        month = today.month - delta
        year = today.year
        if month <= 0:
            month += 12
            year -= 1

        last_day = calendar.monthrange(year, month)[1]
        start = date(year, month, 1)
        end = date(year, month, last_day)

        totals = await tx_repo.sum_income_and_expense(start, end)
        monthly_expenses_list.append(totals["expense"])

        # Aportes = transações do tipo INVESTMENT no período
        inv_total = await _sum_investments(tx_repo, start, end)
        monthly_investments_list.append(inv_total)

    avg_expense = sum(monthly_expenses_list) / 3 if monthly_expenses_list else Decimal("0")
    avg_investment = sum(monthly_investments_list) / 3 if monthly_investments_list else Decimal("0")

    fire_result = calculate_fire_number(
        monthly_expenses=avg_expense,
        withdrawal_rate=Decimal(str(withdrawal_rate)),
    )

    fire_result["current_portfolio"] = float(net_worth_summary.net_worth)
    fire_result["avg_monthly_expenses"] = float(avg_expense)
    fire_result["avg_monthly_investment"] = float(avg_investment)

    # Quanto falta e estimativa de tempo com aporte atual
    remaining = max(Decimal("0"), Decimal(str(fire_result["fire_number"])) - net_worth_summary.net_worth)
    fire_result["remaining_to_fire"] = float(remaining)

    if avg_investment > 0 and remaining > 0:
        from backend.services.simulation_service import simulate_retirement
        proj = simulate_retirement(
            monthly_contribution=avg_investment,
            current_portfolio=net_worth_summary.net_worth,
            target_amount=Decimal(str(fire_result["fire_number"])),
            annual_return_rate=Decimal("10"),  # estimativa conservadora
            inflation_rate=Decimal("5"),
            months=600,
        )
        fire_result["estimated_months_to_fire"] = proj["months_to_target"]
    else:
        fire_result["estimated_months_to_fire"] = None

    return fire_result


# -----------------------------------------------------------------
# Auxiliares privados
# -----------------------------------------------------------------

async def _load_summary(service: FinancialSummaryService):
    net_worth = await service.get_net_worth()
    health = await service.get_health_score()
    monthly = await service.get_monthly_summary(date.today())
    return net_worth, health, monthly


async def _sum_investments(
    repo: TransactionRepository,
    start: date,
    end: date,
) -> Decimal:
    """Soma transações do tipo INVESTMENT em um período."""
    from sqlalchemy import select, func
    from backend.models.transaction import Transaction, TransactionType

    stmt = select(
        func.coalesce(
            func.sum(Transaction.amount).filter(
                Transaction.transaction_type == TransactionType.INVESTMENT
            ),
            Decimal("0.00"),
        ).label("total")
    ).where(
        Transaction.transaction_date >= start,
        Transaction.transaction_date <= end,
    )
    result = await repo._session.execute(stmt)
    return result.scalar() or Decimal("0")
