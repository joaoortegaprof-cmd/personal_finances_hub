"""
Endpoints de fluxo de caixa futuro.

GET /cashflow/projection → projeção de receitas/despesas nos próximos N meses
GET /cashflow/recurring  → resumo consolidado de todos os gastos recorrentes
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.services.cashflow_service import CashflowService

router = APIRouter(prefix="/cashflow", tags=["Fluxo de Caixa"])


@router.get("/projection")
async def get_projection(
    months: int = 3,
    mode:   str = "weekly",   # "weekly" | "monthly"
    db: AsyncSession = Depends(get_db),
):
    """
    Projeta o fluxo de caixa futuro combinando:
      - Receita média histórica (últimos 3 meses)
      - Gastos recorrentes ativos (assinaturas, contratos)
      - Parcelas de dívidas ativas
      - Faturas de cartão abertas

    mode: 'weekly'  → agrupa por semana (melhor para visão de curto prazo)
          'monthly' → agrupa por mês   (melhor para visão de médio prazo)

    months: quantos meses à frente projetar (1–12)
    """
    months = max(1, min(months, 12))
    service = CashflowService(db)
    return await service.project(months=months, mode=mode)


@router.get("/recurring")
async def get_recurring_summary(db: AsyncSession = Depends(get_db)):
    """
    Resumo consolidado dos gastos recorrentes ativos.

    Retorna:
      - Cada recorrente com próximo vencimento e equivalente mensal
      - Totais: semanal, mensal e anual
      - Agrupamento por categoria
    """
    service = CashflowService(db)
    return await service.get_recurring_summary()
