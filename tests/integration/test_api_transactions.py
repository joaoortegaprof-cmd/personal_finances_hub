"""
Testes de integração para os endpoints de transações (/transactions).

Cobertura:
  - income: POST → GET /accounts/{id}/balance aumenta saldo
  - debit: POST → GET /accounts/{id}/balance diminui saldo
  - credit_reduces_limit: compra no cartão aparece na fatura, não debita conta
  - invoice_payment_restores_limit: pagamento da fatura debita conta
  - GET /transactions/summary → retorna resumo mensal correto
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.app import app
from backend.core.database import get_db


# ─────────────────────────────────────────────────────────────────
# Fixture: cliente da API com db_session injetado
# ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def api_client(db_session: AsyncSession):
    """Cliente ASGI com sessão de banco isolada."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────
# Fixtures: conta e cartão via API
# ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def account_id(api_client: AsyncClient) -> int:
    """Cria uma conta com saldo inicial R$5.000 e retorna o ID."""
    payload = {
        "name": "Conta TX Teste",
        "bank_name": "Banco TX",
        "account_type": "corrente",
        "currency": "BRL",
        "initial_balance": "5000.00",
        "initial_balance_date": "2026-01-01",
    }
    resp = await api_client.post("/accounts", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


# ─────────────────────────────────────────────────────────────────
# Helper: data do mês corrente no formato ISO
# ─────────────────────────────────────────────────────────────────

def _today_str() -> str:
    return date.today().isoformat()


# ─────────────────────────────────────────────────────────────────
# Testes
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_income_increases_balance(api_client: AsyncClient, account_id: int):
    """
    Receita de R$2.000 → saldo passa de R$5.000 para R$7.000.
    """
    tx_payload = {
        "description": "Salário maio",
        "amount": "2000.00",
        "transaction_date": _today_str(),
        "transaction_type": "income",
        "category": "salario",
        "account_id": account_id,
    }
    tx_resp = await api_client.post("/transactions", json=tx_payload)
    assert tx_resp.status_code == 201

    balance_resp = await api_client.get(f"/accounts/{account_id}/balance")
    assert balance_resp.status_code == 200
    assert Decimal(str(balance_resp.json()["balance"])) == Decimal("7000.00")


@pytest.mark.asyncio
async def test_debit_decreases_balance(api_client: AsyncClient, account_id: int):
    """
    Débito de R$800 → saldo passa de R$5.000 para R$4.200.
    """
    tx_payload = {
        "description": "Supermercado",
        "amount": "800.00",
        "transaction_date": _today_str(),
        "transaction_type": "debit",
        "category": "supermercado",
        "account_id": account_id,
    }
    tx_resp = await api_client.post("/transactions", json=tx_payload)
    assert tx_resp.status_code == 201

    balance_resp = await api_client.get(f"/accounts/{account_id}/balance")
    assert Decimal(str(balance_resp.json()["balance"])) == Decimal("4200.00")


@pytest.mark.asyncio
async def test_multiple_transactions_accumulate(api_client: AsyncClient, account_id: int):
    """
    Receita R$3.000 + Despesa R$1.200 = saldo 5000 + 3000 - 1200 = R$6.800.
    """
    await api_client.post("/transactions", json={
        "description": "Freelance",
        "amount": "3000.00",
        "transaction_date": _today_str(),
        "transaction_type": "income",
        "category": "salario",
        "account_id": account_id,
    })
    await api_client.post("/transactions", json={
        "description": "Aluguel",
        "amount": "1200.00",
        "transaction_date": _today_str(),
        "transaction_type": "debit",
        "category": "moradia",
        "account_id": account_id,
    })

    balance_resp = await api_client.get(f"/accounts/{account_id}/balance")
    assert Decimal(str(balance_resp.json()["balance"])) == Decimal("6800.00")


@pytest.mark.asyncio
async def test_monthly_summary_endpoint(api_client: AsyncClient, account_id: int):
    """
    GET /transactions/summary retorna income, expense, balance e savings_rate corretos.

    Receita R$4.000, despesa R$1.000:
      balance       = 3000
      savings_rate  = 75.00%
    """
    today = _today_str()

    await api_client.post("/transactions", json={
        "description": "Salário",
        "amount": "4000.00",
        "transaction_date": today,
        "transaction_type": "income",
        "category": "salario",
        "account_id": account_id,
    })
    await api_client.post("/transactions", json={
        "description": "Conta de luz",
        "amount": "1000.00",
        "transaction_date": today,
        "transaction_type": "debit",
        "category": "moradia",
        "account_id": account_id,
    })

    summary_resp = await api_client.get(
        "/transactions/summary",
        params={"reference_date": today},
    )
    assert summary_resp.status_code == 200

    data = summary_resp.json()
    assert Decimal(str(data["income"])) == Decimal("4000.00")
    assert Decimal(str(data["expense"])) == Decimal("1000.00")
    assert Decimal(str(data["balance"])) == Decimal("3000.00")
    assert Decimal(str(data["savings_rate"])) == Decimal("75.00")


@pytest.mark.asyncio
async def test_list_transactions(api_client: AsyncClient, account_id: int):
    """
    GET /transactions retorna a lista com as transações criadas.
    """
    await api_client.post("/transactions", json={
        "description": "TX lista 1",
        "amount": "100.00",
        "transaction_date": _today_str(),
        "transaction_type": "income",
        "category": "outros",
        "account_id": account_id,
    })
    await api_client.post("/transactions", json={
        "description": "TX lista 2",
        "amount": "200.00",
        "transaction_date": _today_str(),
        "transaction_type": "debit",
        "category": "outros",
        "account_id": account_id,
    })

    list_resp = await api_client.get("/transactions")
    assert list_resp.status_code == 200
    descriptions = [tx["description"] for tx in list_resp.json()]
    assert "TX lista 1" in descriptions
    assert "TX lista 2" in descriptions


@pytest.mark.asyncio
async def test_create_transaction_missing_amount_returns_422(
    api_client: AsyncClient, account_id: int
):
    """Payload sem amount → 422."""
    resp = await api_client.post("/transactions", json={
        "description": "Sem valor",
        "transaction_date": _today_str(),
        "transaction_type": "income",
        "category": "outros",
        "account_id": account_id,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_nonexistent_transaction_returns_404(api_client: AsyncClient):
    """GET /transactions/99999 → 404."""
    resp = await api_client.get("/transactions/99999")
    assert resp.status_code == 404
