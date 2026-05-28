"""
Endpoints de cartões de crédito e faturas.

Rotas:
    GET    /cards                          - lista todos os cartões
    POST   /cards                          - cria cartão
    GET    /cards/{id}                     - retorna cartão
    PUT    /cards/{id}                     - atualiza cartão
    DELETE /cards/{id}                     - remove cartão
    GET    /cards/{id}/invoices            - lista faturas do cartão
    POST   /cards/{id}/invoices            - cria fatura manualmente
    PATCH  /cards/{id}/invoices/{inv_id}/status - muda status da fatura
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.accounts import (
    CreditCardCreate,
    CreditCardInvoiceCreate,
    CreditCardInvoiceOut,
    CreditCardOut,
    CreditCardUpdate,
    InvoiceStatusUpdate,
)
from backend.core.database import get_db
from backend.models.account import CreditCard, CreditCardInvoice, InvoiceStatus
from backend.models.transaction import Transaction, TransactionType
from backend.repositories.account_repository import AccountRepository

router = APIRouter(prefix="/cards", tags=["Cartões"])


@router.get("", response_model=list[CreditCardOut])
async def list_cards(db: AsyncSession = Depends(get_db)):
    """Lista todos os cartões ordenados por nome."""
    repo = AccountRepository(db)
    return await repo.get_all_cards()


@router.post("", response_model=CreditCardOut, status_code=status.HTTP_201_CREATED)
async def create_card(payload: CreditCardCreate, db: AsyncSession = Depends(get_db)):
    """Cria um novo cartão de crédito."""
    repo = AccountRepository(db)
    card = CreditCard(**payload.model_dump())
    return await repo.create_card(card)


@router.get("/{card_id}", response_model=CreditCardOut)
async def get_card(card_id: int, db: AsyncSession = Depends(get_db)):
    """Retorna um cartão pelo ID."""
    repo = AccountRepository(db)
    card = await repo.get_card_by_id(card_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cartão não encontrado")
    return card


@router.put("/{card_id}", response_model=CreditCardOut)
async def update_card(
    card_id: int,
    payload: CreditCardUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Atualiza os campos informados de um cartão existente."""
    repo = AccountRepository(db)
    updated = await repo.update_card(card_id, payload.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cartão não encontrado")
    return updated


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(card_id: int, db: AsyncSession = Depends(get_db)):
    """Remove um cartão e todas as suas faturas permanentemente."""
    repo = AccountRepository(db)
    deleted = await repo.delete_card(card_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cartão não encontrado")


@router.get("/{card_id}/available-limit")
async def get_card_available_limit(card_id: int, db: AsyncSession = Depends(get_db)):
    """
    Retorna o limite disponível do cartão.

    Campos retornados:
      - credit_limit:      limite total cadastrado
      - used_amount:       soma das compras CREDIT_EXPENSE na fatura aberta
      - available:         credit_limit - used_amount (mínimo 0)
      - open_invoice_id:   ID da fatura aberta (None se não houver)
      - invoice_status:    status da fatura aberta

    Bug corrigido: o cálculo anterior somava TODOS os lançamentos da fatura,
    incluindo INVOICE_PAYMENT — que deve restaurar limite, não consumi-lo.
    Agora apenas transações do tipo CREDIT_EXPENSE entram no used_amount.
    """
    repo = AccountRepository(db)
    card = await repo.get_card_by_id(card_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cartão não encontrado")

    open_invoice = await repo.get_open_invoice(card_id)

    if not open_invoice:
        # Sem fatura aberta → limite totalmente disponível
        return {
            "credit_limit":    float(card.credit_limit),
            "used_amount":     0.0,
            "available":       float(card.credit_limit),
            "open_invoice_id": None,
            "invoice_status":  None,
        }

    # Soma apenas compras no crédito — pagamentos de fatura não consomem limite
    result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), Decimal("0.00")))
        .where(
            Transaction.credit_card_id == card_id,
            Transaction.invoice_id == open_invoice.id,
            Transaction.transaction_type == TransactionType.CREDIT_EXPENSE,
        )
    )
    used_amount = result.scalar() or Decimal("0.00")

    limit = card.credit_limit
    available = float(limit) - float(used_amount)

    return {
        "credit_limit":    float(limit),
        "used_amount":     float(used_amount),
        "available":       max(0.0, available),
        "open_invoice_id": open_invoice.id,
        "invoice_status":  open_invoice.status,
    }


@router.get("/{card_id}/invoices", response_model=list[CreditCardInvoiceOut])
async def list_invoices(card_id: int, db: AsyncSession = Depends(get_db)):
    """Lista todas as faturas de um cartão em ordem decrescente."""
    repo = AccountRepository(db)
    if not await repo.card_exists(card_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cartão não encontrado")
    return await repo.get_card_invoices(card_id)


@router.post(
    "/{card_id}/invoices",
    response_model=CreditCardInvoiceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_invoice(
    card_id: int,
    payload: CreditCardInvoiceCreate,
    db: AsyncSession = Depends(get_db),
):
    """Cria uma fatura manualmente para o cartão informado."""
    repo = AccountRepository(db)
    if not await repo.card_exists(card_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cartão não encontrado")
    invoice = CreditCardInvoice(credit_card_id=card_id, **payload.model_dump())
    return await repo.create_invoice(invoice)


@router.patch(
    "/{card_id}/invoices/{invoice_id}/status",
    response_model=CreditCardInvoiceOut,
)
async def update_invoice_status(
    card_id: int,
    invoice_id: int,
    payload: InvoiceStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Atualiza o status de uma fatura (aberta → fechada → paga)."""
    repo = AccountRepository(db)
    invoice = await repo.get_invoice_by_id(invoice_id)
    if invoice is None or invoice.credit_card_id != card_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fatura não encontrada")
    updated = await repo.update_invoice(invoice_id, {"status": payload.status})
    return updated
