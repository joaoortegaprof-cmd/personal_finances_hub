"""
Serviço de análise de liquidez da carteira do FinanceHub.

Liquidez define em quantos dias úteis o investidor pode resgatar
o dinheiro SEM incorrer em perdas (deságio, impostos antecipados,
multa de carência etc.).

Janelas suportadas (enum LiquidityWindow):
  D+0 — disponível hoje:
    conta corrente, dinheiro em espécie, carteira digital (Nubank, PicPay…)

  D+1 — próximo dia útil:
    Tesouro Selic, CDB de liquidez diária

  D+2 — dois dias úteis (prazo padrão de liquidação da B3):
    ações, FIIs, ETFs

  Vencimento — capital disponível apenas na data de expiração:
    CDB com carência, Tesouro Prefixado/IPCA+ (resgatar antes pode
    resultar em deságio: o valor de mercado pode estar abaixo do
    valor de face quando os juros subiram desde a aplicação)
"""

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.asset import Asset, LiquidityWindow
from backend.repositories.account_repository import AccountRepository
from backend.repositories.asset_repository import AssetRepository


# -----------------------------------------------------------------
# DTOs de saída
# -----------------------------------------------------------------

@dataclass
class AssetLiquidityItem:
    """Liquidez de um ativo individual da carteira."""
    asset_id: int
    ticker: str | None          # None para CDBs e ativos sem código público
    name: str
    liquidity_window: LiquidityWindow
    net_quantity: Decimal       # posição líquida: SUM(quantity) de todas as operações
    avg_cost: Decimal           # preço médio de aquisição (CMP aproximado)
    estimated_value: Decimal    # net_quantity × avg_cost — proxy do valor atual
    has_loss_on_early_redemption: bool  # True = risco de deságio se resgatar antes


@dataclass
class LiquidityBreakdown:
    """
    Distribuição do patrimônio total por janela de liquidez.

    Permite responder: "se eu precisar de R$X agora/amanhã/em 2 dias,
    tenho disponível sem vender nada com deságio?"
    """
    d0_value: Decimal      # contas + ativos D+0
    d1_value: Decimal      # ativos D+1
    d2_value: Decimal      # ativos D+2
    maturity_value: Decimal  # ativos presos até vencimento
    total_liquid: Decimal  # d0 + d1 + d2 — acessível sem perdas
    total_portfolio: Decimal  # total_liquid + maturity — valor total estimado
    items: list[AssetLiquidityItem] = field(default_factory=list)


# -----------------------------------------------------------------
# Serviço
# -----------------------------------------------------------------

