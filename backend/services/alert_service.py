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

import enum
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.account import CreditCardInvoice, InvoiceStatus
from backend.models.asset import Asset, AssetPosition, AssetType, OperationType
from backend.repositories.account_repository import AccountRepository
from backend.repositories.asset_repository import AssetRepository
from backend.repositories.transaction_repository import TransactionRepository


# -----------------------------------------------------------------
# Enums de classificação
# -----------------------------------------------------------------

class AlertType(str, enum.Enum):
    """Categoria do alerta — define ícone e seção de exibição no dashboard."""
    DARF         = "darf"            # Obrigação fiscal com a Receita Federal
    INVOICE_DUE  = "fatura_vencendo" # Fatura de cartão prestes a vencer
    FIXED_INCOME = "renda_fixa"      # Vencimento de título de renda fixa/Tesouro
    SAVINGS_RATE = "taxa_poupanca"   # Taxa de poupança abaixo da meta configurada


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

    Extensão futura: adicionar check_rebalancing() para alertar quando
    um ativo sair da alocação alvo, e check_high_interest_debt() para
    avisar quando juros de uma dívida superam a rentabilidade dos investimentos.
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
    ) -> list[Alert]:
        """
        Executa todas as verificações e retorna a lista consolidada, ordenada por prioridade.

        Parâmetros configuráveis:
          invoice_due_days    — quantos dias antes do vencimento alertar faturas
          maturity_days_ahead — horizonte de vencimentos de renda fixa a verificar
          savings_goal_pct    — meta de taxa de poupança em % (padrão: 20%)

        Alertas ALTA aparecem primeiro, depois MEDIA e BAIXA — o usuário
        vê imediatamente o que precisa de ação urgente.
        """
        alerts: list[Alert] = []

        alerts.extend(await self.check_darf())
        alerts.extend(await self.check_invoice_due(days_ahead=invoice_due_days))
        alerts.extend(await self.check_fixed_income_maturity(days_ahead=maturity_days_ahead))
        alerts.extend(await self.check_savings_rate(goal_pct=savings_goal_pct))

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
