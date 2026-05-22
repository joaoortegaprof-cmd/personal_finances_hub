"""
Schemas Pydantic para os endpoints de simulação financeira.
"""

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class RetirementSimulationRequest(BaseModel):
    monthly_contribution: Decimal = Field(ge=0)
    current_portfolio: Decimal = Field(ge=0)
    target_amount: Decimal = Field(gt=0)
    annual_return_rate: Decimal = Field(ge=0, le=100)
    inflation_rate: Decimal = Field(ge=0, le=50)
    months: int = Field(ge=1, le=600)


class GoalSimulationRequest(BaseModel):
    goal_name: str = Field(max_length=100)
    goal_amount: Decimal = Field(gt=0)
    monthly_contribution: Decimal = Field(ge=0)
    current_savings: Decimal = Field(ge=0)
    annual_return_rate: Decimal = Field(ge=0, le=100)
    deadline_months: int = Field(ge=1, le=600)


class DebtPayoffRequest(BaseModel):
    debt_amount: Decimal = Field(gt=0)
    monthly_rate: Decimal = Field(gt=0, le=100)
    monthly_payment: Decimal = Field(gt=0)
    extra_payment: Decimal = Field(default=Decimal("0"), ge=0)


class FireRequest(BaseModel):
    monthly_expenses: Optional[Decimal] = Field(default=None, ge=0)
    withdrawal_rate: Decimal = Field(default=Decimal("0.04"), gt=0, le=1)
