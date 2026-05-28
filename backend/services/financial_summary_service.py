"""
Serviço de resumo financeiro do FinanceHub.

Agrega dados de contas, transações e investimentos para gerar
a visão consolidada do patrimônio e da saúde financeira do usuário.

Fluxo de dados:
  AccountRepository   → saldo atual de cada conta
  TransactionRepository → receitas e despesas do período
  AssetRepository     → custo de aquisição da carteira de investimentos
"""

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.account import InvoiceStatus
from backend.models.asset import Asset
from backend.repositories.account_repository import AccountRepository
from backend.repositories.asset_repository import AssetRepository
from backend.repositories.transaction_repository import TransactionRepository


# -----------------------------------------------------------------
# DTOs de saída (dataclasses imutáveis por convenção)
# -----------------------------------------------------------------

@dataclass
class MonthlySummary:
    """Resumo financeiro do mês: receitas, despesas e indicadores derivados."""
    income: Decimal          # soma de todas as receitas do mês
    expense: Decimal         # soma de todas as despesas do mês
    balance: Decimal         # income - expense (pode ser negativo)
    savings_rate: Decimal    # (balance / income) × 100, ou 0,00 se income = 0


@dataclass
class NetWorthSummary:
    """Patrimônio líquido decomposto em ativos e passivos."""
    total_assets: Decimal      # saldo em contas + custo investido
    total_liabilities: Decimal # faturas em aberto + fechadas não pagas
    net_worth: Decimal         # total_assets - total_liabilities
    account_balance: Decimal   # saldo agregado de todas as contas
    investment_cost: Decimal   # custo líquido dos investimentos (compras - vendas)


@dataclass
class HealthScore:
    """Score de saúde financeira de 0 a 100 com decomposição por componente."""
    total: int                    # pontuação global (0-100)
    savings_component: int        # 0-40: baseado na taxa de poupança
    emergency_fund_component: int # 0-40: baseado no fundo de emergência
    debt_component: int           # 0-20: baseado na proporção dívida/renda
    details: dict = field(default_factory=dict)  # dados brutos para a UI detalhar


# -----------------------------------------------------------------
# Serviço
# -----------------------------------------------------------------

