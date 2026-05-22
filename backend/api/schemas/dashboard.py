"""
Schemas Pydantic para o dashboard principal e alertas.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from backend.services.alert_service import AlertPriority, AlertType


class NetWorthOut(BaseModel):
    total_assets: Decimal
    total_liabilities: Decimal
    net_worth: Decimal
    account_balance: Decimal
    investment_cost: Decimal


class HealthScoreOut(BaseModel):
    total: int
    savings_component: int
    emergency_fund_component: int
    debt_component: int
    details: dict


class MonthlySummaryOut(BaseModel):
    income: Decimal
    expense: Decimal
    balance: Decimal
    savings_rate: Decimal
    reference_month: str  # "MM/AAAA"


class DashboardOut(BaseModel):
    net_worth: NetWorthOut
    health_score: HealthScoreOut
    monthly_summary: MonthlySummaryOut


class AlertOut(BaseModel):
    alert_type: AlertType
    priority: AlertPriority
    title: str
    message: str
    data: dict


class AlertsOut(BaseModel):
    alerts: list[AlertOut]
    count: int


class AlertHistoryItemOut(BaseModel):
    """Registro histórico de um alerta que já foi disparado."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_type: str
    priority: str
    title: str
    message: str
    triggered_at: datetime


class AlertHistoryOut(BaseModel):
    history: list[AlertHistoryItemOut]
    count: int
