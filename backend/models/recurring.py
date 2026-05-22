"""
Model de gastos recorrentes — assinaturas, contratos e pagamentos periódicos.
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
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class Periodicity(str, enum.Enum):
    """Frequência de repetição do gasto."""
    WEEKLY      = "weekly"      # Semanal
    BIWEEKLY    = "biweekly"    # Quinzenal
    MONTHLY     = "monthly"     # Mensal
    BIMONTHLY   = "bimonthly"   # Bimestral
    QUARTERLY   = "quarterly"   # Trimestral
    SEMIANNUAL  = "semiannual"  # Semestral
    ANNUAL      = "annual"      # Anual


# Fator de conversão: quantas vezes este gasto ocorre por mês
MONTHLY_FACTOR: dict[Periodicity, Decimal] = {
    Periodicity.WEEKLY:     Decimal("4.33"),
    Periodicity.BIWEEKLY:   Decimal("2.17"),
    Periodicity.MONTHLY:    Decimal("1.00"),
    Periodicity.BIMONTHLY:  Decimal("0.50"),
    Periodicity.QUARTERLY:  Decimal("0.33"),
    Periodicity.SEMIANNUAL: Decimal("0.17"),
    Periodicity.ANNUAL:     Decimal("0.08"),
}


class RecurringExpense(Base):
    """
    Gasto periódico do usuário: assinatura, aluguel, mensalidade, etc.

    next_due_date é atualizado pelo usuário ao registrar o pagamento
    ou automaticamente quando o frontend avança o ciclo.
    """

    __tablename__ = "recurring_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="outros", server_default="outros"
    )

    periodicity: Mapped[Periodicity] = mapped_column(
        Enum(Periodicity, name="periodicity_enum"),
        nullable=False,
        default=Periodicity.MONTHLY,
    )

    next_due_date: Mapped[date] = mapped_column(Date, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_recurring_positive_amount"),
    )

    def __repr__(self) -> str:
        return f"<RecurringExpense id={self.id} name={self.name!r} amount={self.amount}>"
