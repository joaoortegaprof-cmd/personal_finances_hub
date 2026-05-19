"""
Endpoints de lançamentos financeiros (receitas, despesas e transferências).

Ordem dos endpoints importa: /summary deve vir antes de /{id}
para evitar que a string "summary" seja interpretada como um inteiro.
Como {transaction_id} é tipado como int, FastAPI já resolve corretamente,
mas manter /summary primeiro é uma boa prática explícita.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.transactions import (
    MonthlySummaryOut,
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
)
from backend.core.database import get_db
from backend.models.transaction import Transaction, TransactionCategory
from backend.repositories.transaction_repository import TransactionRepository
from backend.services.financial_summary_service import FinancialSummaryService

router = APIRouter(prefix="/transactions", tags=["Transações"])


@router.get("/summary", response_model=MonthlySummaryOut)
async def get_monthly_summary(
    reference_date: Annotated[
        date | None,
        Query(description="Data dentro do mês desejado. Padrão: mês atual."),
    ] = None,
    db: AsyncSession = Depends(get_db),
):
    """Retorna receitas, despesas, saldo e taxa de poupança do mês."""
    service = FinancialSummaryService(db)
    summary = await service.get_monthly_summary(reference_date)
    ref = reference_date or date.today()
    return MonthlySummaryOut(
        income=summary.income,
        expense=summary.expense,
        balance=summary.balance,
        savings_rate=summary.savings_rate,
        reference_month=f"{ref.month:02d}/{ref.year}",
    )


@router.get("", response_model=list[TransactionOut])
async def list_transactions(
    start_date: Annotated[date | None, Query(description="Início do período (inclusive)")] = None,
    end_date: Annotated[date | None, Query(description="Fim do período (inclusive)")] = None,
    category: Annotated[TransactionCategory | None, Query()] = None,
    account_id: Annotated[int | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Lista transações com filtros opcionais.

    Prioridade dos filtros:
    1. Se start_date + end_date → filtra por período (+ account_id opcional)
    2. Se category → filtra por categoria (start_date/end_date opcionais)
    3. Se account_id → filtra por conta com paginação padrão (100 itens)
    4. Sem filtros → retorna os 100 lançamentos mais recentes por PK
    """
    if bool(start_date) != bool(end_date):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date e end_date devem ser informados juntos",
        )

    repo = TransactionRepository(db)

    if start_date and end_date:
        return await repo.get_by_period(start_date, end_date, account_id=account_id)

    if category:
        return await repo.get_by_category(category, start_date=start_date, end_date=end_date)

    if account_id:
        return await repo.get_by_account(account_id)

    return await repo.get_all()


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def create_transaction(payload: TransactionCreate, db: AsyncSession = Depends(get_db)):
    """Registra um novo lançamento de receita, despesa ou transferência."""
    repo = TransactionRepository(db)
    transaction = Transaction(**payload.model_dump())
    return await repo.create(transaction)


@router.get("/{transaction_id}", response_model=TransactionOut)
async def get_transaction(transaction_id: int, db: AsyncSession = Depends(get_db)):
    """Retorna um lançamento pelo ID."""
    repo = TransactionRepository(db)
    transaction = await repo.get_by_id(transaction_id)
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transação não encontrada")
    return transaction


@router.put("/{transaction_id}", response_model=TransactionOut)
async def update_transaction(
    transaction_id: int,
    payload: TransactionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Atualiza os campos informados de um lançamento existente (PATCH semântico)."""
    repo = TransactionRepository(db)
    updated = await repo.update(transaction_id, payload.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transação não encontrada")
    return updated


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(transaction_id: int, db: AsyncSession = Depends(get_db)):
    """Remove um lançamento permanentemente."""
    repo = TransactionRepository(db)
    deleted = await repo.delete(transaction_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transação não encontrada")
