"""
Repositório de gastos recorrentes.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.recurring import MONTHLY_FACTOR, RecurringExpense


class RecurringRepository:

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self, *, active_only: bool = True) -> list[RecurringExpense]:
        stmt = select(RecurringExpense).order_by(RecurringExpense.next_due_date)
        if active_only:
            stmt = stmt.where(RecurringExpense.is_active == True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, expense_id: int) -> RecurringExpense | None:
        return await self._session.get(RecurringExpense, expense_id)

    async def create(self, data: dict) -> RecurringExpense:
        expense = RecurringExpense(**data)
        self._session.add(expense)
        await self._session.flush()
        await self._session.refresh(expense)
        return expense

    async def update(self, expense: RecurringExpense, data: dict) -> RecurringExpense:
        for key, value in data.items():
            if value is not None:
                setattr(expense, key, value)
        await self._session.flush()
        await self._session.refresh(expense)
        return expense

    async def delete(self, expense: RecurringExpense) -> None:
        """Soft delete: marca is_active = False."""
        expense.is_active = False
        await self._session.flush()

    async def get_upcoming(self, days_ahead: int = 7) -> list[RecurringExpense]:
        """Retorna gastos com vencimento nos próximos `days_ahead` dias."""
        today = date.today()
        limit = today + timedelta(days=days_ahead)
        stmt = (
            select(RecurringExpense)
            .where(
                RecurringExpense.is_active == True,
                RecurringExpense.next_due_date >= today,
                RecurringExpense.next_due_date <= limit,
            )
            .order_by(RecurringExpense.next_due_date)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_overdue(self) -> list[RecurringExpense]:
        """Retorna gastos com next_due_date anterior a hoje."""
        stmt = (
            select(RecurringExpense)
            .where(
                RecurringExpense.is_active == True,
                RecurringExpense.next_due_date < date.today(),
            )
            .order_by(RecurringExpense.next_due_date)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def monthly_total(self) -> Decimal:
        """Soma o custo mensal equivalente de todos os gastos ativos."""
        expenses = await self.get_all(active_only=True)
        return sum(
            e.amount * MONTHLY_FACTOR.get(e.periodicity, Decimal("1"))
            for e in expenses
        )
