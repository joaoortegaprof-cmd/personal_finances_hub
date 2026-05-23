"""
Testes de integração para os endpoints de contas (/accounts).

Usa o AsyncClient do httpx com a aplicação FastAPI montada em memória
(sem precisar de servidor uvicorn rodando).

Cobertura:
  - POST /accounts     → cria conta, retorna 201 com dados
  - GET /accounts/{id} → retorna a conta pelo ID
  - GET /accounts/{id}/balance → retorna saldo inicial correto
  - DELETE /accounts/{id}      → remove conta, GET retorna 404
  - GET /accounts/{id} com ID inválido → retorna 404
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.app import app
from backend.core.database import get_db


# ─────────────────────────────────────────────────────────────────
# Override de dependência: injeta o db_session do teste
# ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def api_client(db_session: AsyncSession):
    """
    Cliente HTTP assíncrono conectado ao FastAPI via ASGI (sem I/O de rede).

    Sobrescreve get_db para usar a sessão de teste com rollback.
    """
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # Remove o override para não afetar outros testes
    app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────
# Payload padrão para criar conta
# ─────────────────────────────────────────────────────────────────

ACCOUNT_PAYLOAD = {
    "name": "Conta Integração",
    "bank_name": "Banco Integração",
    "account_type": "corrente",
    "currency": "BRL",
    "initial_balance": "5000.00",
    "initial_balance_date": "2026-01-01",
}


# ─────────────────────────────────────────────────────────────────
# Testes
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_account_returns_201(api_client: AsyncClient):
    """POST /accounts → 201 com id, name e bank_name corretos."""
    response = await api_client.post("/accounts", json=ACCOUNT_PAYLOAD)

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Conta Integração"
    assert data["bank_name"] == "Banco Integração"
    assert data["account_type"] == "corrente"
    assert "id" in data


@pytest.mark.asyncio
async def test_get_account_by_id(api_client: AsyncClient):
    """Cria uma conta e recupera pelo ID → dados batem."""
    create_resp = await api_client.post("/accounts", json=ACCOUNT_PAYLOAD)
    account_id = create_resp.json()["id"]

    get_resp = await api_client.get(f"/accounts/{account_id}")

    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == account_id
    assert get_resp.json()["name"] == "Conta Integração"


@pytest.mark.asyncio
async def test_get_account_balance(api_client: AsyncClient):
    """
    Conta com saldo inicial R$5.000, sem transações.
    GET /accounts/{id}/balance → balance = 5000.00.
    """
    create_resp = await api_client.post("/accounts", json=ACCOUNT_PAYLOAD)
    account_id = create_resp.json()["id"]

    balance_resp = await api_client.get(f"/accounts/{account_id}/balance")

    assert balance_resp.status_code == 200
    data = balance_resp.json()
    assert data["account_id"] == account_id
    assert Decimal(str(data["balance"])) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_delete_account(api_client: AsyncClient):
    """DELETE /accounts/{id} → 204; GET subsequente retorna 404."""
    create_resp = await api_client.post("/accounts", json=ACCOUNT_PAYLOAD)
    account_id = create_resp.json()["id"]

    delete_resp = await api_client.delete(f"/accounts/{account_id}")
    assert delete_resp.status_code == 204

    get_resp = await api_client.get(f"/accounts/{account_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_get_nonexistent_account_returns_404(api_client: AsyncClient):
    """GET /accounts/99999 → 404 (conta inexistente)."""
    response = await api_client.get("/accounts/99999")

    assert response.status_code == 404
    assert "não encontrada" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_accounts(api_client: AsyncClient):
    """
    Cria 2 contas → GET /accounts retorna lista com pelo menos as 2 criadas.
    """
    payload_1 = {**ACCOUNT_PAYLOAD, "name": "Conta A"}
    payload_2 = {**ACCOUNT_PAYLOAD, "name": "Conta B"}

    await api_client.post("/accounts", json=payload_1)
    await api_client.post("/accounts", json=payload_2)

    list_resp = await api_client.get("/accounts")

    assert list_resp.status_code == 200
    names = [a["name"] for a in list_resp.json()]
    assert "Conta A" in names
    assert "Conta B" in names


@pytest.mark.asyncio
async def test_create_account_missing_fields_returns_422(api_client: AsyncClient):
    """Payload sem campos obrigatórios → 422 Unprocessable Entity."""
    response = await api_client.post("/accounts", json={"name": "Incompleta"})

    assert response.status_code == 422
