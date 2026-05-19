"""
Repositório base genérico do FinanceHub.

O padrão Repository isola a camada de acesso a dados dos endpoints da API.
Em vez de espalhar queries SQLAlchemy por toda a aplicação, cada operação
de banco fica aqui — fácil de testar, trocar ou otimizar de forma isolada.

Como usar em um repositório específico:
    class AccountRepository(BaseRepository[Account]):
        def __init__(self, session: AsyncSession) -> None:
            super().__init__(Account, session)
        # ... métodos específicos de Account

Como injetar via FastAPI:
    async def get_account_repo(db: AsyncSession = Depends(get_db)):
        return AccountRepository(db)

    @router.get("/{id}")
    async def get(id: int, repo: AccountRepository = Depends(get_account_repo)):
        return await repo.get_by_id(id)
"""

from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import Base

# TypeVar ligado a Base garante que só subclasses de Base (models do SQLAlchemy)
# podem ser usadas como parâmetro T. O type checker valida isso em tempo de análise.
T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    """
    Repositório genérico com operações CRUD reutilizáveis.

    Parâmetros de tipo:
        T — o model SQLAlchemy que este repositório gerencia.

    A sessão é injetada no construtor (não por método) para que todas as
    operações de uma requisição HTTP compartilhem a mesma transação —
    garantindo que um conjunto de mudanças seja confirmado ou desfeito junto.
    """

    def __init__(self, model: type[T], session: AsyncSession) -> None:
        # Guardamos a classe do model (não uma instância) para construir
        # queries genéricas: select(self._model), session.get(self._model, id) etc.
        self._model = model
        self._session = session

    # -----------------------------------------------------------------
    # Leitura por ID
    # -----------------------------------------------------------------

    async def get_by_id(self, record_id: int) -> T | None:
        """
        Retorna o registro com a PK fornecida ou None se não existir.

        Usa session.get() em vez de select().where(id == X) porque o
        SQLAlchemy mantém um cache de identidade (identity map) na sessão:
        se o objeto já foi carregado na mesma sessão, ele é retornado
        imediatamente sem bater no banco.
        """
        return await self._session.get(self._model, record_id)

    # -----------------------------------------------------------------
    # Listagem com paginação
    # -----------------------------------------------------------------

    async def get_all(self, *, limit: int = 100, offset: int = 0) -> list[T]:
        """
        Retorna uma página de registros ordenados pela PK.

        limit e offset implementam paginação simples — suficiente para
        listas de contas e categorias. Para volumes maiores prefira
        cursor-based pagination (ex: WHERE id > last_seen_id).
        """
        result = await self._session.execute(
            select(self._model)
            .order_by(self._model.id)  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Criação
    # -----------------------------------------------------------------

    async def create(self, obj: T) -> T:
        """
        Persiste um novo objeto no banco e retorna-o com o ID gerado.

        O chamador cria a instância do model e passa aqui — o repositório
        não decide quais campos preencher, apenas persiste.

        flush() envia o INSERT ao banco dentro da transação atual, mas
        não faz commit. Assim o ID autoincrement fica disponível para
        uso imediato (ex: criar relacionamentos) sem commitar ainda.
        """
        self._session.add(obj)
        await self._session.flush()
        # refresh() recarrega o objeto do banco para garantir que campos
        # com server_default (created_at, updated_at) estejam preenchidos.
        await self._session.refresh(obj)
        return obj

    # -----------------------------------------------------------------
    # Atualização parcial
    # -----------------------------------------------------------------

    async def update(self, record_id: int, data: dict[str, Any]) -> T | None:
        """
        Atualiza campos de um registro existente.

        data é um dicionário de {nome_do_campo: novo_valor} — apenas os
        campos presentes são alterados (patch semântico), sem substituir
        todo o objeto. Campos não informados permanecem intactos.

        Retorna None se o ID não existir.
        """
        obj = await self.get_by_id(record_id)
        if obj is None:
            return None

        for field, value in data.items():
            # setattr é a forma genérica de obj.campo = valor
            # hasattr protege contra campos inexistentes no model
            if hasattr(obj, field):
                setattr(obj, field, value)

        await self._session.flush()
        await self._session.refresh(obj)
        return obj

    # -----------------------------------------------------------------
    # Exclusão
    # -----------------------------------------------------------------

    async def delete(self, record_id: int) -> bool:
        """
        Remove o registro do banco.

        Retorna True se o registro existia e foi deletado, False se não
        foi encontrado. O DELETE em cascata (definido nas FKs dos models)
        é executado automaticamente pelo banco.
        """
        obj = await self.get_by_id(record_id)
        if obj is None:
            return False
        await self._session.delete(obj)
        await self._session.flush()
        return True

    # -----------------------------------------------------------------
    # Verificação de existência
    # -----------------------------------------------------------------

    async def exists(self, record_id: int) -> bool:
        """
        Verifica se um registro com o ID dado existe, sem carregá-lo.

        Mais eficiente que get_by_id() quando você só precisa checar
        existência — emite SELECT 1 ... LIMIT 1 em vez de SELECT *.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(self._model)
            .where(self._model.id == record_id)  # type: ignore[attr-defined]
            .limit(1)
        )
        return (result.scalar_one() or 0) > 0
