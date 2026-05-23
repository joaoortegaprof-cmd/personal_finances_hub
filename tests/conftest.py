"""
Fixtures compartilhadas pelo suite de testes do FinanceHub.

Estratégia de banco de dados:
  - Cada teste recebe um banco SQLite em memória completamente isolado.
  - A engine é criada por escopo de sessão (session-scoped) para performance,
    mas a sessão de banco é por teste (function-scoped) com rollback ao final.

Fixtures disponíveis:
  db_session    → AsyncSession com banco em memória, com rollback por teste
  sample_account → Conta corrente com R$5.000 de saldo inicial
  sample_card    → Cartão de crédito com R$8.000 de limite
  sample_asset   → Ativo PETR4 (ação) para testes de investimento
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Importa todos os models para registrar metadados antes do create_all
from backend.core.database import Base
from backend.models.account import Account, AccountType, CreditCard  # noqa: F401
from backend.models.asset import Asset, AssetPosition, AssetType, LiquidityWindow, OperationType  # noqa: F401
from backend.models.transaction import Transaction  # noqa: F401
from backend.models.debt import Debt  # noqa: F401
from backend.models.dividend import Dividend  # noqa: F401
from backend.models.recurring import RecurringExpense  # noqa: F401


# -----------------------------------------------------------------
# Configuração do pytest-asyncio
# -----------------------------------------------------------------

pytest_plugins = ("pytest_asyncio",)


# -----------------------------------------------------------------
# Engine em memória (escopo de módulo para reaproveitar entre testes)
# -----------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
def event_loop():
    """Loop de eventos compartilhado por toda a sessão de testes."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    """
    Engine SQLite em memória com todas as tabelas criadas.

    Escopo 'session' → criada uma vez para toda a bateria de testes.
    O banco em memória é descartado quando o processo termina.
    """
    _engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield _engine

    await _engine.dispose()


# -----------------------------------------------------------------
# Sessão de banco por teste (rollback ao final)
# -----------------------------------------------------------------

@pytest_asyncio.fixture()
async def db_session(engine) -> AsyncSession:
    """
    Sessão de banco de dados com rollback automático após cada teste.

    Cada teste começa com um estado limpo — qualquer dado criado
    durante o teste é descartado ao sair do fixture.
    """
    # Abre uma conexão e inicia uma transação-mãe (savepoint)
    connection = await engine.connect()
    transaction = await connection.begin()

    # Cria a sessão vinculada à conexão com transação ativa
    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    session = session_factory()

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()


# -----------------------------------------------------------------
# Fixture: conta com saldo R$5.000
# -----------------------------------------------------------------

@pytest_asyncio.fixture()
async def sample_account(db_session: AsyncSession) -> Account:
    """
    Conta corrente de teste com R$5.000 de saldo inicial.

    Usada em testes de resumo financeiro, saúde, liquidez etc.
    """
    account = Account(
        name="Conta Teste",
        bank_name="Banco Teste",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Decimal("5000.00"),
        initial_balance_date=date(2026, 1, 1),
    )
    db_session.add(account)
    await db_session.flush()   # gera o ID sem commit definitivo
    await db_session.refresh(account)
    return account


# -----------------------------------------------------------------
# Fixture: cartão de crédito com limite R$8.000
# -----------------------------------------------------------------

@pytest_asyncio.fixture()
async def sample_card(db_session: AsyncSession, sample_account: Account) -> CreditCard:
    """
    Cartão de crédito com R$8.000 de limite vinculado à sample_account.

    Usado em testes de invoice, limite disponível, pagamento de fatura.
    """
    card = CreditCard(
        name="Cartão Teste",
        bank_name="Banco Teste",
        last_four_digits="1234",
        credit_limit=Decimal("8000.00"),
        closing_day=10,
        due_day=15,
        payment_account_id=sample_account.id,
        card_color="#7B61FF",
    )
    db_session.add(card)
    await db_session.flush()
    await db_session.refresh(card)
    return card


# -----------------------------------------------------------------
# Fixture: ativo PETR4
# -----------------------------------------------------------------

@pytest_asyncio.fixture()
async def sample_asset(db_session: AsyncSession) -> Asset:
    """
    Ativo PETR4 (ação, D+2) para testes de investimento, liquidez e IR.
    """
    asset = Asset(
        ticker="PETR4",
        name="Petrobras S.A. PN",
        asset_type=AssetType.STOCK,
        currency="BRL",
        liquidity=LiquidityWindow.D2,
        sector="Energia",
    )
    db_session.add(asset)
    await db_session.flush()
    await db_session.refresh(asset)
    return asset
