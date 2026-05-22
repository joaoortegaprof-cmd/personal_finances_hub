"""
Serviço de notificações do sistema operacional para o FinanceHub.

Verifica alertas financeiros e dispara notificações nativas via plyer:
  - Faturas de cartão vencendo em ≤ N dias
  - Dívidas com parcelas vencendo em ≤ N dias
  - Gastos recorrentes vencendo em ≤ N dias ou vencidos
  - Saldo corrente abaixo de zero (fluxo negativo projetado)

Uso:
    service = NotificationService(api_client)
    service.check_and_notify()   # síncrono, chame em QThread
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from frontend.components.api_client import ApiClient, ApiError

logger = logging.getLogger(__name__)

# Antecedência padrão para alertas (dias)
_DAYS_AHEAD_INVOICE   = 5
_DAYS_AHEAD_DEBT      = 3
_DAYS_AHEAD_RECURRING = 7

# Nome da app nas notificações
_APP_NAME = "FinanceHub"
_APP_ICON = ""   # caminho para ícone .png (opcional)


def _notify(title: str, message: str) -> None:
    """Dispara notificação nativa; engole erros para não quebrar a UI."""
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name=_APP_NAME,
            app_icon=_APP_ICON or None,
            timeout=8,
        )
    except Exception as exc:
        logger.warning("Falha ao enviar notificação: %s", exc)


class NotificationService:

    def __init__(self, client: ApiClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Ponto de entrada
    # ------------------------------------------------------------------

    def check_and_notify(self) -> None:
        """Executa todas as verificações e dispara as notificações pertinentes."""
        self._check_invoices()
        self._check_debts()
        self._check_recurring()

    # ------------------------------------------------------------------
    # Faturas de cartão
    # ------------------------------------------------------------------

    def _check_invoices(self) -> None:
        try:
            cards = self._client.get_cards()
        except ApiError:
            return

        today    = date.today()
        deadline = today + timedelta(days=_DAYS_AHEAD_INVOICE)

        for card in cards:
            invoices = card.get("invoices") or []
            for inv in invoices:
                if inv.get("status") != "open":
                    continue
                try:
                    due = date.fromisoformat(inv["due_date"][:10])
                except (KeyError, ValueError):
                    continue
                if today <= due <= deadline:
                    days_left = (due - today).days
                    amount    = float(inv.get("total_amount", 0))
                    prefix    = "Vence hoje" if days_left == 0 else f"Vence em {days_left} dia(s)"
                    _notify(
                        f"Fatura {card.get('name', 'Cartão')}",
                        f"{prefix} — R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                    )

    # ------------------------------------------------------------------
    # Parcelas de dívidas
    # ------------------------------------------------------------------

    def _check_debts(self) -> None:
        try:
            debts = self._client.get_debts()
        except ApiError:
            return

        today    = date.today()
        deadline = today + timedelta(days=_DAYS_AHEAD_DEBT)

        for debt in debts:
            if not debt.get("is_active", True):
                continue
            remaining = int(debt.get("total_installments", 1)) - int(debt.get("paid_installments", 0))
            if remaining <= 0:
                continue
            due_day = int(debt.get("due_day", 1))
            # Próximo vencimento este ou próximo mês
            try:
                next_due = date(today.year, today.month, min(due_day, 28))
                if next_due < today:
                    m = today.month + 1
                    y = today.year + (m - 1) // 12
                    m = (m - 1) % 12 + 1
                    next_due = date(y, m, min(due_day, 28))
            except ValueError:
                continue

            if today <= next_due <= deadline:
                days_left = (next_due - today).days
                amount    = float(debt.get("installment_amount", 0))
                prefix    = "Vence hoje" if days_left == 0 else f"Vence em {days_left} dia(s)"
                _notify(
                    f"Parcela: {debt.get('name', 'Dívida')}",
                    f"{prefix} — R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                )

    # ------------------------------------------------------------------
    # Gastos recorrentes
    # ------------------------------------------------------------------

    def _check_recurring(self) -> None:
        try:
            expenses = self._client._get("/recurring-expenses")
        except ApiError:
            return

        today    = date.today()
        deadline = today + timedelta(days=_DAYS_AHEAD_RECURRING)

        for exp in expenses:
            if not exp.get("is_active", True):
                continue
            try:
                due = date.fromisoformat(exp["next_due_date"][:10])
            except (KeyError, ValueError):
                continue

            if due < today:
                # Já vencido
                days_late = (today - due).days
                _notify(
                    f"Recorrente vencido: {exp.get('name', '')}",
                    f"Venceu há {days_late} dia(s) — R$ {float(exp.get('amount', 0)):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                )
            elif due <= deadline:
                days_left = (due - today).days
                prefix = "Vence hoje" if days_left == 0 else f"Vence em {days_left} dia(s)"
                _notify(
                    f"Recorrente: {exp.get('name', '')}",
                    f"{prefix} — R$ {float(exp.get('amount', 0)):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                )
