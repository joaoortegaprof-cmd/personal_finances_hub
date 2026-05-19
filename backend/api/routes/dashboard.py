"""
Endpoints do dashboard principal e sistema de alertas inteligentes.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.dashboard import (
    AlertOut,
    AlertsOut,
    DashboardOut,
    HealthScoreOut,
    MonthlySummaryOut,
    NetWorthOut,
)
from backend.core.database import get_db
from backend.services.alert_service import AlertService
from backend.services.financial_summary_service import FinancialSummaryService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("", response_model=DashboardOut)
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    """
    Visão consolidada do patrimônio:
    - Patrimônio líquido (ativos − passivos)
    - Score de saúde financeira (0–100)
    - Resumo de receitas e despesas do mês corrente
    """
    service = FinancialSummaryService(db)
    net_worth, health_score, monthly = await _load_dashboard_data(service)
    today = date.today()

    return DashboardOut(
        net_worth=NetWorthOut(
            total_assets=net_worth.total_assets,
            total_liabilities=net_worth.total_liabilities,
            net_worth=net_worth.net_worth,
            account_balance=net_worth.account_balance,
            investment_cost=net_worth.investment_cost,
        ),
        health_score=HealthScoreOut(
            total=health_score.total,
            savings_component=health_score.savings_component,
            emergency_fund_component=health_score.emergency_fund_component,
            debt_component=health_score.debt_component,
            details=health_score.details,
        ),
        monthly_summary=MonthlySummaryOut(
            income=monthly.income,
            expense=monthly.expense,
            balance=monthly.balance,
            savings_rate=monthly.savings_rate,
            reference_month=f"{today.month:02d}/{today.year}",
        ),
    )


@router.get("/alerts", response_model=AlertsOut)
async def get_alerts(
    invoice_due_days: Annotated[
        int,
        Query(ge=1, le=30, description="Dias de antecedência para alertar faturas de cartão"),
    ] = 3,
    maturity_days_ahead: Annotated[
        int,
        Query(ge=1, le=365, description="Horizonte em dias para vencimentos de renda fixa"),
    ] = 30,
    savings_goal_pct: Annotated[
        float,
        Query(ge=1, le=100, description="Meta de taxa de poupança em % (padrão: 20%)"),
    ] = 20.0,
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna todos os alertas ativos ordenados por prioridade (ALTA → MÉDIA → BAIXA):
    - DARF de renda variável (vendas de ações > R$20.000 no mês)
    - Faturas de cartão próximas do vencimento
    - Títulos de renda fixa com vencimento próximo
    - Taxa de poupança abaixo da meta configurada
    """
    service = AlertService(db)
    alerts = await service.get_all_alerts(
        invoice_due_days=invoice_due_days,
        maturity_days_ahead=maturity_days_ahead,
        savings_goal_pct=Decimal(str(savings_goal_pct)),
    )
    return AlertsOut(
        alerts=[
            AlertOut(
                alert_type=a.alert_type,
                priority=a.priority,
                title=a.title,
                message=a.message,
                data=a.data,
            )
            for a in alerts
        ],
        count=len(alerts),
    )


async def _load_dashboard_data(service: FinancialSummaryService):
    """Carrega os três blocos de dados do dashboard de forma sequencial."""
    net_worth = await service.get_net_worth()
    health_score = await service.get_health_score()
    monthly = await service.get_monthly_summary()
    return net_worth, health_score, monthly
