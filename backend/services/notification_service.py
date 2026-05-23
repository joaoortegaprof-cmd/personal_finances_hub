"""
Serviço de notificações nativas do FinanceHub.

Usa a biblioteca `plyer` para disparar notificações do sistema operacional
(Linux: libnotify via notify-send; macOS: NSUserNotification; Windows: ToastNotifier).

Funcionalidades:
  - NotificationService.send()          → dispara uma notificação genérica
  - notify_darf_due()                   → alerta fiscal de IR pendente
  - notify_invoice_due()                → fatura de cartão prestes a vencer
  - notify_goal_reached()               → objetivo financeiro atingido
  - notify_budget_exceeded()            → gasto acima do orçamento configurado

Anti-spam:
  - Cada notificação tem uma chave única (tipo + referência).
  - A chave é registrada em `data/notification_cache.json` com a data.
  - Se a mesma chave já foi disparada no mesmo dia, é silenciada.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache em disco: evita reenviar a mesma notificação no mesmo dia
_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "notification_cache.json"
_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)


# -----------------------------------------------------------------
# Cache anti-spam
# -----------------------------------------------------------------

def _load_cache() -> dict:
    """Carrega o cache de notificações do arquivo JSON."""
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    """Persiste o cache no arquivo JSON."""
    try:
        _CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Não foi possível salvar o cache de notificações: %s", exc)


def _already_sent_today(key: str) -> bool:
    """Retorna True se a notificação `key` já foi enviada hoje."""
    cache = _load_cache()
    today_str = date.today().isoformat()
    return cache.get(key) == today_str


def _mark_sent(key: str) -> None:
    """Registra que a notificação `key` foi enviada hoje."""
    cache = _load_cache()
    cache[key] = date.today().isoformat()
    # Limpa entradas antigas (mais de 30 dias) para não crescer indefinidamente
    today = date.today()
    cache = {
        k: v for k, v in cache.items()
        if (today - date.fromisoformat(v)).days <= 30
    }
    _save_cache(cache)


# -----------------------------------------------------------------
# NotificationService
# -----------------------------------------------------------------

class NotificationService:
    """
    Fachada para envio de notificações nativas do sistema operacional.

    Cada método público tem um título, mensagem e chave anti-spam embutidos.
    O `plyer` cuida de adaptar a chamada para a API correta do SO.
    """

    APP_NAME = "FinanceHub"
    ICON_PATH: str | None = None  # caminho para .ico/.png se disponível

    # -----------------------------------------------------------------
    # Método base
    # -----------------------------------------------------------------

    def send(
        self,
        title: str,
        message: str,
        *,
        timeout: int = 8,
        key: str | None = None,
    ) -> bool:
        """
        Dispara uma notificação nativa.

        Parâmetros:
          title    → título exibido na notificação
          message  → corpo da mensagem
          timeout  → segundos até a notificação desaparecer (nem todos os SOs respeitam)
          key      → chave anti-spam; se fornecida, a notificação é enviada apenas
                     uma vez por dia para cada valor de key

        Retorna True se a notificação foi disparada, False caso contrário.
        """
        if key and _already_sent_today(key):
            logger.debug("Notificação '%s' já enviada hoje — ignorando.", key)
            return False

        try:
            from plyer import notification  # importação tardia: plyer é opcional

            notification.notify(
                title=f"💰 {title}",
                message=message,
                app_name=self.APP_NAME,
                timeout=timeout,
            )
            logger.info("Notificação enviada: '%s'", title)

            if key:
                _mark_sent(key)

            return True

        except ImportError:
            logger.warning("plyer não instalado — notificações desativadas.")
            return False
        except Exception as exc:
            # Notificações são "best effort" — nunca devem travar a aplicação
            logger.warning("Falha ao enviar notificação '%s': %s", title, exc)
            return False

    # -----------------------------------------------------------------
    # Notificações de negócio
    # -----------------------------------------------------------------

    def notify_darf_due(self, month_label: str, amount: Decimal) -> bool:
        """
        Alerta de DARF de IR sobre renda variável pendente.

        Disparado quando há imposto devido no mês (vendas > R$20k com lucro).
        """
        key = f"darf:{month_label}"
        return self.send(
            title="DARF de IR pendente",
            message=(
                f"Você tem IR de R${amount:,.2f} devido em {month_label}.\n"
                "Emita o DARF pelo SICALC ou portal da Receita Federal."
            ),
            key=key,
        )

    def notify_invoice_due(
        self,
        card_name: str,
        amount: Decimal,
        due_date: date,
        days_ahead: int,
    ) -> bool:
        """
        Alerta de fatura de cartão prestes a vencer.

        days_ahead: quantos dias faltam para o vencimento.
        """
        key = f"invoice:{card_name}:{due_date.isoformat()}"
        if days_ahead <= 1:
            urgency = "HOJE"
        elif days_ahead == 2:
            urgency = "AMANHÃ"
        else:
            urgency = f"em {days_ahead} dias"

        return self.send(
            title=f"Fatura vence {urgency}",
            message=(
                f"{card_name}: R${amount:,.2f} vence em "
                f"{due_date.strftime('%d/%m/%Y')}."
            ),
            key=key,
        )

    def notify_goal_reached(self, goal_name: str, target: Decimal) -> bool:
        """
        Celebra o atingimento de um objetivo financeiro.
        """
        key = f"goal:{goal_name}"
        return self.send(
            title="🎉 Objetivo atingido!",
            message=(
                f"Parabéns! Você atingiu o objetivo '{goal_name}' "
                f"de R${target:,.2f}."
            ),
            key=key,
        )

    def notify_budget_exceeded(
        self,
        category: str,
        spent: Decimal,
        limit: Decimal,
    ) -> bool:
        """
        Alerta de orçamento excedido em uma categoria.
        """
        excess = spent - limit
        key = f"budget:{category}:{date.today().strftime('%Y-%m')}"
        return self.send(
            title=f"Orçamento de {category} excedido",
            message=(
                f"Você gastou R${spent:,.2f} em {category} "
                f"(limite: R${limit:,.2f}, excesso: R${excess:,.2f})."
            ),
            key=key,
        )

    def notify_low_liquidity(self, current: Decimal, minimum: Decimal) -> bool:
        """
        Alerta de liquidez imediata (D+0) abaixo do mínimo configurado.
        """
        key = f"liquidity:{date.today().strftime('%Y-%m')}"
        return self.send(
            title="Liquidez imediata baixa",
            message=(
                f"Seu saldo disponível em D+0 é R${current:,.2f}, "
                f"abaixo do mínimo de R${minimum:,.2f}."
            ),
            key=key,
        )

    def notify_recurring_due(self, name: str, amount: Decimal, days: int) -> bool:
        """
        Lembrete de gasto recorrente vencendo em breve.
        """
        key = f"recurring:{name}:{date.today().strftime('%Y-%m')}"
        label = "hoje" if days == 0 else f"em {days} dia(s)"
        return self.send(
            title="Gasto recorrente vencendo",
            message=f"{name}: R${amount:,.2f} vence {label}.",
            key=key,
        )
