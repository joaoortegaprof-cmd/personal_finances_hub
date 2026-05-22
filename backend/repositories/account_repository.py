"""
Repositório de contas e cartões de crédito.
"""

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.account import (
    Account,
    CreditCard,
    CreditCardInvoice,
    InvoiceStatus,
)
from backend.models.transaction import Transaction, TransactionType
from backend.repositories.base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    """
    Operações de banco relacionadas a contas e cartões de crédito.

    Herda get_by_id, get_all, create, update, delete e exists do BaseRepository.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Account, session)

    # -----------------------------------------------------------------
    # Busca por nome
    # -----------------------------------------------------------------

    async def get_by_name(self, name: str) -> Account | None:
        """
        Retorna a primeira conta cujo nome corresponda exatamente ao texto.

        A busca é case-insensitive via ilike (%) para tolerar variações de
        capitalização — útil na busca em tempo real da interface.
        """
        result = await self._session.execute(
            select(Account).where(Account.name.ilike(name))
        )
        return result.scalars().first()

    # -----------------------------------------------------------------
    # Listagem de contas
    # -----------------------------------------------------------------

    async def get_all_accounts(self) -> list[Account]:
        """
        Retorna todas as contas ordenadas por nome.

        Carrega credit_cards junto via selectinload — o dashboard
        sempre precisa saber quantos cartões cada conta possui.
        """
        result = await self._session.execute(
            select(Account)
            .options(selectinload(Account.credit_cards))
            .order_by(Account.name)
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Saldo atual
    # -----------------------------------------------------------------

    async def get_current_balance(self, account_id: int) -> Decimal:
        """
        Calcula o saldo atual da conta:

            saldo_atual = saldo_inicial
                        + SUM(receitas)
                        - SUM(despesas + transferências)

        Considera apenas transações a partir de initial_balance_date,
        evitando reprocessar histórico anterior ao cadastro no sistema.

        CREDIT_EXPENSE tem account_id=NULL e é excluído explicitamente;
        os demais tipos de saída (DEBIT_EXPENSE, INVOICE_PAYMENT, INVESTMENT)
        subtraem do saldo.
        """
        account = await self.get_by_id(account_id)
        if account is None:
            raise ValueError(f"Conta {account_id} não encontrada.")

        # INCOME soma; DEBIT_EXPENSE / INVOICE_PAYMENT / INVESTMENT subtraem.
        # CREDIT_EXPENSE naturalmente tem account_id=NULL e não aparece aqui.
        signed_amount = case(
            (Transaction.transaction_type == TransactionType.INCOME, Transaction.amount),
            else_=-Transaction.amount,
        )

        result = await self._session.execute(
            select(func.coalesce(func.sum(signed_amount), Decimal("0.00"))).where(
                Transaction.account_id == account_id,
                Transaction.transaction_date >= account.initial_balance_date,
                Transaction.transaction_type != TransactionType.CREDIT_EXPENSE,
            )
        )
        delta: Decimal = result.scalar_one()
        return account.initial_balance + delta

    # -----------------------------------------------------------------
    # Cartões de uma conta
    # -----------------------------------------------------------------

    async def get_cards_for_account(self, account_id: int) -> list[CreditCard]:
        """
        Lista todos os cartões cujo pagamento é debitado desta conta,
        ordenados por nome.
        """
        result = await self._session.execute(
            select(CreditCard)
            .where(CreditCard.payment_account_id == account_id)
            .order_by(CreditCard.name)
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Fatura aberta de um cartão
    # -----------------------------------------------------------------

    async def get_open_invoice(self, card_id: int) -> CreditCardInvoice | None:
        """
        Retorna a fatura com status OPEN do cartão, se existir.

        Há no máximo uma fatura aberta por cartão (a única que ainda
        recebe novos lançamentos). Faturas fechadas e pagas são históricas.
        Ordena por (year DESC, month DESC) para pegar a mais recente
        caso haja inconsistência nos dados.
        """
        result = await self._session.execute(
            select(CreditCardInvoice)
            .where(
                CreditCardInvoice.credit_card_id == card_id,
                CreditCardInvoice.status == InvoiceStatus.OPEN,
            )
            .order_by(CreditCardInvoice.year.desc(), CreditCardInvoice.month.desc())
            .limit(1)
        )
        return result.scalars().first()

    # -----------------------------------------------------------------
    # CRUD global de cartões
    # -----------------------------------------------------------------

    async def get_all_cards(self) -> list[CreditCard]:
        """Lista todos os cartões de todos as contas, ordenados por nome."""
        result = await self._session.execute(
            select(CreditCard).order_by(CreditCard.name)
        )
        return list(result.scalars().all())

    async def get_card_by_id(self, card_id: int) -> CreditCard | None:
        """Retorna um cartão pelo ID."""
        return await self._session.get(CreditCard, card_id)

    async def create_card(self, card: CreditCard) -> CreditCard:
        """Persiste um novo cartão e retorna com ID gerado."""
        self._session.add(card)
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def update_card(self, card_id: int, data: dict) -> CreditCard | None:
        """Atualiza campos informados de um cartão existente."""
        card = await self.get_card_by_id(card_id)
        if card is None:
            return None
        for field, value in data.items():
            if hasattr(card, field):
                setattr(card, field, value)
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def delete_card(self, card_id: int) -> bool:
        """Remove um cartão permanentemente (faturas são cascateadas)."""
        card = await self.get_card_by_id(card_id)
        if card is None:
            return False
        await self._session.delete(card)
        await self._session.flush()
        return True

    async def card_exists(self, card_id: int) -> bool:
        """Verifica se um cartão com o ID dado existe."""
        result = await self._session.execute(
            select(func.count())
            .select_from(CreditCard)
            .where(CreditCard.id == card_id)
            .limit(1)
        )
        return (result.scalar_one() or 0) > 0

    # -----------------------------------------------------------------
    # Faturas de cartão
    # -----------------------------------------------------------------

    async def get_card_invoices(self, card_id: int) -> list[CreditCardInvoice]:
        """Lista todas as faturas de um cartão em ordem decrescente."""
        result = await self._session.execute(
            select(CreditCardInvoice)
            .where(CreditCardInvoice.credit_card_id == card_id)
            .order_by(CreditCardInvoice.year.desc(), CreditCardInvoice.month.desc())
        )
        return list(result.scalars().all())

    async def get_invoice_by_id(self, invoice_id: int) -> CreditCardInvoice | None:
        """Retorna uma fatura pelo ID."""
        return await self._session.get(CreditCardInvoice, invoice_id)

    async def create_invoice(self, invoice: CreditCardInvoice) -> CreditCardInvoice:
        """Persiste uma nova fatura."""
        self._session.add(invoice)
        await self._session.flush()
        await self._session.refresh(invoice)
        return invoice

    async def update_invoice(self, invoice_id: int, data: dict) -> CreditCardInvoice | None:
        """Atualiza campos de uma fatura (status, total_amount etc.)."""
        invoice = await self.get_invoice_by_id(invoice_id)
        if invoice is None:
            return None
        for field, value in data.items():
            if hasattr(invoice, field):
                setattr(invoice, field, value)
        await self._session.flush()
        await self._session.refresh(invoice)
        return invoice