class FinancialSummaryService:
    """
    Serviço central de resumo financeiro.

    Orquestra os três repositórios para produzir os indicadores
    exibidos no dashboard principal sem misturar lógica de banco
    com lógica de negócio.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._account_repo = AccountRepository(session)
        self._transaction_repo = TransactionRepository(session)
        self._asset_repo = AssetRepository(session)

    # -----------------------------------------------------------------
    # Resumo mensal
    # -----------------------------------------------------------------

    async def get_monthly_summary(
        self,
        reference_date: date | None = None,
    ) -> MonthlySummary:
        """
        Calcula receitas, despesas e saldo do mês de reference_date.

        Se reference_date não for informado, usa o mês corrente.

        Taxa de poupança:
            taxa = (receita - despesa) / receita × 100

        Quando a receita é zero a taxa retorna 0,00 — o usuário
        provavelmente ainda não registrou lançamentos neste mês.
        """
        today = reference_date or date.today()
        # Primeiro e último dia do mês de referência
        start = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        end = today.replace(day=last_day)

        totals = await self._transaction_repo.sum_income_and_expense(start, end)
        income = totals["income"]
        expense = totals["expense"]
        balance = totals["balance"]

        savings_rate = (
            (balance / income * 100).quantize(Decimal("0.01"))
            if income > 0
            else Decimal("0.00")
        )

        return MonthlySummary(
            income=income,
            expense=expense,
            balance=balance,
            savings_rate=savings_rate,
        )

    # -----------------------------------------------------------------
    # Patrimônio líquido
    # -----------------------------------------------------------------

    async def get_net_worth(self) -> NetWorthSummary:
        """
        Calcula o patrimônio líquido: ativos totais menos passivos totais.

        Ativos computados:
          • Saldo atual de cada conta (corrente, poupança, digital, espécie)
          • Custo de aquisição líquido dos investimentos (compras - vendas)
            — proxy conservador do valor de mercado sem chamadas externas.

        Passivos computados:
          • Faturas de cartão com status OPEN ou CLOSED ainda não pagas.
            Faturas PAID já saíram do caixa e não são passivos.

        Para o valor de mercado real dos investimentos, consulte
        MarketDataService.get_quote() e cruze com a posição líquida.
        """
        accounts = await self._account_repo.get_all_accounts()

        account_balance = Decimal("0.00")
        total_liabilities = Decimal("0.00")

        for account in accounts:
            balance = await self._account_repo.get_current_balance(account.id)
            account_balance += balance

            # Cartões já vêm carregados por selectinload em get_all_accounts
            for card in account.credit_cards:
                for invoice in card.invoices:
                    if invoice.status in (InvoiceStatus.OPEN, InvoiceStatus.CLOSED):
                        # Apenas faturas ainda não pagas representam passivo real
                        total_liabilities += invoice.total_amount

        # Custo investido = soma de (quantidade líquida × preço médio) por ativo.
        # Usamos qty × avg_price em vez de get_portfolio_summary_by_type para
        # evitar distorções causadas por entradas históricas com sinal errado.
        assets_result = await self._session.execute(select(Asset))
        investment_cost = Decimal("0.00")
        for asset in assets_result.scalars().all():
            qty = await self._asset_repo.get_consolidated_position(asset.id)
            if qty <= 0:
                continue
            avg = await self._asset_repo.calculate_avg_price(asset.id)
            investment_cost += qty * avg
        investment_cost = investment_cost.quantize(Decimal("0.01"))

        total_assets = account_balance + investment_cost
        net_worth = total_assets - total_liabilities

        return NetWorthSummary(
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            net_worth=net_worth,
            account_balance=account_balance,
            investment_cost=investment_cost,
        )

    # -----------------------------------------------------------------
    # Score de saúde financeira
    # -----------------------------------------------------------------

    async def get_health_score(
        self,
        emergency_fund_months: int = 6,
        savings_goal_pct: Decimal = Decimal("20"),
    ) -> HealthScore:
        """
        Calcula um score de saúde financeira de 0 a 100.

        Três componentes somados:

        1. Taxa de poupança (0–40 pontos)
           Escala linear até a meta. Acima da meta, pontua o máximo.
           Fórmula: min(40, savings_rate / goal × 40)

        2. Fundo de emergência (0–40 pontos)
           Quanto o saldo em conta cobre das despesas mensais vs a meta.
           Meta padrão: 6× a despesa mensal (recomendação amplamente usada).
           Fórmula: min(1, saldo_líquido / (despesa × meses_meta)) × 40

        3. Proporção dívida/renda (0–20 pontos)
           Faixas de debt-to-income (passivo total ÷ renda mensal):
             < 1×  → 20 pts  (dívida < um salário)
             1–2×  → 15 pts
             2–4×  → 10 pts
             4–6×  →  5 pts
             > 6×  →  0 pts  (situação crítica)

        O score é propositalmente simples e transparente — o usuário
        deve entender de onde vem cada ponto sem precisar de estatística.
        """
        monthly = await self.get_monthly_summary()
        net_worth = await self.get_net_worth()

        # ── Componente 1: taxa de poupança ──────────────────────────────
        savings_pct = float(monthly.savings_rate)
        goal_pct = float(savings_goal_pct)
        if goal_pct > 0 and savings_pct > 0:
            savings_component = min(40, int(savings_pct / goal_pct * 40))
        else:
            savings_component = 0  # sem receita ou taxa negativa: zero pontos

        # ── Componente 2: fundo de emergência ───────────────────────────
        # Usa o saldo em contas como proxy da reserva de emergência.
        # LiquidityService fornece uma visão mais precisa (apenas D+0/D+1),
        # mas o saldo de conta já é uma boa aproximação para o score geral.
        monthly_expense = float(monthly.expense)
        emergency_target = monthly_expense * emergency_fund_months
        liquid_balance = float(net_worth.account_balance)

        if emergency_target > 0:
            coverage_ratio = min(1.0, liquid_balance / emergency_target)
        else:
            # Sem despesas registradas, assume fundo suficiente
            coverage_ratio = 1.0

        emergency_component = int(coverage_ratio * 40)

        # ── Componente 3: proporção de dívidas ──────────────────────────
        monthly_income = float(monthly.income)
        liabilities = float(net_worth.total_liabilities)

        if monthly_income > 0:
            debt_ratio = liabilities / monthly_income
        else:
            # Sem renda registrada, assume posição neutra para não punir
            # usuários que ainda não cadastraram lançamentos
            debt_ratio = 2.0

        if debt_ratio < 1:
            debt_component = 20
        elif debt_ratio < 2:
            debt_component = 15
        elif debt_ratio < 4:
            debt_component = 10
        elif debt_ratio < 6:
            debt_component = 5
        else:
            debt_component = 0

        total = savings_component + emergency_component + debt_component

        # Cobertura em meses para a UI exibir "sua reserva cobre X meses"
        coverage_months = (
            round(liquid_balance / monthly_expense, 1)
            if monthly_expense > 0
            else 0.0
        )

        return HealthScore(
            total=total,
            savings_component=savings_component,
            emergency_fund_component=emergency_component,
            debt_component=debt_component,
            details={
                "savings_rate_pct": savings_pct,
                "savings_goal_pct": goal_pct,
                "liquid_balance": liquid_balance,
                "emergency_target": emergency_target,
                "emergency_coverage_months": coverage_months,
                "debt_to_income_ratio": round(debt_ratio, 2),
            },
        )
