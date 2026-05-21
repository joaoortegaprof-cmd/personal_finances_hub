"""
Model de proventos recebidos (dividendos, JCP, rendimentos de FII, etc.).

Cada linha representa um provento recebido pelo usuário para um ativo
específico. O total recebido é calculado como amount_per_unit × quantidade
na data ex, ou informado diretamente em total_amount.
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class DividendType(str, enum.Enum):
    DIVIDENDO      = "dividendo"       # Isento de IR para pessoa física
    JCP            = "jcp"             # Juros sobre Capital Próprio — IR 15% na fonte
    RENDIMENTO_FII = "rendimento_fii"  # Rendimento de FII — isento (quando distribuição mensal)
    AMORTIZACAO    = "amortizacao"     # Devolução parcial do capital investido
    BONIFICACAO    = "bonificacao"     # Distribuição de novas ações/cotas sem custo


class Dividend(Base):
    """
    Registro de um provento recebido.

    Vinculado a um ativo via asset_id. O campo total_amount deve ser
    preenchido com o valor efetivamente creditado na conta/corretora.
    """

    __tablename__ = "dividends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Data em que o provento foi efetivamente pago (creditado)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Data ex — quem tinha o ativo até esta data tem direito ao provento
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Valor por cota/ação declarado pela empresa
    amount_per_unit: Mapped[Decimal] = mapped_column(
        Numeric(15, 6), nullable=False, default=Decimal("0")
    )

    # Valor total efetivamente recebido (amount_per_unit × posição na data ex)
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False
    )

    dividend_type: Mapped[DividendType] = mapped_column(
        Enum(DividendType, name="dividend_type_enum"),
        nullable=False,
        default=DividendType.DIVIDENDO,
    )

    # JCP tem 15% de IR retido na fonte obrigatoriamente
    is_taxable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    # Valor de IR retido na fonte (0 se não houver)
    tax_withheld: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, default=Decimal("0"), server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relacionamento com o ativo
    asset: Mapped["Asset"] = relationship("Asset", lazy="joined")  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return (
            f"<Dividend id={self.id} asset_id={self.asset_id} "
            f"type={self.dividend_type} total={self.total_amount} "
            f"date={self.payment_date}>"
        )
