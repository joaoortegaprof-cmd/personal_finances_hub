"""
Endpoints de contas bancárias e cartões de crédito.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.accounts import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    BalanceOut,
    CreditCardOut,
)
from backend.core.database import get_db
from backend.models.account import Account
from backend.repositories.account_repository import AccountRepository

router = APIRouter(prefix="/accounts", tags=["Contas"])


@router.get("", response_model=list[AccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    """Lista todas as contas ordenadas por nome."""
    repo = AccountRepository(db)
    return await repo.get_all_accounts()


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(payload: AccountCreate, db: AsyncSession = Depends(get_db)):
    """Cria uma nova conta bancária ou carteira digital."""
    repo = AccountRepository(db)
    account = Account(**payload.model_dump())
    return await repo.create(account)


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """Retorna uma conta pelo ID."""
    repo = AccountRepository(db)
    account = await repo.get_by_id(account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta não encontrada")
    return account


@router.put("/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: int,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Atualiza os campos informados de uma conta existente (PATCH semântico)."""
    repo = AccountRepository(db)
    updated = await repo.update(account_id, payload.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta não encontrada")
    return updated


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """
    Remove uma conta permanentemente.

    Nota: soft-delete (desativar sem excluir) requer o campo is_active no model.
    Implemente em uma migration Alembic futura se necessário.
    """
    repo = AccountRepository(db)
    deleted = await repo.delete(account_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta não encontrada")


@router.get("/{account_id}/balance", response_model=BalanceOut)
async def get_account_balance(account_id: int, db: AsyncSession = Depends(get_db)):
    """Calcula o saldo atual da conta: saldo_inicial + receitas - despesas."""
    repo = AccountRepository(db)
    if not await repo.exists(account_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta não encontrada")
    balance = await repo.get_current_balance(account_id)
    return BalanceOut(account_id=account_id, balance=balance)


@router.get("/{account_id}/cards", response_model=list[CreditCardOut])
async def get_account_cards(account_id: int, db: AsyncSession = Depends(get_db)):
    """Lista os cartões de crédito cujo pagamento é debitado desta conta."""
    repo = AccountRepository(db)
    if not await repo.exists(account_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta não encontrada")
    return await repo.get_cards_for_account(account_id)
