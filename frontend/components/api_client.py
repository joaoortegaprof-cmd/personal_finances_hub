"""
Cliente HTTP para comunicação com a API interna do FinanceHub.

Design: métodos síncronos dentro de uma classe leve — sem gerenciar event loop
aqui porque o PyQt6 já usa QThread para deslocar chamadas de rede para uma
thread secundária (ver DashboardWorker em dashboard.py). Manter o cliente
síncrono simplifica o tratamento de erros e evita conflitos com o loop Qt.

Base URL configurável via parâmetro (padrão: valor de API_HOST/API_PORT do
backend/core/config.py).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx


class ApiError(Exception):
    """Erro de negócio vindo da API (4xx / 5xx)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    """
    Ponto único de acesso à API REST do FinanceHub.

    Cada método mapeia para um grupo de endpoints e retorna o JSON desserializado.
    Em caso de falha, levanta ApiError com mensagem amigável para exibição na UI.
    """

    # Timeout padrão: 10 s de conexão + 30 s de leitura.
    # Valores generosos porque o SQLite pode demorar em consultas mais pesadas.
    _DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)

    def __init__(self, base_url: str = "http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Executa GET e retorna o JSON. Levanta ApiError em qualquer falha."""
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self._DEFAULT_TIMEOUT) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                return response.json()

        except httpx.ConnectError:
            raise ApiError(
                "Não foi possível conectar à API. "
                "Verifique se o servidor FinanceHub está em execução."
            )
        except httpx.TimeoutException:
            raise ApiError(
                "A API demorou demais para responder. "
                "Tente novamente em alguns instantes."
            )
        except httpx.HTTPStatusError as exc:
            # Tenta extrair a mensagem de detalhe do FastAPI
            try:
                detail = exc.response.json().get("detail", str(exc))
            except Exception:
                detail = str(exc)
            raise ApiError(detail, status_code=exc.response.status_code)
        except httpx.RequestError as exc:
            raise ApiError(f"Erro de rede: {exc}")

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def get_dashboard(self) -> dict:
        """
        Retorna a visão consolidada do patrimônio:
          - net_worth: patrimônio líquido, ativos e passivos
          - health_score: score de saúde financeira (0-100) e componentes
          - monthly_summary: receitas, despesas, saldo e taxa de poupança do mês
        """
        return self._get("/dashboard")

    def get_alerts(
        self,
        invoice_due_days: int = 3,
        maturity_days_ahead: int = 30,
        savings_goal_pct: float = 20.0,
    ) -> dict:
        """
        Retorna alertas ativos ordenados por prioridade (ALTA → MÉDIA → BAIXA).

        Parâmetros configuram sensibilidade de cada tipo de alerta:
          - invoice_due_days: dias de antecedência para alertas de fatura
          - maturity_days_ahead: horizonte de vencimentos de renda fixa
          - savings_goal_pct: meta de taxa de poupança mensal em %
        """
        return self._get(
            "/dashboard/alerts",
            params={
                "invoice_due_days": invoice_due_days,
                "maturity_days_ahead": maturity_days_ahead,
                "savings_goal_pct": savings_goal_pct,
            },
        )

    # ------------------------------------------------------------------
    # Contas
    # ------------------------------------------------------------------

    def get_accounts(self) -> list[dict]:
        """Lista todas as contas bancárias e carteiras digitais."""
        return self._get("/accounts")

    def get_account_balance(self, account_id: int) -> dict:
        """Retorna o saldo atual de uma conta específica."""
        return self._get(f"/accounts/{account_id}/balance")

    # ------------------------------------------------------------------
    # Transações
    # ------------------------------------------------------------------

    def get_transactions(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        category: str | None = None,
        account_id: int | None = None,
    ) -> list[dict]:
        """
        Lista lançamentos com filtros opcionais.

        Se start_date ou end_date for informado, ambos são obrigatórios.
        """
        params: dict[str, Any] = {}
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()
        if category:
            params["category"] = category
        if account_id is not None:
            params["account_id"] = account_id
        return self._get("/transactions", params=params or None)

    def get_monthly_summary(self, reference_date: date | None = None) -> dict:
        """Retorna o resumo mensal (receitas, despesas, saldo, taxa de poupança)."""
        params = {"reference_date": reference_date.isoformat()} if reference_date else None
        return self._get("/transactions/summary", params=params)

    # ------------------------------------------------------------------
    # Carteira de investimentos
    # ------------------------------------------------------------------

    def get_portfolio_summary(self) -> dict:
        """
        Retorna o custo investido agrupado por tipo de ativo
        (ação, FII, CDB, Tesouro etc.) e o total investido.
        """
        return self._get("/portfolio/summary")

    def get_liquidity(self) -> dict:
        """
        Retorna o breakdown de liquidez da carteira por janela:
          D+0 (conta/carteira), D+1 (Tesouro Selic / CDB diário),
          D+2 (ações / FIIs) e vencimento (CDB sem liquidez / Tesouro pré).
        """
        return self._get("/portfolio/liquidity")

    def get_assets(self) -> list[dict]:
        """Lista todos os ativos cadastrados."""
        return self._get("/assets")

    # ------------------------------------------------------------------
    # Sistema
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        """Verifica se a API está no ar. Útil no startup da janela principal."""
        return self._get("/health")
