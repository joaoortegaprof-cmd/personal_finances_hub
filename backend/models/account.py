"""
Models de contas e cartões de crédito.

Hierarquia de relacionamentos:
    Account ──< CreditCard ──< CreditCardInvoice
       │
       └── também é a conta de pagamento de CreditCard (self-ref via FK)

Cada class corresponde a uma tabela no banco de dados.
O SQLAlchemy 2.0 usa a sintaxe "Mapped[tipo]" para declarar colunas
de forma totalmente tipada — o type checker (mypy/pyright) consegue
inferir os tipos corretos sem anotações extras.
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


# -----------------------------------------------------------------
# Enums — conjuntos fechados de valores válidos
# -----------------------------------------------------------------
# Usar enum em vez de string livre evita valores inválidos no banco
# e torna o código mais legível (AccountType.CHECKING vs "corrente").

class AccountType(str, enum.Enum):
    """Tipo da conta financeira."""
    CHECKING    = "corrente"     # Conta corrente tradicional
    SAVINGS     = "poupanca"     # Conta poupança
    INVESTMENT  = "investimento" # Conta de custódia / corretora
    DIGITAL     = "digital"      # Carteira digital (Nubank, PicPay etc.)
    CASH        = "especie"      # Dinheiro físico em carteira


class InvoiceStatus(str, enum.Enum):
    """Estado atual de uma fatura de cartão de crédito."""
    OPEN   = "aberta"   # Ainda recebendo lançamentos
    CLOSED = "fechada"  # Fechada, aguardando pagamento
    PAID   = "paga"     # Quitada


# -----------------------------------------------------------------
# Model: Account
# -----------------------------------------------------------------

class Account(Base):
    """
    Representa qualquer conta que armazena dinheiro: bancária,
    carteira digital ou espécie.

    É a entidade central do sistema — lançamentos de receita/despesa
    e pagamentos de fatura sempre referenciam uma Account.
    """

    __tablename__ = "accounts"

    # --- Chave primária ---
    # Integer com autoincrement é o padrão mais simples para PKs locais.
    # Em sistemas distribuídos preferiríamos UUID, mas SQLite + uso
    # single-user não exige isso.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Identificação ---

    # Nome exibido na interface — escolhido pelo usuário (ex: "Nubank Pessoal")
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Nome da instituição financeira (ex: "Bradesco", "Nubank", "XP")
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Categoria da conta — controla quais operações são permitidas
    # e como o saldo é exibido no dashboard
    account_type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type_enum"),
        nullable=False,
    )

    # --- Moeda ---

    # Código ISO 4217 da moeda (BRL, USD, EUR…).
    # A maioria das contas será BRL, mas suporte multi-moeda é útil
    # para contas no exterior ou carteiras de cripto.
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="BRL",
        server_default="BRL",
    )

    # --- Saldo inicial ---

    # Valor do saldo no momento em que a conta foi cadastrada no sistema.
    # Necessário para calcular o saldo atual sem importar todo o histórico
    # bancário: saldo_atual = initial_balance + soma(lançamentos).
    # Numeric(15, 2) suporta valores até 999.999.999.999,99 com precisão
    # exata — evita erros de arredondamento do float.
    initial_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    # Data em que o saldo inicial foi registrado.
    # Lançamentos anteriores a essa data são ignorados no cálculo.
    initial_balance_date: Mapped[date] = mapped_column(Date, nullable=False)

    # --- Auditoria ---

    # Preenchidos automaticamente pelo banco — não precisam de lógica na app.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),  # func.now() gera NOW() no SQL
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),  # atualizado automaticamente em cada UPDATE
    )

    # --- Relacionamentos ---

    # Lista de cartões de crédito cujo pagamento é debitado nesta conta.
    # back_populates="payment_account" liga ao atributo espelho em CreditCard.
    # lazy="selectin" carrega os cartões em uma segunda query automática
    # (mais eficiente que "joined" quando a lista pode ser grande).
    credit_cards: Mapped[list["CreditCard"]] = relationship(
        "CreditCard",
        back_populates="payment_account",
        lazy="selectin",
    )

    # Transações vinculadas a esta conta.
    # lazy="raise" impede carregamento acidental — uma conta pode ter
    # milhares de transações, então a query deve ser sempre explícita
    # (ex: options(selectinload(Account.transactions))).
    transactions: Mapped[list["Transaction"]] = relationship(  # type: ignore[name-defined]
        "Transaction",
        back_populates="account",
        lazy="raise",
    )

    # Operações de compra/venda executadas nesta corretora ou conta.
    # lazy="raise" pelo mesmo motivo — volume pode ser grande.
    asset_positions: Mapped[list["AssetPosition"]] = relationship(  # type: ignore[name-defined]
        "AssetPosition",
        back_populates="account",
        lazy="raise",
    )

    # --- Constraints ---

    __table_args__ = (
        # Garante que o código de moeda tenha exatamente 3 letras maiúsculas
        CheckConstraint("length(currency) = 3", name="ck_account_currency_length"),
    )

    def __repr__(self) -> str:
        return f"<Account id={self.id} name={self.name!r} type={self.account_type}>"


# -----------------------------------------------------------------
# Model: CreditCard
# -----------------------------------------------------------------

class CreditCard(Base):
    """
    Cartão de crédito vinculado a uma conta de pagamento.

    O ciclo de vida de um cartão:
        1. Lançamentos são registrados contra o cartão ao longo do mês.
        2. Na data de fechamento, a fatura é "fechada" (InvoiceStatus.CLOSED).
        3. Na data de vencimento, o usuário paga a fatura —
           o valor é debitado de payment_account.
    """

    __tablename__ = "credit_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Identificação ---

    # Apelido do cartão — ex: "Nubank Black", "Itaú Visa Platinum"
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Instituição emissora do cartão
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Quatro últimos dígitos — identifica o cartão sem expor dados sensíveis.
    # String de exatamente 4 dígitos numéricos.
    last_four_digits: Mapped[str] = mapped_column(String(4), nullable=False)

    # --- Limites e datas de ciclo ---

    # Limite total disponível no cartão
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Dia do mês em que a fatura fecha (1–28).
    # Limitado a 28 para funcionar em fevereiro sem lógica especial.
    closing_day: Mapped[int] = mapped_column(Integer, nullable=False)

    # Dia do mês em que a fatura vence (1–28)
    due_day: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- Visual ---

    # Cor do cartão escolhida pelo usuário em hex (#RRGGBB).
    # Usada para renderizar o card visual na tela de Cartões.
    card_color: Mapped[str] = mapped_column(
        String(7),
        nullable=False,
        default="#7B61FF",
        server_default="#7B61FF",
    )

    # --- Conta de pagamento ---

    # FK para a Account de onde sai o dinheiro ao pagar a fatura.
    # nullable=True porque o usuário pode cadastrar o cartão antes de
    # cadastrar a conta correspondente.
    payment_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- Auditoria ---

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # --- Relacionamentos ---

    # Conta que paga as faturas deste cartão
    payment_account: Mapped["Account | None"] = relationship(
        "Account",
        back_populates="credit_cards",
    )

    # Faturas mensais geradas para este cartão
    invoices: Mapped[list["CreditCardInvoice"]] = relationship(
        "CreditCardInvoice",
        back_populates="credit_card",
        cascade="all, delete-orphan",  # apagar o cartão apaga suas faturas
        lazy="selectin",
        order_by="CreditCardInvoice.year.desc(), CreditCardInvoice.month.desc()",
    )

    # Compras realizadas com este cartão (lazy="raise" pelo mesmo motivo de Account)
    transactions: Mapped[list["Transaction"]] = relationship(  # type: ignore[name-defined]
        "Transaction",
        back_populates="credit_card",
        lazy="raise",
    )

    # --- Constraints ---

    __table_args__ = (
        CheckConstraint("closing_day BETWEEN 1 AND 28", name="ck_card_closing_day"),
        CheckConstraint("due_day BETWEEN 1 AND 28",     name="ck_card_due_day"),
        CheckConstraint("credit_limit > 0",             name="ck_card_positive_limit"),
        CheckConstraint("length(last_four_digits) = 4", name="ck_card_last_four_length"),
    )

    def __repr__(self) -> str:
        return (
            f"<CreditCard id={self.id} name={self.name!r} "
            f"ending={self.last_four_digits}>"
        )


# -----------------------------------------------------------------
# Model: CreditCardInvoice
# -----------------------------------------------------------------

class CreditCardInvoice(Base):
    """
    Fatura mensal de um cartão de crédito.

    Uma fatura é criada automaticamente (ou manualmente) para cada mês
    de uso do cartão. Ela agrega todos os lançamentos do período e
    registra seu status de pagamento.

    A combinação (credit_card_id, month, year) é única — não podem
    existir duas faturas de janeiro/2025 para o mesmo cartão.
    """

    __tablename__ = "credit_card_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Referência ao cartão ---

    # Cartão ao qual esta fatura pertence.
    # ondelete="CASCADE" garante que ao apagar o cartão todas as
    # suas faturas são apagadas junto (espelhado no relationship acima).
    credit_card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("credit_cards.id", ondelete="CASCADE"),
        nullable=False,
    )

    # --- Período de referência ---

    # Mês de competência da fatura (1 = janeiro … 12 = dezembro)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ano de competência com 4 dígitos (ex: 2025)
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- Datas do ciclo de cobrança ---

    # Data em que a fatura foi ou será fechada para novos lançamentos.
    # Calculada a partir de CreditCard.closing_day no momento da criação.
    closing_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Data limite para pagamento sem juros.
    # Calculada a partir de CreditCard.due_day.
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # --- Valor ---

    # Soma de todos os lançamentos da fatura.
    # Recalculado sempre que um lançamento é adicionado ou removido.
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    # --- Status ---

    # Ciclo: OPEN → CLOSED → PAID
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status_enum"),
        nullable=False,
        default=InvoiceStatus.OPEN,
        server_default=InvoiceStatus.OPEN.value,
    )

    # --- Auditoria ---

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # --- Relacionamento ---

    # Cartão pai — acesso direto ao objeto completo a partir da fatura
    credit_card: Mapped["CreditCard"] = relationship(
        "CreditCard",
        back_populates="invoices",
    )

    # Lançamentos que compõem esta fatura (lazy="raise" — pode ser grande)
    transactions: Mapped[list["Transaction"]] = relationship(  # type: ignore[name-defined]
        "Transaction",
        back_populates="invoice",
        lazy="raise",
    )

    # --- Constraints ---

    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12",  name="ck_invoice_month_range"),
        CheckConstraint("year >= 2000",            name="ck_invoice_year_min"),
        CheckConstraint("total_amount >= 0",       name="ck_invoice_positive_amount"),
        # Unicidade: um cartão só pode ter uma fatura por mês/ano
        UniqueConstraint("credit_card_id", "month", "year", name="uq_invoice_card_month_year"),
    )

    def __repr__(self) -> str:
        return (
            f"<CreditCardInvoice id={self.id} card_id={self.credit_card_id} "
            f"{self.month:02d}/{self.year} status={self.status}>"
        )
