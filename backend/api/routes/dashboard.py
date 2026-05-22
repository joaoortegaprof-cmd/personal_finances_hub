"""
Endpoints do dashboard principal e sistema de alertas inteligentes.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.dashboard import (
    AlertHistoryItemOut,
    AlertHistoryOut,
    AlertOut,
    AlertsOut,
    DashboardOut,
    HealthScoreOut,
    MonthlySummaryOut,
    NetWorthOut,
)
from backend.core.database import get_db
from backend.repositories.alert_history_repository import AlertHistoryRepository
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
    invoice_due_days: Annotated[int, Query(ge=1, le=30)] = 3,
    maturity_days_ahead: Annotated[int, Query(ge=1, le=365)] = 30,
    savings_goal_pct: Annotated[float, Query(ge=1, le=100)] = 20.0,
    debt_due_days: Annotated[int, Query(ge=1, le=30)] = 3,
    recurring_due_days: Annotated[int, Query(ge=1, le=30)] = 7,
    min_liquidity: Annotated[float, Query(ge=0)] = 0.0,
    enabled: Annotated[
        str | None,
        Query(description="Tipos de alerta ativos separados por vírgula; None = todos"),
    ] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna todos os alertas ativos ordenados por prioridade (ALTA → MÉDIA → BAIXA).
    Cada alerta disparado é automaticamente persistido no histórico (deduplicação diária).

    Tipos suportados: darf, fatura_vencendo, renda_fixa, taxa_poupanca,
    come_cotas, parcela_divida, recorrente, liquidez_baixa, rebalanceamento, juros_altos.
    """
    enabled_set = set(enabled.split(",")) if enabled else None
    service = AlertService(db)
    alerts = await service.get_all_alerts(
        invoice_due_days=invoice_due_days,
        maturity_days_ahead=maturity_days_ahead,
        savings_goal_pct=Decimal(str(savings_goal_pct)),
        debt_due_days=debt_due_days,
        recurring_due_days=recurring_due_days,
        min_liquidity=Decimal(str(min_liquidity)),
        enabled=enabled_set,
    )

    # Persiste os alertas no histórico (deduplicação diária no repositório)
    if alerts:
        history_repo = AlertHistoryRepository(db)
        await history_repo.log_alerts(alerts)

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


@router.get("/alert-history", response_model=AlertHistoryOut)
async def get_alert_history(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna o histórico dos N alertas mais recentes disparados pelo sistema.

    Ordenado do mais recente para o mais antigo.
    Útil para auditoria e para o usuário saber o que foi alertado no passado.
    """
    repo = AlertHistoryRepository(db)
    history = await repo.get_recent(limit=limit)
    return AlertHistoryOut(
        history=[
            AlertHistoryItemOut(
                id=h.id,
                alert_type=h.alert_type,
                priority=h.priority.value,
                title=h.title,
                message=h.message,
                triggered_at=h.triggered_at,
            )
            for h in history
        ],
        count=len(history),
    )


async def _load_dashboard_data(service: FinancialSummaryService):
    """Carrega os três blocos de dados do dashboard de forma sequencial."""
    net_worth = await service.get_net_worth()
    health_score = await service.get_health_score()
    monthly = await service.get_monthly_summary()
    return net_worth, health_score, monthly
