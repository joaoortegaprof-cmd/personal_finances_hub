"""
Repositório de transações financeiras.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.transaction import Transaction, TransactionCategory, TransactionType
from backend.repositories.base import BaseRepository


class TransactionRepository(BaseRepository[Transaction]):
    """
    Operações de banco relacionadas a lançamentos de receita, despesa
    e transferência.

    Herda get_by_id, get_all, create, update, delete e exists do BaseRepository.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Transaction, session)

    # -----------------------------------------------------------------
    # Filtro por período
    # -----------------------------------------------------------------

    async def get_by_period(
        self,
        start_date: date,
        end_date: date,
        *,
        account_id: int | None = None,
    ) -> list[Transaction]:
        """
        Lista transações cujo transaction_date está dentro do intervalo
        [start_date, end_date] (ambos inclusive).

        account_id é opcional — quando informado, restringe ao histórico
        de uma única conta. Omitido, retorna todas as contas.

        Ordenado por data crescente para facilitar relatórios temporais.
        """
        stmt = (
            select(Transaction)
            .where(
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
            )
            .order_by(Transaction.transaction_date, Transaction.id)
        )

        if account_id is not None:
            stmt = stmt.where(Transaction.account_id == account_id)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Filtro por categoria
    # -----------------------------------------------------------------

    async def get_by_category(
        self,
        category: TransactionCategory,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Transaction]:
        """
        Lista transações de uma categoria específica.

        start_date e end_date são opcionais — quando fornecidos, adicionam
        filtro temporal (útil para o breakdown mensal de gastos por categoria).
        """
        stmt = (
            select(Transaction)
            .where(Transaction.category == category)
            .order_by(Transaction.transaction_date.desc())
        )

        if start_date is not None:
            stmt = stmt.where(Transaction.transaction_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(Transaction.transaction_date <= end_date)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Filtro por conta
    # -----------------------------------------------------------------

    async def get_by_account(
        self,
        account_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Transaction]:
        """
        Retorna as transações de uma conta, da mais recente para a mais antiga.

        Paginado porque uma conta corrente ativa pode ter centenas de
        lançamentos por mês — não faz sentido carregar todos de uma vez.
        """
        result = await self._session.execute(
            select(Transaction)
            .where(Transaction.account_id == account_id)
            .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Totais de receitas e despesas
    # -----------------------------------------------------------------

    async def sum_income_and_expense(
        self,
        start_date: date,
        end_date: date,
        *,
        account_id: int | None = None,
    ) -> dict[str, Decimal]:
        """
        Calcula o total de receitas e despesas em um período.

        Retorna:
            {
                "income":  Decimal — soma das receitas,
                "expense": Decimal — soma das despesas (valor positivo),
                "balance": Decimal — income - expense (pode ser negativo),
            }

        Executa uma única query com SUM condicional em vez de duas queries
        separadas — mais eficiente, especialmente com filtro de conta.
        """
        # SUM parcial com FILTER equivale a:
        #   SELECT SUM(amount) FILTER (WHERE type = 'receita') AS income,
        #          SUM(amount) FILTER (WHERE type = 'despesa') AS expense
        # func.coalesce garante 0 quando não há registros.
        # Despesa = DEBIT + CREDIT + INVESTMENT (excluindo INVOICE_PAYMENT
        # para não duplicar com CREDIT_EXPENSE já contabilizado)
        expense_types = [
            TransactionType.DEBIT_EXPENSE,
            TransactionType.CREDIT_EXPENSE,
            TransactionType.INVESTMENT,
        ]
        stmt = select(
            func.coalesce(
                func.sum(Transaction.amount).filter(
                    Transaction.transaction_type == TransactionType.INCOME
                ),
                Decimal("0.00"),
            ).label("income"),
            func.coalesce(
                func.sum(Transaction.amount).filter(
                    Transaction.transaction_type.in_(expense_types)
                ),
                Decimal("0.00"),
            ).label("expense"),
        ).where(
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
        )

        if account_id is not None:
            stmt = stmt.where(Transaction.account_id == account_id)

        row = (await self._session.execute(stmt)).one()
        income: Decimal = row.income
        expense: Decimal = row.expense
        return {
            "income": income,
            "expense": expense,
            "balance": income - expense,
        }

    # -----------------------------------------------------------------
    # Gastos por categoria (para análise de padrões)
    # -----------------------------------------------------------------

    async def sum_expense_by_category(
        self, start_date: date, end_date: date
    ) -> dict[str, float]:
        """
        Retorna o total gasto por categoria no período.
        Considera DEBIT_EXPENSE e CREDIT_EXPENSE (excluindo INVESTMENT).
        """
        expense_types = [TransactionType.DEBIT_EXPENSE, TransactionType.CREDIT_EXPENSE]
        stmt = (
            select(Transaction.category, func.sum(Transaction.amount).label("total"))
            .where(
                Transaction.transaction_type.in_(expense_types),
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
            )
            .group_by(Transaction.category)
        )
        rows = (await self._session.execute(stmt)).all()
        return {str(row.category): float(row.total) for row in rows}

    # -----------------------------------------------------------------
    # Transações recorrentes
    # -----------------------------------------------------------------

    async def get_recurring(
        self,
        *,
        account_id: int | None = None,
    ) -> list[Transaction]:
        """
        Lista transações marcadas como recorrentes (is_recurring=True).

        Usadas para:
          - Projetar fluxo de caixa futuro (assumindo que se repetem).
          - Alertar quando uma recorrência esperada não apareceu no mês.

        account_id opcional para filtrar por conta específica.
        """
        stmt = (
            select(Transaction)
            .where(Transaction.is_recurring.is_(True))
            .order_by(Transaction.transaction_date.desc())
        )

        if account_id is not None:
            stmt = stmt.where(Transaction.account_id == account_id)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())
