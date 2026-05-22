"""
Sistema de alertas inteligentes do FinanceHub.

Alertas são verificações automáticas que identificam situações financeiras
que requerem atenção imediata ou planejamento — fiscal, de liquidez ou saúde.

Cada alerta carrega:
  alert_type  — categoria (fiscal, fatura, vencimento, poupança…)
  priority    — ALTA / MEDIA / BAIXA → define cor e posição no dashboard
  title       — texto curto para notificação do sistema operacional
  message     — descrição completa com valores e orientação ao usuário
  data        — dicionário com campos brutos para a UI renderizar detalhes
"""

import calendar
import enum
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.account import CreditCardInvoice, InvoiceStatus
from backend.models.asset import Asset, AssetPosition, AssetType, OperationType
from backend.models.debt import Debt
from backend.models.recurring import RecurringExpense
from backend.repositories.account_repository import AccountRepository
from backend.repositories.asset_repository import AssetRepository
from backend.repositories.transaction_repository import TransactionRepository


# -----------------------------------------------------------------
# Enums de classificação
# -----------------------------------------------------------------

class AlertType(str, enum.Enum):
    """Categoria do alerta — define ícone e seção de exibição no dashboard."""
    DARF            = "darf"             # Obrigação fiscal com a Receita Federal
    INVOICE_DUE     = "fatura_vencendo"  # Fatura de cartão prestes a vencer
    FIXED_INCOME    = "renda_fixa"       # Vencimento de título de renda fixa/Tesouro
    SAVINGS_RATE    = "taxa_poupanca"    # Taxa de poupança abaixo da meta configurada
    COME_COTAS      = "come_cotas"       # Lembrete de come-cotas de fundos (maio/novembro)
    DEBT_DUE        = "parcela_divida"   # Parcela de dívida vencendo em breve
    RECURRING_DUE   = "recorrente"       # Gasto recorrente vencendo / vencido
    LOW_LIQUIDITY   = "liquidez_baixa"   # Liquidez imediata abaixo do mínimo configurado
    REBALANCING     = "rebalanceamento"  # Ativo fora da alocação-alvo
    HIGH_INTEREST   = "juros_altos"      # Juros da dívida superam retorno estimado dos investimentos


class AlertPriority(str, enum.Enum):
    """
    Urgência do alerta.

    HIGH   → vermelho, topo da lista, notificação imediata no SO
    MEDIUM → amarelo, abaixo dos alertas altos
    LOW    → azul, exibido de forma discreta (informativo)
    """
    HIGH   = "alta"
    MEDIUM = "media"
    LOW    = "baixa"


# -----------------------------------------------------------------
# DTO de alerta
# -----------------------------------------------------------------

@dataclass
class Alert:
    """Representa um alerta gerado pelo sistema."""
    alert_type: AlertType
    priority: AlertPriority
    title: str    # curto — usado em notificações do SO (≤ ~50 caracteres)
    message: str  # completo — exibido no card do dashboard com contexto e valores
    data: dict = field(default_factory=dict)  # campos brutos para a UI processar


# -----------------------------------------------------------------
# Serviço
# -----------------------------------------------------------------

