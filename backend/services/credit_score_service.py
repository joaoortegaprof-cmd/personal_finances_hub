"""
Serviço de Score de Crédito Pessoal do FinanceHub.

Inspirado na metodologia dos bureaus de crédito brasileiros (Serasa, SPC),
mas adaptado para o contexto de finanças pessoais — não usa dados externos.

Score de 0 a 1.000, composto por 5 fatores:

  1. Histórico de pagamentos (0–350 pts) — 35%
     Faturas de cartão pagas no prazo vs vencidas/não pagas.
     Cada fatura PAID pontua positivo; CLOSED e OPEN com atraso pontuam negativo.

  2. Utilização de crédito (0–300 pts) — 30%
     (saldo_utilizado / limite_total) — menor é melhor.
     < 10%  → 300 pts   (excelente controle)
     10-30% → 240 pts
     30-50% → 150 pts
     50-70% → 80 pts
     > 70%  → 0 pts     (limite muito comprometido)

  3. Carga de dívidas (0–150 pts) — 15%
     Parcelas mensais / renda mensal (debt-service ratio).
     < 10%  → 150 pts
     10-20% → 120 pts
     20-30% → 80 pts
     30-40% → 40 pts
     > 40%  → 0 pts

  4. Reserva de emergência (0–100 pts) — 10%
     Cobertura em meses (saldo_liquido / despesa_mensal).
     ≥ 6 meses → 100 pts
     3-6 meses → 70 pts
     1-3 meses → 40 pts
     < 1 mês   → 0 pts

  5. Taxa de poupança (0–100 pts) — 10%
     (renda - despesa) / renda.
     ≥ 30%  → 100 pts
     20-30% → 80 pts
     10-20% → 50 pts
     5-10%  → 20 pts
     < 5%   → 0 pts

Rating (badge):
  900–1.000 → Excelente  (verde)
  750–899   → Muito Bom  (verde-claro)
  600–749   → Bom        (azul)
  450–599   → Regular    (amarelo)
  300–449   → Ruim       (laranja)
  0–299     → Muito Ruim (vermelho)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.account import CreditCard, CreditCardInvoice, InvoiceStatus
from backend.models.debt import Debt
from backend.repositories.account_repository import AccountRepository
from backend.repositories.transaction_repository import TransactionRepository


# -----------------------------------------------------------------
# DTOs
# -----------------------------------------------------------------

@dataclass
class ScoreComponent:
    """Um componente do score com nome, pontos obtidos e pontos máximos."""
    name:        str
    score:       int    # pontos obtidos
    max_score:   int    # pontos máximos possíveis
    weight_pct:  int    # peso no score total (%)
    description: str    # explicação textual do resultado
    details:     dict   = field(default_factory=dict)


@dataclass
class CreditScore:
    """Score de crédito pessoal completo."""
    total:      int                     # 0–1000
    rating:     str                     # Excelente / Muito Bom / Bom / Regular / Ruim / Muito Ruim
    color:      str                     # cor hex do badge
    components: list[ScoreComponent]    # detalhamento por fator
    details:    dict = field(default_factory=dict)  # dados brutos para UI

    @property
    def pct(self) -> float:
        """Percentual do score máximo (0.0–100.0)."""
        return self.total / 10.0


# -----------------------------------------------------------------
# Constantes de rating
# -----------------------------------------------------------------

_RATINGS = [
    (900, "Excelente",  "#0a7a4a"),  # verde escuro
    (750, "Muito Bom",  "#2e9e68"),  # verde médio
    (600, "Bom",        "#2e6abf"),  # azul
    (450, "Regular",    "#b8860b"),  # amarelo escuro
    (300, "Ruim",       "#cc6600"),  # laranja
    (0,   "Muito Ruim", "#b71c1c"),  # vermelho
]


def _get_rating(score: int) -> tuple[str, str]:
    """Retorna (label, cor_hex) para o score."""
    for threshold, label, color in _RATINGS:
        if score >= threshold:
            return label, color
    return "Muito Ruim", "#b71c1c"


# -----------------------------------------------------------------
# Serviço
# -----------------------------------------------------------------

class CreditScoreService:
    """
    Calcula o score de crédito pessoal do usuário.

    Não chama APIs externas — usa apenas os dados do banco local.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._account_repo = AccountRepository(session)
        self._tx_repo = TransactionRepository(session)

    async def calculate(self) -> CreditScore:
        """
        Calcula o score completo e retorna um objeto CreditScore.

        O cálculo é feito de forma assíncrona para não bloquear a thread.
        """
        components = []

        c1 = await self._payment_history()
        c2 = await self._credit_utilization()
        c3 = await self._debt_burden()
        c4 = await self._emergency_reserve()
        c5 = await self._savings_rate()

        components = [c1, c2, c3, c4, c5]
        total = sum(c.score for c in components)

        rating, color = _get_rating(total)

        return CreditScore(
            total=total,
            rating=rating,
            color=color,
            components=components,
            details={
                "payment_history_pct": round(c1.score / c1.max_score * 100, 1),
                "utilization_pct": c2.details.get("utilization_pct", 0),
                "debt_service_ratio": c3.details.get("debt_service_ratio", 0),
                "emergency_months": c4.details.get("coverage_months", 0),
                "savings_rate_pct": c5.details.get("savings_rate_pct", 0),
            },
        )

    # -----------------------------------------------------------------
    # Componente 1: Histórico de pagamentos (0–350 pts)
    # -----------------------------------------------------------------

    async def _payment_history(self) -> ScoreComponent:
        """
        Avalia o histórico de pagamento de faturas de cartão.

        Faturas PAID contribuem positivamente.
        Faturas CLOSED (fechadas mas não pagas) e faturas em aberto
        há muito tempo reduzem o score.
        """
        stmt = select(CreditCardInvoice)
        result = await self._session.execute(stmt)
        invoices = result.scalars().all()

        total  = len(invoices)
        paid   = sum(1 for i in invoices if i.status == InvoiceStatus.PAID)
        closed = sum(1 for i in invoices if i.status == InvoiceStatus.CLOSED)
        # Faturas abertas com vencimento passado = inadimplência
        today = date.today()
        overdue = sum(
            1 for i in invoices
            if i.status == InvoiceStatus.OPEN
            and hasattr(i, "due_date")
            and i.due_date is not None
            and i.due_date < today
        )

        if total == 0:
            # Sem histórico: score neutro (50% do máximo)
            score = 175
            desc = "Sem histórico de faturas. Score neutro aplicado."
        else:
            pay_rate = paid / total
            # Penaliza faturas fechadas não pagas e atrasadas
            penalty = (closed * 0.5 + overdue * 1.0) / max(total, 1)
            raw = max(0.0, pay_rate - penalty)
            score = int(raw * 350)
            score = max(0, min(350, score))
            desc = (
                f"{paid}/{total} faturas pagas em dia "
                f"({overdue} em atraso, {closed} fechadas pendentes)."
            )

        return ScoreComponent(
            name="Histórico de Pagamentos",
            score=score,
            max_score=350,
            weight_pct=35,
            description=desc,
            details={"paid": paid, "total": total, "overdue": overdue, "closed_unpaid": closed},
        )

    # -----------------------------------------------------------------
    # Componente 2: Utilização de crédito (0–300 pts)
    # -----------------------------------------------------------------

    async def _credit_utilization(self) -> ScoreComponent:
        """
        Avalia o percentual do limite de crédito em uso.

        Baixa utilização (< 10%) é o melhor sinal de saúde creditícia.
        """
        stmt = select(CreditCard)
        result = await self._session.execute(stmt)
        cards = result.scalars().all()

        total_limit = sum(c.credit_limit for c in cards)
        if total_limit == 0:
            return ScoreComponent(
                name="Utilização de Crédito",
                score=200,   # sem cartão: score médio
                max_score=300,
                weight_pct=30,
                description="Sem cartões de crédito cadastrados. Score médio aplicado.",
                details={"utilization_pct": 0.0},
            )

        # Saldo utilizado = total de faturas OPEN (em uso agora)
        inv_stmt = select(CreditCardInvoice).where(
            CreditCardInvoice.status == InvoiceStatus.OPEN
        )
        inv_result = await self._session.execute(inv_stmt)
        open_invoices = inv_result.scalars().all()
        used = sum(i.total_amount for i in open_invoices)

        utilization_pct = float(used / total_limit * 100)

        if utilization_pct < 10:
            score, desc = 300, f"Excelente! Apenas {utilization_pct:.1f}% do limite utilizado."
        elif utilization_pct < 30:
            score, desc = 240, f"Bom. {utilization_pct:.1f}% do limite utilizado."
        elif utilization_pct < 50:
            score, desc = 150, f"Regular. {utilization_pct:.1f}% do limite comprometido."
        elif utilization_pct < 70:
            score, desc = 80, f"Alto. {utilization_pct:.1f}% do limite em uso."
        else:
            score, desc = 0, f"Crítico! {utilization_pct:.1f}% do limite comprometido."

        return ScoreComponent(
            name="Utilização de Crédito",
            score=score,
            max_score=300,
            weight_pct=30,
            description=desc,
            details={
                "total_limit": float(total_limit),
                "used": float(used),
                "utilization_pct": round(utilization_pct, 1),
            },
        )

    # -----------------------------------------------------------------
    # Componente 3: Carga de dívidas (0–150 pts)
    # -----------------------------------------------------------------

    async def _debt_burden(self) -> ScoreComponent:
        """
        Avalia o quanto as parcelas mensais de dívidas comprometem a renda.
        """
        # Soma das parcelas mensais ativas
        stmt = select(Debt).where(
            Debt.is_active.is_(True),
            Debt.paid_installments < Debt.total_installments,
        )
        result = await self._session.execute(stmt)
        debts = result.scalars().all()

        monthly_debt_payments = sum(d.installment_amount for d in debts)

        # Renda mensal: média dos últimos 3 meses
        today = date.today()
        incomes = []
        for delta in range(3):
            m = today.month - delta
            y = today.year
            if m <= 0:
                m += 12
                y -= 1
            import calendar as _cal
            last = _cal.monthrange(y, m)[1]
            summary = await self._tx_repo.sum_income_and_expense(
                date(y, m, 1), date(y, m, last)
            )
            incomes.append(float(summary["income"]))

        monthly_income = sum(incomes) / 3 if incomes else 0

        if monthly_income == 0:
            score = 75  # neutro
            ratio = 0.0
            desc = "Sem renda registrada. Score neutro aplicado."
        else:
            ratio = float(monthly_debt_payments / Decimal(str(monthly_income)) * 100)
            if ratio < 10:
                score, desc = 150, f"Ótimo! Parcelas representam apenas {ratio:.1f}% da renda."
            elif ratio < 20:
                score, desc = 120, f"Bom. {ratio:.1f}% da renda comprometida com dívidas."
            elif ratio < 30:
                score, desc = 80, f"Regular. {ratio:.1f}% da renda em parcelas."
            elif ratio < 40:
                score, desc = 40, f"Alto. {ratio:.1f}% da renda comprometida."
            else:
                score, desc = 0, f"Crítico! {ratio:.1f}% da renda em dívidas."

        return ScoreComponent(
            name="Carga de Dívidas",
            score=score,
            max_score=150,
            weight_pct=15,
            description=desc,
            details={
                "monthly_debt_payments": float(monthly_debt_payments),
                "monthly_income": monthly_income,
                "debt_service_ratio": round(ratio, 1),
                "active_debts": len(debts),
            },
        )

    # -----------------------------------------------------------------
    # Componente 4: Reserva de emergência (0–100 pts)
    # -----------------------------------------------------------------

    async def _emergency_reserve(self) -> ScoreComponent:
        """
        Avalia a cobertura da reserva de emergência em meses de despesa.
        """
        accounts = await self._account_repo.get_all_accounts()
        liquid = Decimal("0")
        for acc in accounts:
            bal = await self._account_repo.get_current_balance(acc.id)
            if bal > 0:
                liquid += bal

        today = date.today()
        import calendar as _cal
        last = _cal.monthrange(today.year, today.month)[1]
        monthly = await self._tx_repo.sum_income_and_expense(
            date(today.year, today.month, 1), date(today.year, today.month, last)
        )
        monthly_expense = float(monthly["expense"])

        if monthly_expense == 0:
            coverage = 0.0
            score, desc = 50, "Sem despesas registradas. Score neutro aplicado."
        else:
            coverage = float(liquid) / monthly_expense
            if coverage >= 6:
                score, desc = 100, f"Excelente! Reserva cobre {coverage:.1f} meses de despesas."
            elif coverage >= 3:
                score, desc = 70, f"Bom. Reserva cobre {coverage:.1f} meses (meta: 6)."
            elif coverage >= 1:
                score, desc = 40, f"Regular. Reserva cobre {coverage:.1f} meses (meta: 6)."
            else:
                score, desc = 0, f"Crítico! Reserva cobre apenas {coverage:.1f} meses."

        return ScoreComponent(
            name="Reserva de Emergência",
            score=score,
            max_score=100,
            weight_pct=10,
            description=desc,
            details={
                "liquid_balance": float(liquid),
                "monthly_expense": monthly_expense,
                "coverage_months": round(coverage, 1),
            },
        )

    # -----------------------------------------------------------------
    # Componente 5: Taxa de poupança (0–100 pts)
    # -----------------------------------------------------------------

    async def _savings_rate(self) -> ScoreComponent:
        """
        Avalia o percentual da renda que está sendo poupado.
        """
        today = date.today()
        import calendar as _cal
        last = _cal.monthrange(today.year, today.month)[1]
        monthly = await self._tx_repo.sum_income_and_expense(
            date(today.year, today.month, 1), date(today.year, today.month, last)
        )

        income  = float(monthly["income"])
        expense = float(monthly["expense"])

        if income == 0:
            return ScoreComponent(
                name="Taxa de Poupança",
                score=50,
                max_score=100,
                weight_pct=10,
                description="Sem renda registrada este mês. Score neutro aplicado.",
                details={"savings_rate_pct": 0.0},
            )

        rate = max(0.0, (income - expense) / income * 100)

        if rate >= 30:
            score, desc = 100, f"Excelente! Você está poupando {rate:.1f}% da renda."
        elif rate >= 20:
            score, desc = 80, f"Muito bom. Taxa de poupança: {rate:.1f}%."
        elif rate >= 10:
            score, desc = 50, f"Regular. Poupar mais pode melhorar seu score."
        elif rate >= 5:
            score, desc = 20, f"Baixo. Taxa de poupança: {rate:.1f}%."
        else:
            score, desc = 0, f"Atenção! Você está poupando apenas {rate:.1f}%."

        return ScoreComponent(
            name="Taxa de Poupança",
            score=score,
            max_score=100,
            weight_pct=10,
            description=desc,
            details={"savings_rate_pct": round(rate, 1), "income": income, "expense": expense},
        )


# -----------------------------------------------------------------
# Função standalone para o endpoint (compatível com o padrão do projeto)
# -----------------------------------------------------------------

async def get_credit_score(db: AsyncSession) -> CreditScore:
    """Ponto de entrada do endpoint GET /credit-score."""
    service = CreditScoreService(db)
    return await service.calculate()
