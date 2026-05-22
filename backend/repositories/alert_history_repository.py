"""
Repositório de histórico de alertas do FinanceHub.

Responsabilidades:
  - Persistir alertas disparados, com deduplicação diária.
  - Recuperar os N alertas mais recentes para exibição no dashboard.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.alert_history import AlertHistory, AlertHistoryPriority
from backend.services.alert_service import Alert


class AlertHistoryRepository:
    """Operações de leitura e escrita na tabela alert_history."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    async def log_alerts(self, alerts: list[Alert]) -> None:
        """
        Persiste cada alerta na tabela, respeitando deduplicação diária.

        Deduplicação: se já existir uma entrada com o mesmo (alert_type, title)
        no dia de hoje (UTC), o alerta não é reinserido.

        Isso evita spam de linhas idênticas toda vez que o dashboard é
        recarregado pelo usuário ao longo do mesmo dia.
        """
        if not alerts:
            return

        today_start = datetime.combine(date.today(), datetime.min.time())

        # Busca (alert_type, title) já registrados hoje
        result = await self._session.execute(
            select(AlertHistory.alert_type, AlertHistory.title).where(
                AlertHistory.triggered_at >= today_start
            )
        )
        existing_today: set[tuple[str, str]] = {(row[0], row[1]) for row in result}

        for alert in alerts:
            key = (str(alert.alert_type.value), alert.title)
            if key in existing_today:
                continue  # já registrado hoje — pular

            entry = AlertHistory(
                alert_type=str(alert.alert_type.value),
                priority=AlertHistoryPriority(alert.priority.value),
                title=alert.title,
                message=alert.message,
                triggered_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            )
            self._session.add(entry)
            existing_today.add(key)  # evita duplicatas dentro do mesmo batch

        await self._session.commit()

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    async def get_recent(self, limit: int = 50) -> list[AlertHistory]:
        """
        Retorna os N alertas mais recentes, ordenados do mais novo para o mais antigo.

        limit: máximo de registros retornados (padrão 50).
        """
        result = await self._session.execute(
            select(AlertHistory)
            .order_by(AlertHistory.triggered_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
