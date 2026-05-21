"""
Endpoints de dívidas e financiamentos.

Cronograma de amortização usa Tabela Price (parcela constante):
    PMT        = prestação fixa (installment_amount cadastrado)
    Juros(n)   = saldo_anterior × taxa_mensal
    Principal(n) = PMT − Juros(n)
    Saldo(n)   = saldo_anterior − Principal(n)

A projeção parte do saldo devedor atual (remaining_amount) e do número de
parcelas restantes (total_installments − paid_installments), recalculando o
PMT pelo método de anuidade caso o saldo não bata exatamente com o valor
cadastrado (possível após pagamentos antecipados ou reestruturações).
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.schemas.debts import (
    DebtCreate,
    DebtOut,
    DebtPaymentCreate,
    DebtPaymentOut,
    DebtScheduleOut,
    DebtUpdate,
    ScheduleInstallment,
)
from backend.core.database import get_db
from backend.models.debt import Debt, DebtPayment

router = APIRouter(prefix="/debts", tags=["Dívidas"])


# -----------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------

def _next_due_date(start_date: date, due_day: int, paid: int) -> date:
    """Calcula a data de vencimento da próxima parcela não paga."""
    # A parcela N vence no mês (start_date + N meses), dia due_day
    installment_index = paid + 1  # próxima parcela a pagar (1-based)
    month = start_date.month + installment_index - 1
    year  = start_date.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    # Clamp para o último dia do mês (ex: due_day=31 em fevereiro → 28/29)
    last_day = calendar.monthrange(year, month)[1]
    day = min(due_day, last_day)
    return date(year, month, day)


def _build_schedule(
    debt: Debt,
) -> list[ScheduleInstallment]:
    """
    Gera o cronograma completo de parcelas restantes via Tabela Price.

    Recalcula o PMT a partir do saldo e taxa atuais para garantir
    consistência mesmo após pagamentos parciais ou antecipados.
    """
    remaining = int(debt.total_installments) - int(debt.paid_installments)
    if remaining <= 0:
        return []

    rate  = Decimal(str(debt.interest_rate)) / Decimal("100")
    saldo = Decimal(str(debt.remaining_amount))

    # Recalcula PMT pela fórmula de anuidade: PMT = PV × i / (1 − (1+i)^−n)
    if rate > 0 and remaining > 0:
        factor = (1 + rate) ** remaining
        pmt = (saldo * rate * factor / (factor - 1)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        pmt = (saldo / remaining).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    schedule: list[ScheduleInstallment] = []
    balance = saldo

    for i in range(remaining):
        installment_num = debt.paid_installments + i + 1
        due_date = _next_due_date(debt.start_date, debt.due_day, debt.paid_installments + i)

        interest  = (balance * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        principal = pmt - interest

        # Última parcela: ajusta para zerar o saldo exatamente
        if i == remaining - 1:
            principal = balance
            pmt_this  = principal + interest
        else:
            pmt_this = pmt

        balance = (balance - principal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if balance < 0:
            balance = Decimal("0.00")

        schedule.append(
            ScheduleInstallment(
                installment_number=installment_num,
                due_date=due_date,
                installment_amount=pmt_this,
                interest=interest,
                principal=principal,
                remaining_balance=balance,
            )
        )

    return schedule


# -----------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------

@router.get("", response_model=list[DebtOut])
async def list_debts(db: AsyncSession = Depends(get_db)):
    """Lista todas as dívidas ativas."""
    result = await db.execute(
        select(Debt).where(Debt.is_active.is_(True)).order_by(Debt.created_at)
    )
    return list(result.scalars().all())


@router.post("", response_model=DebtOut, status_code=status.HTTP_201_CREATED)
async def create_debt(payload: DebtCreate, db: AsyncSession = Depends(get_db)):
    """Cadastra uma nova dívida ou financiamento."""
    debt = Debt(**payload.model_dump())
    db.add(debt)
    await db.flush()
    await db.refresh(debt)
    return debt


@router.put("/{debt_id}", response_model=DebtOut)
async def update_debt(
    debt_id: int,
    payload: DebtUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Atualiza campos da dívida (PATCH semântico via PUT)."""
    result = await db.execute(select(Debt).where(Debt.id == debt_id))
    debt = result.scalar_one_or_none()
    if debt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dívida não encontrada")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(debt, field, value)

    await db.flush()
    await db.refresh(debt)
    return debt


@router.delete("/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_debt(debt_id: int, db: AsyncSession = Depends(get_db)):
    """Desativa a dívida (soft delete — marca is_active=False)."""
    result = await db.execute(select(Debt).where(Debt.id == debt_id))
    debt = result.scalar_one_or_none()
    if debt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dívida não encontrada")
    debt.is_active = False
    await db.flush()


@router.get("/{debt_id}/schedule", response_model=DebtScheduleOut)
async def get_debt_schedule(debt_id: int, db: AsyncSession = Depends(get_db)):
    """
    Retorna o cronograma completo de amortização das parcelas futuras.

    Usa Tabela Price (prestação constante). O PMT é recalculado a partir
    do saldo devedor atual para garantir consistência com pagamentos
    antecipados ou reestruturações.
    """
    result = await db.execute(select(Debt).where(Debt.id == debt_id))
    debt = result.scalar_one_or_none()
    if debt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dívida não encontrada")

    remaining = int(debt.total_installments) - int(debt.paid_installments)
    schedule  = _build_schedule(debt)

    return DebtScheduleOut(
        debt_id=debt.id,
        debt_name=debt.name,
        interest_rate_monthly=debt.interest_rate,
        remaining_installments=remaining,
        schedule=schedule,
    )


@router.post(
    "/{debt_id}/payments",
    response_model=DebtPaymentOut,
    status_code=status.HTTP_201_CREATED,
)
async def register_payment(
    debt_id: int,
    payload: DebtPaymentCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Registra um pagamento realizado e atualiza o saldo devedor.

    Incrementa paid_installments e atualiza remaining_amount com
    o valor informado em remaining_after.
    """
    result = await db.execute(select(Debt).where(Debt.id == debt_id))
    debt = result.scalar_one_or_none()
    if debt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dívida não encontrada")

    payment = DebtPayment(debt_id=debt_id, **payload.model_dump())
    db.add(payment)

    # Atualiza saldo devedor e contagem de parcelas
    debt.remaining_amount = payload.remaining_after
    debt.paid_installments = int(debt.paid_installments) + 1

    # Quitou a dívida automaticamente se saldo zerou
    if debt.remaining_amount <= 0:
        debt.remaining_amount = Decimal("0.00")
        debt.is_active = False

    await db.flush()
    await db.refresh(payment)
    return payment