class AlertService:
    """
    Serviço de alertas inteligentes.

    Cada método check_* é independente — pode ser chamado isoladamente
    ou via get_all_alerts, que agrega e ordena por prioridade.
    """

    # Limite de isenção de IR em vendas de ações (art. 22, Lei 9.250/1995).
    # Acima de R$20.000 em vendas de ações no mês, o lucro é tributável.
    # FIIs e ETFs têm regras distintas e nunca têm essa isenção.
    DARF_EXEMPTION_LIMIT = Decimal("20000.00")

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._account_repo = AccountRepository(session)
        self._asset_repo = AssetRepository(session)
        self._transaction_repo = TransactionRepository(session)

    # -----------------------------------------------------------------
    # Agregador principal
    # -----------------------------------------------------------------

    async def get_all_alerts(
        self,
        invoice_due_days: int = 3,
        maturity_days_ahead: int = 30,
        savings_goal_pct: Decimal = Decimal("20"),
        debt_due_days: int = 3,
        recurring_due_days: int = 7,
        min_liquidity: Decimal = Decimal("0"),
        enabled: set[str] | None = None,
    ) -> list[Alert]:
        """
        Executa todas as verificações e retorna a lista consolidada, ordenada por prioridade.

        enabled — conjunto de AlertType.value a incluir; None = todos ativos.
        """
        def _ok(t: AlertType) -> bool:
            return enabled is None or t.value in enabled

        alerts: list[Alert] = []

        if _ok(AlertType.DARF):
            alerts.extend(await self.check_darf())
        if _ok(AlertType.INVOICE_DUE):
            alerts.extend(await self.check_invoice_due(days_ahead=invoice_due_days))
        if _ok(AlertType.FIXED_INCOME):
            alerts.extend(await self.check_fixed_income_maturity(days_ahead=maturity_days_ahead))
        if _ok(AlertType.SAVINGS_RATE):
            alerts.extend(await self.check_savings_rate(goal_pct=savings_goal_pct))
        if _ok(AlertType.COME_COTAS):
            alerts.extend(await self.check_come_cotas())
        if _ok(AlertType.DEBT_DUE):
            alerts.extend(await self.check_debt_installment(days_ahead=debt_due_days))
        if _ok(AlertType.RECURRING_DUE):
            alerts.extend(await self.check_recurring_due(days_ahead=recurring_due_days))
        if _ok(AlertType.LOW_LIQUIDITY) and min_liquidity > 0:
            alerts.extend(await self.check_liquidity_threshold(min_liquidity=min_liquidity))
        if _ok(AlertType.REBALANCING):
            alerts.extend(await self.check_rebalancing())
        if _ok(AlertType.HIGH_INTEREST):
            alerts.extend(await self.check_high_interest_debt())

        priority_order = {
            AlertPriority.HIGH: 0,
            AlertPriority.MEDIUM: 1,
            AlertPriority.LOW: 2,
        }
        alerts.sort(key=lambda a: priority_order[a.priority])
        return alerts

    # -----------------------------------------------------------------
    # check_darf — obrigação fiscal de renda variável
    # -----------------------------------------------------------------

    async def check_darf(
        self,
        reference_date: date | None = None,
    ) -> list[Alert]:
        """
        Verifica se o usuário precisa emitir DARF de renda variável no mês corrente.

        Regra fiscal brasileira (art. 22, Lei 9.250/1995):
          • Apenas vendas de AÇÕES têm isenção até R$20.000/mês.
          • Acima disso, o lucro líquido é tributado a 15% (swing trade)
            ou 20% (day trade) e deve ser recolhido via DARF até o último
            dia útil do mês seguinte ao das vendas.
          • FIIs, ETFs, BDRs e cripto NÃO têm essa isenção e são
            tratados em alert separado (versão futura).

        Esta verificação detecta se o VOLUME de vendas superou R$20.000.
        O cálculo do LUCRO LÍQUIDO tributável (descontando preço médio,
        taxas e eventuais prejuízos a compensar) deve ser feito em tela
        específica — esta alerta serve apenas de gatilho para o usuário
        verificar a situação.
        """
        today = reference_date or date.today()
        month_start = today.replace(day=1)

        # Soma o valor bruto de todas as vendas de ações no mês:
        # receita = |quantidade_vendida| × preço_unitário - taxas
        result = await self._session.execute(
            select(
                func.coalesce(
                    func.sum(
                        func.abs(AssetPosition.quantity) * AssetPosition.unit_price
                        - AssetPosition.fees
                    ),
                    Decimal("0.00"),
                ).label("sell_proceeds")
            )
            .join(Asset, AssetPosition.asset_id == Asset.id)
            .where(
                Asset.asset_type == AssetType.STOCK,
                AssetPosition.operation_type == OperationType.SELL,
                AssetPosition.operation_date >= month_start,
                AssetPosition.operation_date <= today,
            )
        )
        sell_proceeds: Decimal = result.scalar_one()

        if sell_proceeds <= self.DARF_EXEMPTION_LIMIT:
            return []

        excess = sell_proceeds - self.DARF_EXEMPTION_LIMIT
        return [
            Alert(
                alert_type=AlertType.DARF,
                priority=AlertPriority.HIGH,
                title="DARF de renda variável",
                message=(
                    f"Vendas de ações em {today.month:02d}/{today.year} somaram "
                    f"R${sell_proceeds:,.2f}, acima do limite de isenção de R$20.000,00. "
                    f"Apure o lucro líquido e emita o DARF até o último dia útil "
                    f"do próximo mês."
                ),
                data={
                    "sell_proceeds": float(sell_proceeds),
                    "exemption_limit": float(self.DARF_EXEMPTION_LIMIT),
                    "excess_over_limit": float(excess),
                    "reference_month": f"{today.month:02d}/{today.year}",
                },
            )
        ]

    # -----------------------------------------------------------------
    # check_invoice_due — faturas de cartão próximas do vencimento
    # -----------------------------------------------------------------

    async def check_invoice_due(
        self,
        days_ahead: int = 3,
        reference_date: date | None = None,
    ) -> list[Alert]:
        """
        Alerta para faturas de cartão de crédito com vencimento próximo.

        Por que alertar com antecedência?
          • Juros do rotativo no Brasil giram em torno de 400% ao ano —
            esquecer o vencimento é um dos erros financeiros mais caros.
          • Com days_ahead=3 o usuário ainda tem tempo de programar um TED/PIX.

        Regras de prioridade:
          • Vencida (due_date < hoje):         HIGH — já há juro sendo cobrado
          • Vence hoje ou amanhã (≤1 dia):     HIGH — risco iminente
          • Vence em 2–days_ahead dias:        MEDIUM — ação necessária em breve

        Faturas PAID são ignoradas — já quitadas, não são mais um risco.
        Faturas OPEN têm valor ainda não final, mas o alerta serve de aviso precoce.
        """
        today = reference_date or date.today()
        alert_threshold = today + timedelta(days=days_ahead)

        accounts = await self._account_repo.get_all_accounts()
        alerts: list[Alert] = []

        for account in accounts:
            for card in account.credit_cards:
                for invoice in card.invoices:
                    if invoice.status == InvoiceStatus.PAID:
                        continue  # já paga, sem risco
                    if invoice.total_amount <= 0:
                        continue  # fatura vazia, sem impacto financeiro
                    if invoice.due_date > alert_threshold:
                        continue  # ainda longe do vencimento

                    days_until_due = (invoice.due_date - today).days

                    if days_until_due < 0:
                        priority = AlertPriority.HIGH
                        when_text = f"venceu há {abs(days_until_due)} dia(s) — juro do rotativo ativo"
                    elif days_until_due <= 1:
                        priority = AlertPriority.HIGH
                        when_text = "vence hoje" if days_until_due == 0 else "vence amanhã"
                    else:
                        priority = AlertPriority.MEDIUM
                        when_text = f"vence em {days_until_due} dia(s)"

                    alerts.append(Alert(
                        alert_type=AlertType.INVOICE_DUE,
                        priority=priority,
                        title=f"Fatura {card.name} — R${invoice.total_amount:,.2f}",
                        message=(
                            f"Fatura {invoice.month:02d}/{invoice.year} do cartão "
                            f"{card.name} (final {card.last_four_digits}) "
                            f"{when_text}: R${invoice.total_amount:,.2f}."
                        ),
                        data={
                            "card_id": card.id,
                            "card_name": card.name,
                            "last_four_digits": card.last_four_digits,
                            "invoice_id": invoice.id,
                            "invoice_month": invoice.month,
                            "invoice_year": invoice.year,
                            "total_amount": float(invoice.total_amount),
                            "due_date": invoice.due_date.isoformat(),
                            "days_until_due": days_until_due,
                            "status": invoice.status.value,
                        },
                    ))

        return alerts

    # -----------------------------------------------------------------
    # check_fixed_income_maturity — vencimentos de renda fixa/Tesouro
    # -----------------------------------------------------------------

    async def check_fixed_income_maturity(
        self,
        days_ahead: int = 30,
        reference_date: date | None = None,
    ) -> list[Alert]:
        """
        Alerta sobre títulos de renda fixa e Tesouro Direto com vencimento próximo.

        Com days_ahead dias de antecedência, o investidor pode:
          • Pesquisar opções de reinvestimento antes que o dinheiro fique parado
            (renda fixa costuma ter carência — o timing importa)
          • Preparar a declaração do IR sobre ganhos de capital no resgate
          • Avaliar a rolagem automática oferecida pela corretora

        Prioridade:
          • ≤ 7 dias:  HIGH — resgate iminente, decisão urgente
          • 8–30 dias: MEDIUM — há tempo para pesquisa, mas ação necessária

        Apenas ativos com posição > 0 são alertados — evita ruído para
        títulos já totalmente resgatados ou vendidos.
        """
        today = reference_date or date.today()
        alert_threshold = today + timedelta(days=days_ahead)

        result = await self._session.execute(
            select(Asset).where(
                Asset.asset_type.in_([AssetType.FIXED_INCOME, AssetType.TREASURY]),
                Asset.maturity_date.is_not(None),
                Asset.maturity_date >= today,          # não vencido ainda
                Asset.maturity_date <= alert_threshold, # dentro do horizonte
            )
        )
        maturing_assets = list(result.scalars().all())

        alerts: list[Alert] = []
        for asset in maturing_assets:
            net_qty = await self._asset_repo.get_consolidated_position(asset.id)
            if net_qty <= 0:
                continue  # posição zerada — título já resgatado

            avg_cost = await self._asset_repo.calculate_avg_price(asset.id)
            estimated_value = (net_qty * avg_cost).quantize(Decimal("0.01"))
            days_to_maturity = (asset.maturity_date - today).days

            priority = AlertPriority.HIGH if days_to_maturity <= 7 else AlertPriority.MEDIUM

            display_name = asset.ticker or asset.name
            alerts.append(Alert(
                alert_type=AlertType.FIXED_INCOME,
                priority=priority,
                title=f"Vencimento: {display_name}",
                message=(
                    f"'{asset.name}' vence em {days_to_maturity} dia(s) "
                    f"({asset.maturity_date.strftime('%d/%m/%Y')}). "
                    f"Valor estimado ao custo: R${estimated_value:,.2f}. "
                    f"Avalie as opções de reinvestimento."
                ),
                data={
                    "asset_id": asset.id,
                    "asset_name": asset.name,
                    "ticker": asset.ticker,
                    "asset_type": asset.asset_type.value,
                    "maturity_date": asset.maturity_date.isoformat(),
                    "days_to_maturity": days_to_maturity,
                    "estimated_value": float(estimated_value),
                    "indexer": asset.indexer.value if asset.indexer else None,
                },
            ))

        return alerts

    # -----------------------------------------------------------------
    # check_savings_rate — taxa de poupança abaixo da meta
    # -----------------------------------------------------------------

    async def check_savings_rate(
        self,
        goal_pct: Decimal = Decimal("20"),
        reference_date: date | None = None,
    ) -> list[Alert]:
        """
        Alerta quando a taxa de poupança do mês corrente fica abaixo da meta.

        Taxa de poupança:
            taxa = (receita - despesa) / receita × 100

        Uma taxa negativa significa que o usuário gastou mais do que ganhou
        — está consumindo reservas ou se endividando.

        Origem da meta de 20%:
          A regra 50/30/20 (necessidades / desejos / poupança) sugere
          guardar ao menos 20% da renda. É um ponto de partida; o usuário
          pode configurar sua própria meta.

        Prioridade:
          • Taxa negativa ou < meta/2:  HIGH — situação crítica
          • Taxa entre meta/2 e meta:   MEDIUM — atenção necessária

        Sem receitas cadastradas, o serviço não alerta — impossível calcular
        a taxa sem o denominador da fórmula.
        """
        today = reference_date or date.today()
        month_start = today.replace(day=1)

        totals = await self._transaction_repo.sum_income_and_expense(month_start, today)
        income = totals["income"]
        expense = totals["expense"]

        if income == 0:
            # Sem receitas: usuário ainda não cadastrou lançamentos este mês
            return []

        savings_rate = ((income - expense) / income * 100).quantize(Decimal("0.01"))

        if savings_rate >= goal_pct:
            return []  # meta atingida — sem alerta

        shortfall = (goal_pct - savings_rate).quantize(Decimal("0.01"))
        half_goal = goal_pct / 2

        if savings_rate < 0:
            priority = AlertPriority.HIGH
            rate_text = f"negativa em {abs(savings_rate):.1f}% (gastou mais do que ganhou)"
        elif savings_rate < half_goal:
            priority = AlertPriority.HIGH
            rate_text = f"{savings_rate:.1f}%"
        else:
            priority = AlertPriority.MEDIUM
            rate_text = f"{savings_rate:.1f}%"

        return [
            Alert(
                alert_type=AlertType.SAVINGS_RATE,
                priority=priority,
                title=f"Poupança {savings_rate:.1f}% — meta {goal_pct:.0f}%",
                message=(
                    f"Taxa de poupança do mês: {rate_text} "
                    f"(meta: {goal_pct:.0f}%). "
                    f"Faltam {shortfall:.1f} p.p. para atingir a meta. "
                    f"Revise os gastos ou aumente a renda para equilibrar."
                ),
                data={
                    "savings_rate_pct": float(savings_rate),
                    "goal_pct": float(goal_pct),
                    "shortfall_pct": float(shortfall),
                    "income": float(income),
                    "expense": float(expense),
                    "reference_month": f"{today.month:02d}/{today.year}",
                },
            )
        ]

    # -----------------------------------------------------------------
    # check_come_cotas — tributação semestral de fundos
    # -----------------------------------------------------------------

    async def check_come_cotas(
        self, reference_date: date | None = None
    ) -> list[Alert]:
        """
        Lembrete de come-cotas: fundos de investimento sofrem tributação
        automática em maio e novembro (último dia útil).

        Alerta em abril e outubro (mês anterior) para o investidor planejar
        o impacto no patrimônio e avaliar resgates antes do evento.
        """
        today = reference_date or date.today()
        if today.month not in (4, 10):
            return []

        next_month = 5 if today.month == 4 else 11
        next_month_name = "Maio" if next_month == 5 else "Novembro"

        return [
            Alert(
                alert_type=AlertType.COME_COTAS,
                priority=AlertPriority.LOW,
                title=f"Come-cotas em {next_month_name}",
                message=(
                    f"Fundos de investimento (exceto imobiliários) sofrem come-cotas "
                    f"no último dia útil de {next_month_name}. O imposto é retido "
                    f"automaticamente pelo administrador. Avalie resgates antes da "
                    f"data se preferir antecipar a liquidez."
                ),
                data={"month": next_month, "month_name": next_month_name},
            )
        ]

    # -----------------------------------------------------------------
    # check_debt_installment — parcelas de dívidas vencendo em breve
    # -----------------------------------------------------------------

    async def check_debt_installment(
        self, days_ahead: int = 3, reference_date: date | None = None
    ) -> list[Alert]:
        """
        Alerta sobre parcelas de dívidas ativas com vencimento próximo.
        """
        today = reference_date or date.today()
        deadline = today + timedelta(days=days_ahead)

        result = await self._session.execute(
            select(Debt).where(
                Debt.is_active == True,
                Debt.paid_installments < Debt.total_installments,
            )
        )
        debts = list(result.scalars().all())
        alerts: list[Alert] = []

        for debt in debts:
            day = min(debt.due_day, 28)
            next_due = date(today.year, today.month, day)
            if next_due < today:
                m = today.month + 1
                y = today.year + (m - 1) // 12
                m = (m - 1) % 12 + 1
                next_due = date(y, m, min(day, 28))

            if next_due > deadline:
                continue

            days_left = (next_due - today).days
            if days_left < 0:
                priority = AlertPriority.HIGH
                when = f"venceu há {abs(days_left)} dia(s)"
            elif days_left <= 1:
                priority = AlertPriority.HIGH
                when = "vence hoje" if days_left == 0 else "vence amanhã"
            else:
                priority = AlertPriority.MEDIUM
                when = f"vence em {days_left} dia(s)"

            alerts.append(Alert(
                alert_type=AlertType.DEBT_DUE,
                priority=priority,
                title=f"Parcela: {debt.name}",
                message=(
                    f"Parcela de '{debt.name}' {when}: "
                    f"R${float(debt.installment_amount):,.2f}. "
                    f"Saldo devedor restante: R${float(debt.remaining_amount):,.2f}."
                ),
                data={
                    "debt_id": debt.id,
                    "debt_name": debt.name,
                    "installment_amount": float(debt.installment_amount),
                    "remaining_amount": float(debt.remaining_amount),
                    "due_date": next_due.isoformat(),
                    "days_left": days_left,
                },
            ))

        return alerts

    # -----------------------------------------------------------------
    # check_recurring_due — gastos recorrentes próximos ou vencidos
    # -----------------------------------------------------------------

    async def check_recurring_due(
        self, days_ahead: int = 7, reference_date: date | None = None
    ) -> list[Alert]:
        """
        Alerta sobre gastos recorrentes (assinaturas, contratos) que estão
        vencidos ou vencendo nos próximos days_ahead dias.
        """
        today = reference_date or date.today()
        deadline = today + timedelta(days=days_ahead)

        result = await self._session.execute(
            select(RecurringExpense).where(RecurringExpense.is_active == True)
        )
        expenses = list(result.scalars().all())
        alerts: list[Alert] = []

        for exp in expenses:
            due = exp.next_due_date
            if due > deadline:
                continue

            days_left = (due - today).days
            if days_left < 0:
                priority = AlertPriority.MEDIUM
                when = f"venceu há {abs(days_left)} dia(s)"
            elif days_left == 0:
                priority = AlertPriority.MEDIUM
                when = "vence hoje"
            else:
                priority = AlertPriority.LOW
                when = f"vence em {days_left} dia(s)"

            alerts.append(Alert(
                alert_type=AlertType.RECURRING_DUE,
                priority=priority,
                title=f"Recorrente: {exp.name}",
                message=(
                    f"'{exp.name}' ({exp.category}) {when}: "
                    f"R${float(exp.amount):,.2f}."
                ),
                data={
                    "expense_id": exp.id,
                    "name": exp.name,
                    "amount": float(exp.amount),
                    "due_date": due.isoformat(),
                    "days_left": days_left,
                },
            ))

        return alerts

    # -----------------------------------------------------------------
    # check_liquidity_threshold — liquidez imediata abaixo do mínimo
    # -----------------------------------------------------------------

    async def check_liquidity_threshold(
        self, min_liquidity: Decimal = Decimal("0"), reference_date: date | None = None
    ) -> list[Alert]:
        """
        Alerta quando o saldo imediato (D+0) cai abaixo do mínimo configurado.
        """
        if min_liquidity <= 0:
            return []

        accounts = await self._account_repo.get_all_accounts()
        total_d0 = Decimal("0")
        for acc in accounts:
            bal = await self._account_repo.get_current_balance(acc.id)
            if bal > 0:
                total_d0 += bal

        if total_d0 >= min_liquidity:
            return []

        shortfall = (min_liquidity - total_d0).quantize(Decimal("0.01"))
        return [
            Alert(
                alert_type=AlertType.LOW_LIQUIDITY,
                priority=AlertPriority.HIGH,
                title=f"Liquidez baixa: R${float(total_d0):,.2f}",
                message=(
                    f"Saldo disponível imediato (D+0): R${float(total_d0):,.2f}, "
                    f"abaixo do mínimo configurado de R${float(min_liquidity):,.2f}. "
                    f"Faltam R${float(shortfall):,.2f}. Considere resgatar um ativo D+1."
                ),
                data={
                    "current_d0": float(total_d0),
                    "min_liquidity": float(min_liquidity),
                    "shortfall": float(shortfall),
                },
            )
        ]

    # -----------------------------------------------------------------
    # check_rebalancing — ativo fora da alocação-alvo
    # -----------------------------------------------------------------

    async def check_rebalancing(self) -> list[Alert]:
        """
        Alerta quando um ativo está com peso ≥ 5 p.p. fora da alocação-alvo.

        Requer que o ativo tenha target_allocation_pct definido.
        O peso atual é calculado como: custo_ativo / custo_total_carteira.
        """
        all_assets = await self._asset_repo.get_all()
        if not all_assets:
            return []

        costs: dict[int, Decimal] = {}
        for asset in all_assets:
            qty  = await self._asset_repo.get_consolidated_position(asset.id)
            avg  = await self._asset_repo.calculate_avg_price(asset.id)
            costs[asset.id] = (qty * avg).quantize(Decimal("0.01"))

        total = sum(costs.values()) or Decimal("1")
        alerts: list[Alert] = []

        for asset in all_assets:
            target = getattr(asset, "target_allocation_pct", None)
            if not target or target <= 0:
                continue
            actual_pct = (costs[asset.id] / total * 100).quantize(Decimal("0.1"))
            deviation  = abs(actual_pct - target)
            if deviation < Decimal("5"):
                continue

            direction = "acima" if actual_pct > target else "abaixo"
            alerts.append(Alert(
                alert_type=AlertType.REBALANCING,
                priority=AlertPriority.LOW,
                title=f"Rebalanceamento: {asset.ticker or asset.name}",
                message=(
                    f"'{asset.ticker or asset.name}' está {float(actual_pct):.1f}% da carteira "
                    f"({direction} da meta de {float(target):.1f}%, desvio de {float(deviation):.1f} p.p.). "
                    f"Considere rebalancear comprando/vendendo para voltar à alocação-alvo."
                ),
                data={
                    "asset_id": asset.id,
                    "ticker": asset.ticker,
                    "name": asset.name,
                    "actual_pct": float(actual_pct),
                    "target_pct": float(target),
                    "deviation_pct": float(deviation),
                },
            ))

        return alerts

    # -----------------------------------------------------------------
    # check_high_interest_debt — juros da dívida > retorno estimado
    # -----------------------------------------------------------------

    async def check_high_interest_debt(
        self,
        benchmark_annual_rate: Decimal = Decimal("0.1275"),  # CDI ≈ 12,75 % a.a.
    ) -> list[Alert]:
        """
        Alerta quando a taxa de juros mensal de uma dívida supera a rentabilidade
        estimada dos investimentos (benchmark padrão: CDI).

        Lógica:
          - Converte a taxa mensal da dívida para taxa anual equivalente:
            taxa_anual = (1 + taxa_mensal)^12 - 1
          - Compara com o benchmark anual (padrão CDI ≈ 12,75 % a.a.).
          - Emite alerta MÉDIO se taxa_anual_dívida > benchmark_anual.

        Contexto: quando os juros de uma dívida superam o retorno esperado
        dos investimentos, é financeiramente mais eficiente usar o capital
        investido para quitar a dívida antecipadamente.
        """
        result = await self._session.execute(
            select(Debt).where(Debt.is_active == True)  # noqa: E712
        )
        debts = result.scalars().all()

        if not debts:
            return []

        alerts: list[Alert] = []

        for debt in debts:
            # Taxa mensal cadastrada (ex.: 0.025 = 2,5% a.m.)
            monthly_rate = debt.interest_rate / Decimal("100")  # converte % para decimal

            # Taxa anual equivalente: (1 + r_mensal)^12 - 1
            annual_rate = ((1 + monthly_rate) ** 12 - 1).quantize(Decimal("0.0001"))

            if annual_rate <= benchmark_annual_rate:
                continue  # juros da dívida estão dentro do aceitável

            # Calcula quantos p.p. a dívida supera o benchmark
            excess_pp = ((annual_rate - benchmark_annual_rate) * 100).quantize(Decimal("0.1"))
            annual_pct = (annual_rate * 100).quantize(Decimal("0.1"))
            benchmark_pct = (benchmark_annual_rate * 100).quantize(Decimal("0.1"))

            alerts.append(Alert(
                alert_type=AlertType.HIGH_INTEREST,
                priority=AlertPriority.MEDIUM,
                title=f"Juros altos: {debt.name}",
                message=(
                    f"'{debt.name}' cobra {float(debt.interest_rate):.2f}% a.m. "
                    f"({float(annual_pct):.1f}% a.a. equiv.), "
                    f"{float(excess_pp):.1f} p.p. acima do benchmark "
                    f"({float(benchmark_pct):.1f}% a.a. CDI). "
                    "Considere antecipar parcelas usando parte dos investimentos."
                ),
                data={
                    "debt_id": debt.id,
                    "debt_name": debt.name,
                    "monthly_rate_pct": float(debt.interest_rate),
                    "annual_rate_pct": float(annual_pct),
                    "benchmark_annual_pct": float(benchmark_pct),
                    "excess_pp": float(excess_pp),
                },
            ))

        return alerts
