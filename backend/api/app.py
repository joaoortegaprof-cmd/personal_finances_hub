"""
Aplicação FastAPI principal do FinanceHub.

Ciclo de vida:
    startup  → init_db() cria as tabelas SQLite que ainda não existem
    shutdown → conexões do pool são fechadas pelo SQLAlchemy automaticamente

Routers registrados:
    /accounts    → CRUD de contas e cartões
    /transactions → CRUD de lançamentos + resumo mensal
    /assets      → CRUD de ativos e operações
    /portfolio   → visão consolidada e liquidez da carteira
    /dashboard   → patrimônio, saúde financeira e alertas
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.routes.accounts import router as accounts_router
from backend.api.routes.assets import assets_router, portfolio_router
from backend.api.routes.cards import router as cards_router
from backend.api.routes.dashboard import router as dashboard_router
from backend.api.routes.debts import router as debts_router
from backend.api.routes.dividends import router as dividends_router
from backend.api.routes.market import router as market_router
from backend.api.routes.simulation import router as simulation_router
from backend.api.routes.tax import router as tax_router
from backend.api.routes.transactions import router as transactions_router
from backend.core.config import settings
from backend.core.database import init_db

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Lifespan — startup e shutdown
# -----------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa o banco de dados antes de aceitar requisições."""
    logger.info("Iniciando FinanceHub API — criando tabelas se necessário...")
    await init_db()
    logger.info("Banco de dados pronto.")
    yield
    logger.info("Encerrando FinanceHub API.")


# -----------------------------------------------------------------
# Criação da aplicação
# -----------------------------------------------------------------

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="API interna do FinanceHub — gestão financeira pessoal.",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)


# -----------------------------------------------------------------
# Middlewares
# -----------------------------------------------------------------

# CORS liberado para localhost — a interface PyQt6 roda localmente.
# Em produção, restrinja allow_origins à origem real do frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------
# Routers
# -----------------------------------------------------------------

app.include_router(accounts_router)
app.include_router(cards_router)
app.include_router(transactions_router)
app.include_router(assets_router)
app.include_router(portfolio_router)
app.include_router(dashboard_router)
app.include_router(market_router)
app.include_router(debts_router)
app.include_router(dividends_router)
app.include_router(tax_router)
app.include_router(simulation_router)


# -----------------------------------------------------------------
# Health check
# -----------------------------------------------------------------

@app.get("/health", tags=["Sistema"], summary="Verifica se a API está no ar")
async def health_check():
    """Retorna status OK e a versão atual da API."""
    return {"status": "ok", "version": settings.APP_VERSION}


# -----------------------------------------------------------------
# Handler global de exceções
# -----------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Captura qualquer exceção não tratada e retorna 500 sem vazar stack trace.
    HTTPException não passa por aqui — o FastAPI a trata antes.
    """
    logger.exception("Erro não tratado em %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Erro interno do servidor.",
            "type": type(exc).__name__,
        },
    )
