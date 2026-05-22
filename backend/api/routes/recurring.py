"""
Endpoints de gastos recorrentes (assinaturas e contratos periódicos).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.recurring import (
    RecurringExpenseCreate,
    RecurringExpenseOut,
    RecurringExpenseUpdate,
    RecurringSummaryOut,
)
from backend.core.database import get_db
from backend.repositories.recurring_repository import RecurringRepository

router = APIRouter(prefix="/recurring-expenses", tags=["Recorrentes"])


@router.get("", response_model=list[RecurringExpenseOut])
async def list_recurring(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    repo = RecurringRepository(db)
    return await repo.get_all(active_only=active_only)


@router.get("/summary", response_model=RecurringSummaryOut)
async def recurring_summary(
    upcoming_days: int = 7,
    db: AsyncSession = Depends(get_db),
):
    repo = RecurringRepository(db)
    expenses   = await repo.get_all(active_only=True)
    monthly    = await repo.monthly_total()
    upcoming   = await repo.get_upcoming(days_ahead=upcoming_days)
    overdue    = await repo.get_overdue()

    return RecurringSummaryOut(
        monthly_total=monthly,
        annual_total=monthly * 12,
        active_count=len(expenses),
        upcoming=upcoming,
        overdue=overdue,
    )


@router.post("", response_model=RecurringExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_recurring(
    body: RecurringExpenseCreate,
    db: AsyncSession = Depends(get_db),
):
    repo = RecurringRepository(db)
    return await repo.create(body.model_dump())


@router.put("/{expense_id}", response_model=RecurringExpenseOut)
async def update_recurring(
    expense_id: int,
    body: RecurringExpenseUpdate,
    db: AsyncSession = Depends(get_db),
):
    repo    = RecurringRepository(db)
    expense = await repo.get_by_id(expense_id)
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto recorrente não encontrado.")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    return await repo.update(expense, data)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recurring(
    expense_id: int,
    db: AsyncSession = Depends(get_db),
):
    repo    = RecurringRepository(db)
    expense = await repo.get_by_id(expense_id)
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto recorrente não encontrado.")
    await repo.delete(expense)
