"""
Model de transações financeiras do FinanceHub.

Uma Transaction é o registro atômico de qualquer movimentação de dinheiro:
um salário recebido, uma compra no supermercado, um pagamento de fatura.

Relacionamentos:
    Transaction >── Account             (conta de origem/destino)
    Transaction >── CreditCard          (cartão usado na compra, opcional)
    Transaction >── CreditCardInvoice   (fatura que agrupa a compra, opcional)

Os três FKs são independentes e opcionais:
    - Débito em conta:        account_id preenchido, card e invoice nulos
    - Compra no cartão:       account_id nulo, credit_card_id e invoice_id preenchidos
    - Transferência entre contas: account_id preenchido (origem), description indica destino
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
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


# -----------------------------------------------------------------
# Enums
# -----------------------------------------------------------------

class TransactionType(str, enum.Enum):
    """Natureza do fluxo financeiro da transação."""
    INCOME   = "receita"      # Entrada de dinheiro (salário, dividendo, venda…)
    EXPENSE  = "despesa"      # Saída de dinheiro (compra, conta, serviço…)
    TRANSFER = "transferencia"# Movimentação interna entre contas próprias


class TransactionCategory(str, enum.Enum):
    """
    Categoria que classifica o propósito da transação.

    Usada para agrupar gastos em gráficos, calcular orçamentos e
    detectar padrões de consumo. Novas categorias exigem migration.
    """
    # Receitas
    SALARY      = "salario"       # Renda do trabalho (CLT, PJ, freelance)
    DIVIDENDS   = "dividendos"    # Proventos de ações, FIIs, fundos

    # Moradia e vida
    HOUSING     = "moradia"       # Aluguel, condomínio, IPTU, reformas
    SUPERMARKET = "supermercado"  # Compras de alimentação no varejo
    TRANSPORT   = "transporte"    # Combustível, Uber, transporte público, IPVA
    HEALTH      = "saude"         # Plano, farmácia, consultas, exames
    EDUCATION   = "educacao"      # Cursos, mensalidades, livros

    # Lazer e consumo
    RESTAURANT  = "restaurante"   # Refeições fora de casa, delivery
    ENTERTAINMENT = "entretenimento" # Streaming, jogos, cinema, shows
    SHOPPING    = "compras"       # Vestuário, eletrônicos, itens gerais
    TRAVEL      = "viagem"        # Passagens, hospedagem, passeios

    # Financeiro
    CREDIT_CARD = "cartao_credito" # Pagamento de fatura de cartão
    LOAN        = "emprestimo"     # Parcelas de empréstimos ou financiamentos
    INVESTMENT  = "investimento"   # Aportes em ativos, CDB, Tesouro, ações

    # Genérico
    OTHER       = "outros"         # Tudo que não se encaixa nas categorias acima


class ExpenseNature(str, enum.Enum):
    """
    Classifica a natureza de uma despesa para análise de saúde financeira.

    Só é relevante para transaction_type == EXPENSE (ou TRANSFER).
    Receitas devem ter este campo como None.
    """
    ESSENTIAL      = "essential"       # Gasto essencial (moradia, saúde, transporte…)
    DISCRETIONARY  = "discretionary"   # Gasto supérfluo (lazer, restaurante, compras…)
    INVESTMENT     = "investment"      # Aporte / investimento
    TRANSFER       = "transfer"        # Transferência interna entre contas


# Mapeamento automático: categoria → natureza da despesa (populado abaixo)


# -----------------------------------------------------------------
# Model: Transaction
# -----------------------------------------------------------------

class Transaction(Base):
    """
    Registro de qualquer movimentação financeira do usuário.

    O valor (amount) é sempre positivo — o tipo (TransactionType) e o
    relacionamento com a conta determinam se é entrada ou saída no saldo.
    Isso simplifica queries de soma e comparação entre receitas e despesas.
    """

    __tablename__ = "transactions"

    # --- Chave primária ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # -----------------------------------------------------------------
    # Campos principais
    # -----------------------------------------------------------------

    # Texto livre que descreve a transação — ex: "Aluguel abril/2025",
    # "Supermercado Extra", "Salário". Exibido nas listagens.
    description: Mapped[str] = mapped_column(String(255), nullable=False)

    # Valor absoluto da transação, sempre positivo.
    # Numeric(15, 2) garante precisão exata para valores monetários —
    # floats têm erros de arredondamento que se acumulam em somas.
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Data em que a transação ocorreu (não a data de registro no sistema).
    # Separar as duas datas é importante: o usuário pode registrar uma
    # compra de ontem hoje, e os relatórios devem refletir o dia real.
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)

    # -----------------------------------------------------------------
    # Classificação
    # -----------------------------------------------------------------

    # Natureza do fluxo: receita (+), despesa (–) ou transferência
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="transaction_type_enum"),
        nullable=False,
    )

    # Categoria para agrupamento em relatórios e orçamentos
    category: Mapped[TransactionCategory] = mapped_column(
        Enum(TransactionCategory, name="transaction_category_enum"),
        nullable=False,
        default=TransactionCategory.OTHER,
        server_default=TransactionCategory.OTHER.value,
    )

    # Natureza da despesa (nullable — só relevante para despesas/transferências)
    expense_nature: Mapped[ExpenseNature | None] = mapped_column(
        Enum(ExpenseNature, name="expense_nature_enum"),
        nullable=True,
    )

    # -----------------------------------------------------------------
    # Chaves estrangeiras — todas opcionais
    # -----------------------------------------------------------------

    # Conta bancária ou carteira digital envolvida na transação.
    # Nulo quando a transação é de cartão de crédito ainda não pago
    # (o dinheiro ainda não saiu da conta).
    account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Cartão de crédito usado na compra.
    # Preenchido junto com invoice_id quando a compra foi no crédito.
    credit_card_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("credit_cards.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Fatura mensal à qual esta transação pertence.
    # Permite calcular o total da fatura somando transactions com
    # invoice_id = X, e agrupar compras por período de fatura.
    invoice_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("credit_card_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -----------------------------------------------------------------
    # Campos auxiliares
    # -----------------------------------------------------------------

    # Texto livre para anotações do usuário — ex: "parcelado em 3x",
    # "reembolso pendente", "nota fiscal #1234". Sem restrição de formato.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tags separadas por vírgula para filtros personalizados.
    # Ex: "trabalho,reembolsavel,projeto-alfa"
    # Armazenado como string simples — fácil de exibir e editar,
    # sem necessidade de tabela extra para um volume pequeno de dados.
    tags: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Indica se esta transação se repete periodicamente (mensalidade,
    # salário, aluguel). Usado para projetar o fluxo de caixa futuro
    # e alertar caso uma recorrência esperada não apareça.
    is_recurring: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",  # SQLite representa False como 0
    )

    # Marca lançamentos que compõem a reserva de emergência.
    # Permite calcular o saldo reservado sem criar uma conta separada.
    is_emergency_fund: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    # -----------------------------------------------------------------
    # Auditoria
    # -----------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # -----------------------------------------------------------------
    # Relacionamentos (many-to-one — muitas transações para um objeto)
    # -----------------------------------------------------------------

    # Conta da transação.
    # lazy="joined" emite um JOIN na mesma query que carrega a
    # transação — eficiente quando account é quase sempre necessária.
    account: Mapped["Account | None"] = relationship(  # type: ignore[name-defined]
        "Account",
        back_populates="transactions",
        lazy="joined",
    )

    # Cartão de crédito (se a compra foi no crédito)
    credit_card: Mapped["CreditCard | None"] = relationship(  # type: ignore[name-defined]
        "CreditCard",
        back_populates="transactions",
        lazy="joined",
    )

    # Fatura que contém esta transação
    invoice: Mapped["CreditCardInvoice | None"] = relationship(  # type: ignore[name-defined]
        "CreditCardInvoice",
        back_populates="transactions",
        lazy="joined",
    )

    # -----------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------

    __table_args__ = (
        # Valor da transação deve ser positivo — o tipo define o sinal
        CheckConstraint("amount > 0", name="ck_transaction_positive_amount"),
        # Compra no cartão exige que cartão e fatura sejam informados juntos
        CheckConstraint(
            "(credit_card_id IS NULL) = (invoice_id IS NULL)",
            name="ck_transaction_card_invoice_together",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} "
            f"type={self.transaction_type} "
            f"amount={self.amount} "
            f"date={self.transaction_date}>"
        )


# Mapeamento automático: categoria → natureza da despesa
CATEGORY_TO_NATURE: dict[TransactionCategory, ExpenseNature] = {
    TransactionCategory.HOUSING:       ExpenseNature.ESSENTIAL,
    TransactionCategory.SUPERMARKET:   ExpenseNature.ESSENTIAL,
    TransactionCategory.HEALTH:        ExpenseNature.ESSENTIAL,
    TransactionCategory.TRANSPORT:     ExpenseNature.ESSENTIAL,
    TransactionCategory.EDUCATION:     ExpenseNature.ESSENTIAL,
    TransactionCategory.RESTAURANT:    ExpenseNature.DISCRETIONARY,
    TransactionCategory.ENTERTAINMENT: ExpenseNature.DISCRETIONARY,
    TransactionCategory.SHOPPING:      ExpenseNature.DISCRETIONARY,
    TransactionCategory.TRAVEL:        ExpenseNature.DISCRETIONARY,
    TransactionCategory.INVESTMENT:    ExpenseNature.INVESTMENT,
}
