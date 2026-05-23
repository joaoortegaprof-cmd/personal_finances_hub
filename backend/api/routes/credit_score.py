"""
Endpoint de Score de Crédito Pessoal do FinanceHub.

GET /credit-score → CreditScore com 5 componentes e rating 0–1000
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.services.credit_score_service import get_credit_score

router = APIRouter(prefix="/credit-score", tags=["Score de Crédito"])


@router.get("")
async def credit_score_endpoint(db: AsyncSession = Depends(get_db)):
    """
    Calcula o score de crédito pessoal do usuário (0–1.000).

    Composto por 5 fatores:
      - Histórico de pagamentos (35%)
      - Utilização de crédito (30%)
      - Carga de dívidas (15%)
      - Reserva de emergência (10%)
      - Taxa de poupança (10%)

    Rating:
      900–1000: Excelente | 750–899: Muito Bom | 600–749: Bom
      450–599:  Regular   | 300–449: Ruim       | 0–299:   Muito Ruim
    """
    score = await get_credit_score(db)

    return {
        "total":    score.total,
        "rating":   score.rating,
        "color":    score.color,
        "pct":      score.pct,
        "details":  score.details,
        "components": [
            {
                "name":        c.name,
                "score":       c.score,
                "max_score":   c.max_score,
                "weight_pct":  c.weight_pct,
                "description": c.description,
                "details":     c.details,
            }
            for c in score.components
        ],
    }
