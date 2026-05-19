"""
Configurações centrais da aplicação FinanceHub.

Todas as variáveis são lidas automaticamente do arquivo .env na raiz do projeto.
Variáveis de ambiente do sistema sobrescrevem o .env (útil para CI/produção).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # -----------------------------------------------------------------
    # Identidade da aplicação
    # -----------------------------------------------------------------

    # Nome exibido em logs, títulos de janela e cabeçalhos da API
    APP_NAME: str = "FinanceHub"

    # Versão semântica — incrementar a cada release publicado
    APP_VERSION: str = "0.1.0"

    # -----------------------------------------------------------------
    # Servidor FastAPI (API interna)
    # -----------------------------------------------------------------

    # Endereço de bind do servidor Uvicorn.
    # Em produção use "0.0.0.0" para aceitar conexões externas;
    # "127.0.0.1" restringe ao localhost (recomendado em dev).
    API_HOST: str = "127.0.0.1"

    # Porta TCP em que o FastAPI será exposto.
    # Escolhida fora das portas reservadas (<1024) e do comum 8000/8080
    # para evitar conflito com outros serviços locais.
    API_PORT: int = 8765

    # -----------------------------------------------------------------
    # Banco de dados
    # -----------------------------------------------------------------

    # URL de conexão do SQLAlchemy.
    # Padrão SQLite para desenvolvimento local — nenhuma instalação extra necessária.
    # Para PostgreSQL em produção, substitua por:
    #   postgresql+asyncpg://usuario:senha@host:5432/finance_hub
    DATABASE_URL: str = "sqlite+aiosqlite:///./finance_hub.db"

    # -----------------------------------------------------------------
    # Segurança / JWT
    # -----------------------------------------------------------------

    # Chave usada para assinar tokens JWT.
    # NUNCA deixe o valor padrão em produção — gere uma chave aleatória com:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str = "TROQUE_ESTA_CHAVE_EM_PRODUCAO"

    # Algoritmo de assinatura do JWT (HS256 é o padrão mais comum)
    JWT_ALGORITHM: str = "HS256"

    # Tempo de expiração do token de acesso em minutos (padrão: 8 horas)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # -----------------------------------------------------------------
    # Cotações de mercado
    # -----------------------------------------------------------------

    # Intervalo em segundos entre cada atualização automática de cotações.
    # 300 s = 5 minutos — equilíbrio entre dados frescos e respeito aos
    # limites de requisição das fontes (yfinance, python-bcb).
    MARKET_DATA_REFRESH_INTERVAL: int = 300

    # -----------------------------------------------------------------
    # Localização e moeda
    # -----------------------------------------------------------------

    # Moeda padrão para exibição de valores monetários (ISO 4217)
    DEFAULT_CURRENCY: str = "BRL"

    # Locale usado para formatar números, datas e moeda na interface.
    # pt_BR → "R$ 1.234,56"  |  en_US → "$ 1,234.56"
    DEFAULT_LOCALE: str = "pt_BR"

    # -----------------------------------------------------------------
    # Depuração
    # -----------------------------------------------------------------

    # Em modo debug:
    #   - FastAPI expõe /docs e /redoc
    #   - SQLAlchemy loga todas as queries SQL no terminal
    #   - Erros retornam stack traces completos
    # Defina DEBUG=false em produção.
    DEBUG: bool = True

    # -----------------------------------------------------------------
    # Configuração do pydantic-settings
    # -----------------------------------------------------------------

    model_config = SettingsConfigDict(
        # Caminho do arquivo .env relativo ao diretório de trabalho
        env_file=".env",
        # Ignora variáveis de ambiente que não existam no modelo
        extra="ignore",
        # Leitura sem distinção entre maiúsculas/minúsculas (DATABASE_URL == database_url)
        case_sensitive=False,
    )


# Instância global — importe `settings` diretamente nos outros módulos:
#   from backend.core.config import settings
settings = Settings()