class LiquidityService:
    """
    Serviço de análise de liquidez da carteira.

    Combina saldos de contas bancárias (sempre D+0) com as posições
    de investimentos classificadas pela janela de cada ativo.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._asset_repo = AssetRepository(session)
        self._account_repo = AccountRepository(session)

    async def get_liquidity_breakdown(self) -> LiquidityBreakdown:
        """
        Calcula o valor disponível em cada janela de liquidez.

        Valor dos investimentos usa o custo médio como proxy do preço
        atual — suficiente para planejamento de liquidez sem depender
        de chamadas externas ao yfinance. Para precisão de mercado,
        multiplique net_quantity pela cotação do MarketDataService.

        Algoritmo:
          1. Soma o saldo positivo de todas as contas em D+0.
          2. Para cada janela de liquidez, consulta os ativos classificados
             naquela janela e calcula: net_qty × avg_cost.
          3. Agrega os totais e monta o breakdown.
        """
        items: list[AssetLiquidityItem] = []
        window_totals: dict[LiquidityWindow, Decimal] = {
            w: Decimal("0.00") for w in LiquidityWindow
        }

        # ── Passo 1: contas bancárias e carteiras digitais (D+0) ────────
        # Saldo em conta é acessível imediatamente — não há prazo de
        # liquidação ou carência. Ignora saldos negativos (cheque especial)
        # porque representam dívida, não liquidez.
        accounts = await self._account_repo.get_all_accounts()
        for account in accounts:
            balance = await self._account_repo.get_current_balance(account.id)
            if balance > 0:
                window_totals[LiquidityWindow.D0] += balance

        # ── Passo 2: ativos classificados por janela de liquidez ─────────
        for window in LiquidityWindow:
            # get_by_liquidity já carrega as posições via selectinload —
            # evita N+1 queries ao calcular posição e custo de cada ativo
            assets = await self._asset_repo.get_by_liquidity(window)
            for asset in assets:
                item = await self._build_asset_item(asset, window)
                # Ignora ativos com posição zero (totalmente vendidos/resgatados)
                if item.estimated_value > 0:
                    items.append(item)
                    window_totals[window] += item.estimated_value

        d0 = window_totals[LiquidityWindow.D0]
        d1 = window_totals[LiquidityWindow.D1]
        d2 = window_totals[LiquidityWindow.D2]
        maturity = window_totals[LiquidityWindow.MATURITY]

        return LiquidityBreakdown(
            d0_value=d0,
            d1_value=d1,
            d2_value=d2,
            maturity_value=maturity,
            total_liquid=d0 + d1 + d2,
            total_portfolio=d0 + d1 + d2 + maturity,
            items=sorted(
                items,
                # Ordena por janela (mais líquido primeiro), depois por nome
                key=lambda x: (list(LiquidityWindow).index(x.liquidity_window), x.name),
            ),
        )

    async def get_available_by_window(self, window: LiquidityWindow) -> Decimal:
        """
        Retorna o valor total disponível em uma janela específica.

        Atalho para quando a tela precisa de apenas um número
        (ex.: "quanto posso resgatar hoje?") sem o breakdown completo.
        """
        breakdown = await self.get_liquidity_breakdown()
        return {
            LiquidityWindow.D0: breakdown.d0_value,
            LiquidityWindow.D1: breakdown.d1_value,
            LiquidityWindow.D2: breakdown.d2_value,
            LiquidityWindow.MATURITY: breakdown.maturity_value,
        }[window]

    async def get_emergency_fund_coverage(
        self,
        monthly_expense: Decimal,
        target_months: int = 6,
    ) -> dict:
        """
        Calcula quantos meses de despesas o dinheiro líquido (D+0 + D+1) cobre.

        Referência: especialistas em finanças pessoais recomendam entre 3 e 6
        meses de despesas como reserva de emergência. Usamos D+0 + D+1 porque
        são as janelas resgatáveis em até 1 dia útil sem perda.

        Retorna um dict com:
          current_liquid: valor total em D+0 + D+1
          target_value:   meta em reais (monthly_expense × target_months)
          coverage_months: quantos meses o valor atual cobre
          progress_pct:   progresso em relação à meta (0–100+)
        """
        breakdown = await self.get_liquidity_breakdown()
        liquid = breakdown.d0_value + breakdown.d1_value
        target = monthly_expense * target_months

        coverage_months = (
            float(liquid / monthly_expense)
            if monthly_expense > 0
            else 0.0
        )
        progress_pct = (
            float(liquid / target * 100)
            if target > 0
            else 100.0  # sem meta, considera atingida
        )

        return {
            "current_liquid": liquid,
            "target_value": target,
            "coverage_months": round(coverage_months, 1),
            "progress_pct": round(min(progress_pct, 100.0), 1),
            "is_sufficient": liquid >= target,
        }

    # -----------------------------------------------------------------
    # Helpers privados
    # -----------------------------------------------------------------

    async def _build_asset_item(
        self, asset: Asset, window: LiquidityWindow
    ) -> AssetLiquidityItem:
        """
        Constrói o item de liquidez para um ativo.

        Chama o repositório para calcular posição líquida e preço médio
        separadamente — duas queries pequenas e indexadas por asset_id.
        Em volume alto (>500 ativos), otimize agrupando num único SELECT.

        Ativos com preço médio = 0 (dados incompletos/inválidos) são marcados
        com estimated_value = 0 para que o chamador os ignore no breakdown —
        evita distorção do total de liquidez por posições sem custo registrado.
        """
        net_quantity = await self._asset_repo.get_consolidated_position(asset.id)
        avg_cost = await self._asset_repo.calculate_avg_price(asset.id)

        # Ignorar ativos cujo preço médio não pôde ser calculado (sem BUYs
        # com price > 0).  Valor zero preserva o filtro `estimated_value > 0`
        # do chamador sem introduzir valores inflados ou negativos no breakdown.
        if avg_cost <= Decimal("0"):
            estimated_value = Decimal("0.00")
        else:
            estimated_value = (net_quantity * avg_cost).quantize(Decimal("0.01"))

        # Ativos com vencimento têm risco de deságio por marcação a mercado:
        # Tesouro Prefixado e IPCA+ podem valer menos que o custo de compra
        # quando os juros de mercado sobem após a aplicação.
        has_loss_on_early_redemption = (window == LiquidityWindow.MATURITY)

        return AssetLiquidityItem(
            asset_id=asset.id,
            ticker=asset.ticker,
            name=asset.name,
            liquidity_window=window,
            net_quantity=net_quantity,
            avg_cost=avg_cost,
            estimated_value=estimated_value,
            has_loss_on_early_redemption=has_loss_on_early_redemption,
        )
