"""
Serviço de análise de padrão de gastos do FinanceHub.

Compara os gastos do mês atual com a média dos 3 meses anteriores por categoria.
Categorias com variação > ANOMALY_THRESHOLD (padrão: 50%) são sinalizadas como anomalias.
Também calcula a tendência (crescente/estável/decrescente) com base na inclinação linear.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.ext.asyncio import AsyncSession

from backend.repositories.transaction_repository import TransactionRepository

ANOMALY_THRESHOLD = 50.0   # % acima da média → anomalia


@dataclass
class CategoryPattern:
    category:       str
    current_month:  float   # gasto no mês atual
    avg_3m:         float   # média dos 3 meses anteriores
    change_pct:     float   # variação % em relação à média
    trend:          str     # "crescente" | "estável" | "decrescente"
    is_anomaly:     bool    # True se change_pct > ANOMALY_THRESHOLD


class ExpensePatternService:

    def __init__(self, session: AsyncSession) -> None:
        self._repo = TransactionRepository(session)

    async def analyse(self, reference_date: date | None = None) -> list[CategoryPattern]:
        today = reference_date or date.today()

        # Mês atual (do 1º até hoje)
        current_start = today.replace(day=1)
        current_by_cat = await self._repo.sum_expense_by_category(current_start, today)

        # 3 meses anteriores
        history: list[dict[str, float]] = []
        for delta in range(1, 4):
            m = today.month - delta
            y = today.year
            if m <= 0:
                m += 12; y -= 1
            last = calendar.monthrange(y, m)[1]
            monthly = await self._repo.sum_expense_by_category(
                date(y, m, 1), date(y, m, last)
            )
            history.append(monthly)

        # Todas as categorias com movimento em qualquer período
        all_cats = set(current_by_cat.keys())
        for h in history:
            all_cats.update(h.keys())

        patterns: list[CategoryPattern] = []
        for cat in sorted(all_cats):
            current = current_by_cat.get(cat, 0.0)

            monthly_vals = [h.get(cat, 0.0) for h in history]
            avg3m = sum(monthly_vals) / 3 if monthly_vals else 0.0

            if avg3m > 0:
                change_pct = (current - avg3m) / avg3m * 100
            elif current > 0:
                change_pct = 100.0
            else:
                change_pct = 0.0

            # Tendência: slope dos 3 meses históricos (mais recente primeiro)
            vals = list(reversed(monthly_vals))  # cronológico: mais antigo primeiro
            if len(vals) >= 2 and sum(vals) > 0:
                n = len(vals)
                x_mean = (n - 1) / 2
                y_mean = sum(vals) / n
                num = sum((i - x_mean) * (vals[i] - y_mean) for i in range(n))
                den = sum((i - x_mean) ** 2 for i in range(n))
                slope = num / den if den else 0.0
                rel_slope = slope / y_mean * 100 if y_mean else 0.0
                if rel_slope > 5:
                    trend = "crescente"
                elif rel_slope < -5:
                    trend = "decrescente"
                else:
                    trend = "estável"
            else:
                trend = "estável"

            patterns.append(CategoryPattern(
                category=cat,
                current_month=round(current, 2),
                avg_3m=round(avg3m, 2),
                change_pct=round(change_pct, 1),
                trend=trend,
                is_anomaly=change_pct > ANOMALY_THRESHOLD and current > 50,
            ))

        # Anomalias primeiro, depois por gasto atual decrescente
        patterns.sort(key=lambda p: (-p.is_anomaly, -p.current_month))
        return patterns
