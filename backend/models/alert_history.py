"""
Model de histórico de alertas do FinanceHub.

Cada linha registra um alerta que foi disparado pelo sistema, preservando
o contexto (tipo, prioridade, mensagem) no momento em que ocorreu.

Deduplicação:
  O repositório insere apenas uma ocorrência por (alert_type, title)
  por dia — evitar spam de registros idênticos toda vez que o
  dashboard é recarregado.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class AlertHistoryPriority(str, enum.Enum):
    HIGH   = "alta"
    MEDIUM = "media"
    LOW    = "baixa"


class AlertHistory(Base):
    """Registro persistente de alertas disparados pelo sistema."""

    __tablename__ = "alert_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Tipo do alerta — espelha AlertType.value
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Prioridade no momento em que o alerta foi gerado
    priority: Mapped[AlertHistoryPriority] = mapped_column(
        Enum(AlertHistoryPriority, name="alert_history_priority_enum"),
        nullable=False,
    )

    # Título curto (≤ 100 chars) — mesmo usado na notificação do SO
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # Mensagem completa — contexto e valores no momento do disparo
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Quando foi disparado (UTC)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AlertHistory id={self.id} type={self.alert_type!r} "
            f"priority={self.priority} triggered_at={self.triggered_at}>"
        )
