"""
Agendador de notificações do FinanceHub.

Roda em uma thread daemon separada e verifica periodicamente se
há alertas pendentes que merecem notificação nativa do sistema operacional.

Verificações realizadas a cada `interval_seconds` (padrão 1 hora):
  1. DARF de IR: vendas de ações > R$20k no mês → notifica se há imposto due
  2. Faturas de cartão: vencem em ≤ 3 dias → notifica com urgência
  3. Gastos recorrentes: vencem em ≤ 2 dias → notifica o usuário
  4. Liquidez: saldo D+0 < mínimo configurado → notifica
  5. Come-cotas: lembrete em abril e outubro

Uso (em main.py):
    scheduler = NotificationScheduler(api_base="http://127.0.0.1:8765")
    scheduler.start()   # inicia thread daemon; morre com o processo
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from decimal import Decimal

import requests

from backend.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class NotificationScheduler:
    """
    Thread daemon que verifica e dispara notificações periódicas.

    Parâmetros:
      api_base         → URL base da API FastAPI (ex: "http://127.0.0.1:8765")
      interval_seconds → intervalo entre verificações (padrão: 3600 = 1 hora)
      min_liquidity    → saldo mínimo D+0 configurado (padrão R$500)
      invoice_days_warn → avisar N dias antes do vencimento da fatura (padrão 3)
    """

    def __init__(
        self,
        api_base: str = "http://127.0.0.1:8765",
        interval_seconds: int = 3600,
        min_liquidity: Decimal = Decimal("500.00"),
        invoice_days_warn: int = 3,
    ) -> None:
        self._api = api_base.rstrip("/")
        self._interval = interval_seconds
        self._min_liquidity = min_liquidity
        self._invoice_days_warn = invoice_days_warn
        self._svc = NotificationService()
        self._thread: threading.Thread | None = None

    # -----------------------------------------------------------------
    # API pública
    # -----------------------------------------------------------------

    def start(self) -> None:
        """Inicia a thread daemon de verificação. Pode ser chamado apenas uma vez."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("NotificationScheduler já está rodando.")
            return

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,                  # termina junto com o processo principal
            name="notification-scheduler",
        )
        self._thread.start()
        logger.info("NotificationScheduler iniciado (intervalo: %ds).", self._interval)

    # -----------------------------------------------------------------
    # Loop principal
    # -----------------------------------------------------------------

    def _run_loop(self) -> None:
        """Loop infinito: verifica → dorme → verifica…"""
        # Aguarda 30 s após o start para dar tempo à API de subir completamente
        time.sleep(30)

        while True:
            try:
                self._check_all()
            except Exception as exc:
                # Nunca deixa a thread morrer por exceção
                logger.exception("Erro no ciclo de notificações: %s", exc)

            time.sleep(self._interval)

    def _check_all(self) -> None:
        """Executa todas as verificações de uma vez."""
        logger.debug("Executando verificações de notificação…")
        self._check_darf()
        self._check_invoices()
        self._check_recurring()
        self._check_liquidity()
        self._check_come_cotas()

    # -----------------------------------------------------------------
    # Verificações individuais
    # -----------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        """
        Faz uma requisição GET à API interna.
        Retorna None em caso de erro (sem lançar exceção).
        """
        try:
            resp = requests.get(f"{self._api}{path}", params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("GET %s → %d", path, resp.status_code)
        except requests.exceptions.RequestException as exc:
            logger.debug("Requisição falhou (%s): %s", path, exc)
        return None

    def _check_darf(self) -> None:
        """
        Verifica se há IR de renda variável devido no mês corrente.
        Usa GET /tax/ir?year=YYYY.
        """
        today = date.today()
        data = self._get("/tax/ir", params={"year": today.year})
        if not data or "months" not in data:
            return

        current_month = today.month
        for m in data["months"]:
            if m["month"] == current_month and m["tax_due"] > 0:
                amount = Decimal(str(m["tax_due"]))
                label = m["month_label"]
                self._svc.notify_darf_due(f"{label}/{today.year}", amount)
                break

    def _check_invoices(self) -> None:
        """
        Verifica faturas de cartão que vencem nos próximos N dias.
        Usa GET /accounts para listar cartões e suas faturas.
        """
        today = date.today()
        accounts = self._get("/accounts")
        if not accounts:
            return

        for account in accounts:
            cards = self._get(f"/accounts/{account['id']}/cards")
            if not cards:
                continue
            for card in cards:
                invoices = self._get(f"/cards/{card['id']}/invoices")
                if not invoices:
                    continue
                for invoice in invoices:
                    if invoice["status"] not in ("aberta", "fechada"):
                        continue
                    due = date.fromisoformat(invoice["due_date"])
                    days = (due - today).days
                    if 0 <= days <= self._invoice_days_warn:
                        amount = Decimal(str(invoice["total_amount"]))
                        self._svc.notify_invoice_due(
                            card_name=card["name"],
                            amount=amount,
                            due_date=due,
                            days_ahead=days,
                        )

    def _check_recurring(self) -> None:
        """
        Verifica gastos recorrentes que vencem em ≤ 2 dias.
        Usa GET /recurring.
        """
        today = date.today()
        items = self._get("/recurring")
        if not items:
            return

        for item in items:
            next_due = item.get("next_due_date")
            if not next_due:
                continue
            due = date.fromisoformat(next_due)
            days = (due - today).days
            if 0 <= days <= 2:
                amount = Decimal(str(item["amount"]))
                self._svc.notify_recurring_due(
                    name=item["name"],
                    amount=amount,
                    days=days,
                )

    def _check_liquidity(self) -> None:
        """
        Verifica se o saldo D+0 está abaixo do mínimo configurado.
        Usa GET /portfolio/liquidity.
        """
        data = self._get("/portfolio/liquidity")
        if not data:
            return

        d0 = Decimal(str(data.get("d0_value", 0)))
        if d0 < self._min_liquidity:
            self._svc.notify_low_liquidity(current=d0, minimum=self._min_liquidity)

    def _check_come_cotas(self) -> None:
        """
        Lembrete de come-cotas: disparado em abril (antecipa maio) e outubro (antecipa novembro).
        """
        today = date.today()
        if today.month in (4, 10) and today.day >= 20:
            next_month = "Maio" if today.month == 4 else "Novembro"
            self._svc.send(
                title="Lembrete: Come-cotas se aproxima",
                message=(
                    f"O come-cotas ocorre em {next_month}. "
                    "Verifique seus fundos de investimento."
                ),
                key=f"comecotas:{today.year}-{today.month}",
            )
