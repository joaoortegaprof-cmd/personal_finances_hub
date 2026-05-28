"""
Alembic env.py — configuração de migrações do FinanceHub.

Importa a Base e todos os models para que o autogenerate detecte
automaticamente novas tabelas, colunas e constraints.

Para criar uma nova migração após alterar um model:
    alembic revision --autogenerate -m "descricao_breve"
    alembic upgrade head
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── Importar Base e todos os models ──────────────────────────────────────────
# É obrigatório importar cada model para que a MetaData da Base
# contenha todas as tabelas durante o autogenerate.

from backend.core.database import Base          # noqa: E402

import backend.models.account      # noqa: F401 — registra: accounts, credit_cards, …
import backend.models.transaction  # noqa: F401 — registra: transactions
import backend.models.asset        # noqa: F401 — registra: assets, asset_positions

# Se novos models forem criados, adicione o import aqui:
# import backend.models.novo_model

# ── Configuração Alembic ──────────────────────────────────────────────────────

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MetaData com todas as tabelas dos models importados acima
target_metadata = Base.metadata


# ── Funções de migração ───────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Modo offline: gera SQL sem conectar ao banco (útil para revisão)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online: conecta ao banco e executa as migrações diretamente."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
