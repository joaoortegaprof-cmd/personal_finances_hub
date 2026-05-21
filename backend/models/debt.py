"""
Models de dívidas e financiamentos do FinanceHub.

Hierarquia:
    Debt ──< DebtPayment
      │
      └── representa a dívida em si (financiamento, empréstimo…)
          DebtPayment representa cada pagamento realizado

Separar Debt de DebtPayment permite:
  - Rastrear o histórico completo de pagamentos.
  - Calcular amortização acumulada vs juros pagos.
  - Projetar o cronograma futuro de parcelas.
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


# -----------------------------------------------------------------
# Enums
# -----------------------------------------------------------------

class DebtType(str, enum.Enum):
    """Tipo da dívida — determina como os juros e parcelas são calculados."""
    FINANCING        = "financiamento"      # Financiamento de imóvel, carro etc.
    CREDIT_CARD_REVOLVING = "cartao_rotativo"  # Rotativo do cartão de crédito
    PERSONAL_LOAN    = "emprestimo_pessoal" # Empréstimo pessoal bancário
    OVERDRAFT        = "cheque_especial"    # Cheque especial / conta garantida
    OTHER            = "outro"              # Qualquer outro tipo de dívida


# -----------------------------------------------------------------
# Model: Debt
# -----------------------------------------------------------------

class Debt(Base):
    """
    Registro de uma dívida ou financiamento do usuário.

    O saldo devedor atual (remaining_amount) é atualizado a cada
    pagamento registrado. A taxa de juros (interest_rate) é mensal,
    usada para projetar o cronograma de amortização (Tabela Price).
    """

    __tablename__ = "debts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # -----------------------------------------------------------------
    # Identificação
    # -----------------------------------------------------------------

    # Nome descritivo da dívida — ex: "Financiamento Carro", "Empréstimo Banco X"
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Instituição financeira credora — ex: "Bradesco", "Itaú", "CEF"
    institution: Mapped[str] = mapped_column(String(200), nullable=False)

    # -----------------------------------------------------------------
    # Valores financeiros
    # -----------------------------------------------------------------

    # Valor total original da dívida no momento da contratação
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Saldo devedor atual — atualizado a cada pagamento registrado
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Taxa de juros mensal em percentual — ex: 1.5 representa 1,5% ao mês
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)

    # Valor da parcela mensal (Tabela Price = constante, SAC = decrescente)
    installment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # -----------------------------------------------------------------
    # Parcelas
    # -----------------------------------------------------------------

    # Número total de parcelas do contrato
    total_installments: Mapped[int] = mapped_column(Integer, nullable=False)

    # Parcelas já pagas até o momento
    paid_installments: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # -----------------------------------------------------------------
    # Datas
    # -----------------------------------------------------------------

    # Data de início do contrato / primeiro vencimento
    start_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Dia do mês em que a parcela vence (1–31)
    due_day: Mapped[int] = mapped_column(Integer, nullable=False)

    # -----------------------------------------------------------------
    # Classificação e status
    # -----------------------------------------------------------------

    debt_type: Mapped[DebtType] = mapped_column(
        Enum(DebtType, name="debt_type_enum"), nullable=False
    )

    # False quando a dívida foi quitada ou cancelada (soft delete)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    # -----------------------------------------------------------------
    # Auditoria
    # -----------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # -----------------------------------------------------------------
    # Relacionamentos
    # -----------------------------------------------------------------

    payments: Mapped[list["DebtPayment"]] = relationship(
        "DebtPayment",
        back_populates="debt",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="DebtPayment.payment_date",
    )

    # -----------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------

    __table_args__ = (
        CheckConstraint("total_amount > 0",         name="ck_debt_positive_total"),
        CheckConstraint("remaining_amount >= 0",     name="ck_debt_nonneg_remaining"),
        CheckConstraint("interest_rate > 0",         name="ck_debt_positive_rate"),
        CheckConstraint("installment_amount > 0",    name="ck_debt_positive_installment"),
        CheckConstraint("total_installments > 0",    name="ck_debt_positive_installments"),
        CheckConstraint("paid_installments >= 0",    name="ck_debt_nonneg_paid"),
        CheckConstraint("due_day BETWEEN 1 AND 31",  name="ck_debt_valid_due_day"),
    )

    def __repr__(self) -> str:
        return (
            f"<Debt id={self.id} name={self.name!r} "
            f"remaining={self.remaining_amount} type={self.debt_type}>"
        )


# -----------------------------------------------------------------
# Model: DebtPayment
# -----------------------------------------------------------------

class DebtPayment(Base):
    """
    Registro de um pagamento realizado em uma dívida.

    Cada linha decompõe o pagamento em amortização (principal_paid)
    e encargos (interest_paid), permitindo visualizar quanto do dinheiro
    pago foi para quitar a dívida vs quanto foi para os juros.
    """

    __tablename__ = "debt_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # -----------------------------------------------------------------
    # Chave estrangeira
    # -----------------------------------------------------------------

    debt_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("debts.id", ondelete="CASCADE"),
        nullable=False,
    )

    # -----------------------------------------------------------------
    # Dados do pagamento
    # -----------------------------------------------------------------

    # Data em que o pagamento foi efetuado
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Valor total pago (principal_paid + interest_paid)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Quanto do pagamento foi para amortização do saldo devedor
    principal_paid: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Quanto do pagamento foi para juros
    interest_paid: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Saldo devedor restante após este pagamento
    remaining_after: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # -----------------------------------------------------------------
    # Auditoria
    # -----------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # -----------------------------------------------------------------
    # Relacionamentos
    # -----------------------------------------------------------------

    debt: Mapped["Debt"] = relationship("Debt", back_populates="payments")

    # -----------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------

    __table_args__ = (
        CheckConstraint("amount_paid > 0",       name="ck_payment_positive_amount"),
        CheckConstraint("principal_paid >= 0",   name="ck_payment_nonneg_principal"),
        CheckConstraint("interest_paid >= 0",    name="ck_payment_nonneg_interest"),
        CheckConstraint("remaining_after >= 0",  name="ck_payment_nonneg_remaining"),
    )

    def __repr__(self) -> str:
        return (
            f"<DebtPayment id={self.id} debt_id={self.debt_id} "
            f"date={self.payment_date} amount={self.amount_paid}>"
        )
