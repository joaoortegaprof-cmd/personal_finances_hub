"""
Models de investimentos do FinanceHub.

Hierarquia:
    Asset ──< AssetPosition >── Account
      │
      └── representa o "que é" o ativo (PETR4, Tesouro IPCA+ 2029…)
          AssetPosition representa "o que você fez" (comprou 100 cotas em tal data)

Separar Asset de AssetPosition é fundamental porque:
  - O mesmo ativo pode ter múltiplas compras/vendas ao longo do tempo.
  - O preço médio e a posição atual são calculados agregando as operações.
  - Metadados do ativo (setor, indexador, liquidez) ficam em um lugar só.
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
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


# -----------------------------------------------------------------
# Enums
# -----------------------------------------------------------------

class AssetType(str, enum.Enum):
    """
    Classe do ativo financeiro.

    Determina qual fonte de dados busca a cotação (yfinance, python-bcb,
    cálculo próprio) e quais regras fiscais se aplicam.
    """
    STOCK          = "acao"              # Ação listada na B3
    FII            = "fii"               # Fundo de Investimento Imobiliário (B3)
    ETF            = "etf"               # Exchange Traded Fund (B3 ou exterior)
    TREASURY       = "tesouro_direto"    # Títulos do Tesouro Nacional
    FIXED_INCOME   = "renda_fixa"        # CDB, LCI, LCA, Debentures…
    CRYPTO         = "criptomoeda"       # Bitcoin, Ethereum etc.
    INTL_STOCK     = "acao_internacional"# Ações negociadas no exterior (NYSE, NASDAQ…)
    PENSION        = "previdencia"       # PGBL / VGBL
    OTHER          = "outros"            # Qualquer ativo que não se encaixa acima


class AssetIndexer(str, enum.Enum):
    """
    Indexador de ativos de renda fixa e Tesouro Direto.

    Define como a rentabilidade é calculada e projetada.
    Irrelevante para renda variável — campo fica nulo nesses casos.
    """
    IPCA_PLUS  = "IPCA+"      # IPCA + taxa fixa (ex: Tesouro IPCA+ 2029)
    CDI        = "CDI"        # Percentual do CDI (ex: CDB 110% CDI)
    SELIC      = "Selic"      # Taxa Selic (ex: Tesouro Selic 2027)
    PREFIXED   = "Prefixado"  # Taxa fixa independente de índice (ex: CDB 12% a.a.)
    IGPM_PLUS  = "IGPM+"      # IGP-M + taxa fixa (menos comum hoje)


class LiquidityWindow(str, enum.Enum):
    """
    Janela de liquidez — em quantos dias úteis o dinheiro está disponível.

    Usada para calcular o gráfico de liquidez acumulada da carteira e
    emitir alertas quando a reserva de emergência estiver abaixo do mínimo.
    """
    D0         = "D+0"        # Disponível imediatamente (conta corrente, carteira digital)
    D1         = "D+1"        # Disponível no próximo dia útil (Tesouro Selic, CDB diário)
    D2         = "D+2"        # Liquidação padrão B3 (ações, FIIs, ETFs)
    MATURITY   = "vencimento" # Só disponível no vencimento (CDB sem liquidez, Tesouro Prefixado)


class OperationType(str, enum.Enum):
    """
    Tipo da operação que originou a posição.

    Necessário porque nem toda movimentação é uma compra/venda comercial —
    bonificações e desdobramentos alteram a quantidade sem custo financeiro
    e precisam de tratamento especial no cálculo do preço médio.
    """
    BUY           = "compra"         # Aquisição pagando pelo ativo
    SELL          = "venda"          # Alienação recebendo pelo ativo
    BONUS         = "bonificacao"    # Ações extras distribuídas pela empresa
    SPLIT         = "desdobramento"  # 1 ação vira N (quantidade ↑, preço ↓)
    REVERSE_SPLIT = "grupamento"     # N ações viram 1 (quantidade ↓, preço ↑)
    AMORTIZATION  = "amortizacao"    # Devolução parcial do principal (FIIs, debentures)


# -----------------------------------------------------------------
# Model: Asset
# -----------------------------------------------------------------

class Asset(Base):
    """
    Cadastro de um ativo financeiro — o "produto" em si.

    Não armazena preço atual nem posição — essas informações mudam
    constantemente e vêm de AssetPosition (histórico de operações)
    e de APIs externas (yfinance, python-bcb) em tempo real.
    """

    __tablename__ = "assets"

    # --- Chave primária ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # -----------------------------------------------------------------
    # Identificação
    # -----------------------------------------------------------------

    # Código de negociação do ativo na bolsa ou exchange.
    # Ex: "PETR4" (ação), "HGLG11" (FII), "BTC-USD" (cripto no yfinance).
    # Opcional porque alguns ativos (CDB, previdência) não têm ticker público.
    # unique=True garante que não existam dois cadastros para o mesmo ativo.
    ticker: Mapped[str | None] = mapped_column(
        String(20), nullable=True, unique=True, index=True
    )

    # Nome completo do ativo para exibição na interface.
    # Ex: "Petrobras S.A. PN", "CSHG Logística FII", "Tesouro IPCA+ 2029"
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Classe do ativo — determina regras fiscais, fonte de cotação e liquidez padrão
    asset_type: Mapped[AssetType] = mapped_column(
        Enum(AssetType, name="asset_type_enum"),
        nullable=False,
    )

    # -----------------------------------------------------------------
    # Setor e subsetor (principalmente para renda variável)
    # -----------------------------------------------------------------

    # Setor econômico macroscópico — ex: "Energia", "Financeiro", "Saúde"
    # Usado para analisar diversificação setorial da carteira.
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Subsetor mais específico — ex: "Petróleo e Gás", "Bancos", "Hospitais"
    # Permite análise mais granular dentro de um setor.
    sub_sector: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # -----------------------------------------------------------------
    # Moeda
    # -----------------------------------------------------------------

    # Código ISO 4217 da moeda de negociação do ativo.
    # A maioria dos ativos brasileiros é BRL; ações internacionais
    # podem ser USD, EUR etc. — importante para conversão de câmbio.
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="BRL", server_default="BRL"
    )

    # -----------------------------------------------------------------
    # Atributos exclusivos de renda fixa e Tesouro Direto
    # -----------------------------------------------------------------

    # Referencial de rentabilidade — nulo para renda variável.
    # Ex: um CDB "110% do CDI" tem indexer=CDI; a taxa fica em notes.
    indexer: Mapped[AssetIndexer | None] = mapped_column(
        Enum(AssetIndexer, name="asset_indexer_enum"),
        nullable=True,
    )

    # Data de vencimento do título ou aplicação.
    # Após essa data o capital é devolvido ao investidor.
    # Nulo para ativos de prazo indeterminado (ações, FIIs, cripto).
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # -----------------------------------------------------------------
    # Liquidez
    # -----------------------------------------------------------------

    # Janela de liquidez padrão do ativo.
    # Pode ser sobrescrita pelo usuário para refletir o produto específico
    # (ex: CDB com carência tem liquidez diferente do CDB diário).
    liquidity: Mapped[LiquidityWindow] = mapped_column(
        Enum(LiquidityWindow, name="liquidity_window_enum"),
        nullable=False,
        default=LiquidityWindow.D2,
        server_default=LiquidityWindow.D2.value,
    )

    # -----------------------------------------------------------------
    # Campos auxiliares
    # -----------------------------------------------------------------

    # Anotações livres — taxa contratada, instituição emissora, CNPJ do fundo etc.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # Histórico de operações com este ativo.
    # lazy="raise" porque o volume pode ser grande; use selectinload explícito.
    positions: Mapped[list["AssetPosition"]] = relationship(
        "AssetPosition",
        back_populates="asset",
        cascade="all, delete-orphan",  # apagar o ativo apaga todas as operações
        lazy="raise",
        order_by="AssetPosition.operation_date",
    )

    # -----------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------

    __table_args__ = (
        # Renda fixa e Tesouro devem ter indexador informado.
        # Usa os nomes dos membros do enum (ex: 'FIXED_INCOME', 'TREASURY')
        # porque o SQLAlchemy armazena o .name do enum, não o .value.
        CheckConstraint(
            "NOT (asset_type IN ('FIXED_INCOME', 'TREASURY') AND indexer IS NULL)",
            name="ck_asset_fixed_income_requires_indexer",
        ),
        # Código de moeda deve ter exatamente 3 caracteres
        CheckConstraint("length(currency) = 3", name="ck_asset_currency_length"),
    )

    def __repr__(self) -> str:
        ticker_part = f" [{self.ticker}]" if self.ticker else ""
        return f"<Asset id={self.id}{ticker_part} name={self.name!r} type={self.asset_type}>"


# -----------------------------------------------------------------
# Model: AssetPosition
# -----------------------------------------------------------------

class AssetPosition(Base):
    """
    Registro de uma operação com um ativo — compra, venda ou evento corporativo.

    Cada linha representa uma nota de corretagem ou evento.
    A posição atual e o preço médio são calculados agregando todas as
    operações de um ativo em ordem cronológica:

        posição_atual = SUM(quantity)          -- positivo=comprado, negativo=vendido
        custo_total   = SUM(quantity * price + fees)  -- apenas compras
        preço_médio   = custo_total / posição_atual
    """

    __tablename__ = "asset_positions"

    # --- Chave primária ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # -----------------------------------------------------------------
    # Chaves estrangeiras
    # -----------------------------------------------------------------

    # Ativo ao qual esta operação se refere — obrigatório
    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Conta/corretora onde a operação foi executada.
    # nullable=True porque o usuário pode registrar a operação antes de
    # cadastrar a corretora, ou o ativo pode estar em mais de uma.
    account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -----------------------------------------------------------------
    # Dados da operação
    # -----------------------------------------------------------------

    # Data em que a operação foi executada (pregão, aplicação, evento).
    # Determina a ordem de cálculo do preço médio e o período fiscal.
    operation_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Quantidade do ativo movimentada.
    # Positivo = entrada (compra, bonificação, desdobramento).
    # Negativo = saída (venda, grupamento).
    # Numeric(18, 8) para suportar criptomoedas (ex: 0.00250000 BTC)
    # e desdobramentos fracionários de ETFs.
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)

    # Preço unitário pago ou recebido por cota/ação.
    # Preenchido com 0 em bonificações e desdobramentos (sem custo financeiro).
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(15, 6),  # 6 casas para ativos de baixo preço unitário (cripto, penny stocks)
        nullable=False,
        default=Decimal("0.000000"),
        server_default="0.000000",
    )

    # Custos operacionais da transação: corretagem + emolumentos + taxas B3.
    # Somados ao custo total da compra para calcular o preço médio correto.
    # Em vendas, reduzem o lucro tributável.
    fees: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        default=Decimal("0.0000"),
        server_default="0.0000",
    )

    # Tipo da operação — define como o preço médio é recalculado.
    # Compra: aumenta posição e custo.
    # Venda: reduz posição, mantém preço médio, gera resultado.
    # Bonificação/desdobramento: aumenta quantidade sem alterar custo total.
    operation_type: Mapped[OperationType] = mapped_column(
        Enum(OperationType, name="operation_type_enum"),
        nullable=False,
    )

    # -----------------------------------------------------------------
    # Campos auxiliares
    # -----------------------------------------------------------------

    # Anotações livres — número da nota de corretagem, observações fiscais etc.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # Ativo ao qual a operação pertence
    asset: Mapped["Asset"] = relationship(
        "Asset",
        back_populates="positions",
    )

    # Corretora/conta onde a operação foi executada
    account: Mapped["Account | None"] = relationship(  # type: ignore[name-defined]
        "Account",
        back_populates="asset_positions",
        lazy="joined",
    )

    # -----------------------------------------------------------------
    # Propriedade calculada
    # -----------------------------------------------------------------

    @property
    def total_cost(self) -> Decimal:
        """
        Custo financeiro total da operação: (quantidade × preço) + taxas.

        Para vendas e eventos corporativos sem custo (bonificação,
        desdobramento) o resultado pode ser zero ou negativo — use
        com atenção ao calcular preço médio (filtre só compras).
        """
        return (self.quantity * self.unit_price + self.fees).quantize(Decimal("0.01"))

    # -----------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------

    __table_args__ = (
        # Quantidade não pode ser zero — uma operação sem movimentação não faz sentido
        CheckConstraint("quantity != 0", name="ck_position_nonzero_quantity"),
        # Taxas não podem ser negativas
        CheckConstraint("fees >= 0", name="ck_position_nonneg_fees"),
        # Preço unitário não pode ser negativo
        CheckConstraint("unit_price >= 0", name="ck_position_nonneg_price"),
    )

    def __repr__(self) -> str:
        return (
            f"<AssetPosition id={self.id} asset_id={self.asset_id} "
            f"op={self.operation_type} qty={self.quantity} "
            f"price={self.unit_price} date={self.operation_date}>"
        )
