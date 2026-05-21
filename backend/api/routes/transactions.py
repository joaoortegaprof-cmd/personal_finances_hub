"""
Endpoints de lançamentos financeiros (receitas, despesas e transferências).

Ordem dos endpoints importa: /summary e /emergency-fund devem vir antes
de /{id} para evitar que as strings sejam interpretadas como inteiros.
"""

import calendar
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.transactions import (
    EmergencyFundOut,
    MonthlySummaryOut,
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
)
from backend.core.database import get_db
from backend.models.transaction import Transaction, TransactionCategory, TransactionType
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


@router.get("/emergency-fund", response_model=EmergencyFundOut)
async def get_emergency_fund(db: AsyncSession = Depends(get_db)):
    """
    Retorna o status da reserva de emergência:
      - saldo_total: soma dos lançamentos marcados como is_emergency_fund=True
      - media_gastos_6m: média mensal de despesas nos últimos 6 meses
      - meses_cobertos: saldo_total / media_gastos_6m
    """
    # Saldo da reserva = soma dos valores marcados como emergência
    ef_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), Decimal("0.00"))).where(
            Transaction.is_emergency_fund.is_(True),
            Transaction.transaction_type == TransactionType.INCOME,
        )
    )
    saldo_total: Decimal = ef_result.scalar() or Decimal("0.00")

    # Despesas dos últimos 6 meses
    today = date.today()
    # Primeiro dia do mês 6 meses atrás
    six_months_ago_month = today.month - 6
    six_months_ago_year  = today.year
    if six_months_ago_month <= 0:
        six_months_ago_month += 12
        six_months_ago_year  -= 1
    start_6m = date(six_months_ago_year, six_months_ago_month, 1)
    last_day  = calendar.monthrange(today.year, today.month)[1]
    end_today = today.replace(day=last_day)

    expense_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), Decimal("0.00"))).where(
            Transaction.transaction_type == TransactionType.EXPENSE,
            Transaction.transaction_date >= start_6m,
            Transaction.transaction_date <= end_today,
        )
    )
    total_expense_6m: Decimal = expense_result.scalar() or Decimal("0.00")
    media_gastos_6m = (total_expense_6m / 6).quantize(Decimal("0.01"))

    meses_cobertos = (
        (saldo_total / media_gastos_6m).quantize(Decimal("0.1"))
        if media_gastos_6m > 0
        else Decimal("0.0")
    )

    return EmergencyFundOut(
        saldo_total=saldo_total,
        media_gastos_6m=media_gastos_6m,
        meses_cobertos=meses_cobertos,
    )


@router.get("/essential-cost")
async def get_essential_cost(db: AsyncSession = Depends(get_db)):
    """
    Retorna o custo médio mensal com categorias essenciais dos últimos 3 meses.
    Inclui breakdown por categoria para exibição detalhada no dashboard.
    """
    today = date.today()
    three_months_ago_month = today.month - 3
    three_months_ago_year  = today.year
    if three_months_ago_month <= 0:
        three_months_ago_month += 12
        three_months_ago_year  -= 1
    start_3m = date(three_months_ago_year, three_months_ago_month, 1)

    essential_cats = [
        TransactionCategory.HOUSING,
        TransactionCategory.SUPERMARKET,
        TransactionCategory.HEALTH,
        TransactionCategory.TRANSPORT,
        TransactionCategory.EDUCATION,
    ]

    result = await db.execute(
        select(Transaction.category, func.coalesce(func.sum(Transaction.amount), 0))
        .where(
            Transaction.transaction_type == TransactionType.EXPENSE,
            Transaction.category.in_(essential_cats),
            Transaction.transaction_date >= start_3m,
            Transaction.transaction_date <= today,
        )
        .group_by(Transaction.category)
    )
    # SQLAlchemy pode retornar o nome do enum ("HOUSING") ou o valor ("moradia")
    # dependendo da versão; normalizamos para o nome do enum como chave.
    by_cat: dict[str, float] = {}
    for row in result:
        raw = row[0]
        if hasattr(raw, "name"):
            key = raw.name           # enum object → usa nome
        else:
            # string bruta — pode ser nome ("HOUSING") ou valor ("moradia")
            reverse = {c.value: c.name for c in TransactionCategory}
            key = reverse.get(str(raw), str(raw))
        by_cat[key] = float(row[1])

    total_3m    = sum(by_cat.values())
    monthly_avg = round(total_3m / 3, 2)

    breakdown = [
        {
            "category":        cat.value,
            "monthly_average": round(by_cat.get(cat.name, 0.0) / 3, 2),
        }
        for cat in essential_cats
    ]

    return {"monthly_average": monthly_avg, "breakdown": breakdown}


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
