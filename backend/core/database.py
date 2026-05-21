"""
Camada de acesso ao banco de dados do FinanceHub.

Usa SQLAlchemy 2.0 no modo totalmente assíncrono (async/await), o que permite
que o FastAPI processe outras requisições enquanto aguarda respostas do banco —
fundamental para uma API responsiva mesmo com queries mais lentas.

Fluxo de vida de uma requisição:
    1. FastAPI recebe a requisição
    2. get_db() abre uma sessão dedicada para aquela requisição
    3. O endpoint executa queries usando essa sessão
    4. get_db() confirma (commit) ou desfaz (rollback) e fecha a sessão
"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.core.config import settings


# -----------------------------------------------------------------
# Motor assíncrono (engine)
# -----------------------------------------------------------------
# O engine é o ponto de entrada do SQLAlchemy para o banco.
# Ele gerencia o pool de conexões — reaproveita conexões abertas em
# vez de criar uma nova a cada query (muito mais eficiente).
#
# create_async_engine → versão assíncrona de create_engine.
# O prefixo "sqlite+aiosqlite://" diz ao SQLAlchemy para usar o
# driver aiosqlite (async) em vez do sqlite3 padrão (síncrono).
#
# echo=settings.DEBUG → em modo debug, toda query SQL gerada pelo
# SQLAlchemy é impressa no terminal, ótimo para aprendizado e debug.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    # pool_pre_ping verifica se a conexão ainda está ativa antes de
    # reutilizá-la do pool — evita erros silenciosos após inatividade.
    pool_pre_ping=True,
)


# -----------------------------------------------------------------
# Fábrica de sessões (session factory)
# -----------------------------------------------------------------
# Uma Session é a "unidade de trabalho" do SQLAlchemy: ela rastreia
# quais objetos foram carregados/criados/alterados e os sincroniza
# com o banco em um único commit.
#
# async_sessionmaker cria sessões assíncronas vinculadas ao engine.
# Parâmetros relevantes:
#   expire_on_commit=False → após um commit, os atributos dos objetos
#   permanecem acessíveis sem precisar de nova query ao banco.
#   Sem isso, acessar obj.campo depois do commit levantaria um erro
#   "MissingGreenlet" no contexto assíncrono.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# -----------------------------------------------------------------
# Base declarativa
# -----------------------------------------------------------------
# Todos os models (tabelas) do projeto herdam de Base.
# O SQLAlchemy usa essa herança para:
#   - Registrar os models no metadata (mapa de tabelas)
#   - Gerar o esquema SQL via Base.metadata.create_all()
#   - Resolver relacionamentos entre tabelas
#
# Exemplo de uso em um model:
#   class Transaction(Base):
#       __tablename__ = "transactions"
#       id: Mapped[int] = mapped_column(primary_key=True)
class Base(DeclarativeBase):
    pass


# -----------------------------------------------------------------
# Dependency injection — get_db
# -----------------------------------------------------------------
# FastAPI possui um sistema de "dependências": funções que são
# executadas antes do endpoint e injetam valores como parâmetros.
#
# get_db é um gerador assíncrono (usa "yield") que:
#   1. Abre uma nova sessão para cada requisição HTTP
#   2. Fornece a sessão ao endpoint via "yield"
#   3. Após o endpoint terminar, confirma as alterações (commit)
#      ou, se ocorreu uma exceção, desfaz tudo (rollback)
#   4. Garante que a sessão seja fechada (finally) — nunca vaza conexões
#
# Uso em um endpoint FastAPI:
#   @router.get("/transactions")
#   async def list_transactions(db: AsyncSession = Depends(get_db)):
#       result = await db.execute(select(Transaction))
#       return result.scalars().all()
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# -----------------------------------------------------------------
# Inicialização do banco
# -----------------------------------------------------------------
# Chamada uma única vez na inicialização da aplicação (em main.py).
# Percorre todos os models registrados em Base.metadata e cria as
# tabelas que ainda não existem — não apaga dados existentes.
#
# Em produção, prefira Alembic para migrações controladas.
# init_db é conveniente apenas em desenvolvimento para subir o
# esquema sem precisar criar migrations a cada mudança.
async def init_db() -> None:
    async with engine.begin() as conn:
        # run_sync executa a função síncrona create_all() dentro do
        # contexto assíncrono — necessário pois a API de metadata
        # do SQLAlchemy ainda é síncrona.
        await conn.run_sync(Base.metadata.create_all)

        # Migração incremental: adiciona is_emergency_fund à tabela assets
        # se a coluna ainda não existir (banco pré-existente).
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM pragma_table_info('assets') "
                "WHERE name='is_emergency_fund'"
            )
        )
        if result.scalar() == 0:
            await conn.execute(
                text(
                    "ALTER TABLE assets "
                    "ADD COLUMN is_emergency_fund BOOLEAN NOT NULL DEFAULT 0"
                )
            )
