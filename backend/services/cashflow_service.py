"""
Serviço de projeção de fluxo de caixa futuro do FinanceHub.

Cruza quatro fontes de dados para construir eventos futuros:
  1. Gastos recorrentes (assinaturas, contratos) — via next_due_date + periodicidade
  2. Parcelas de dívidas ativas                  — via due_day mensal
  3. Faturas de cartão a vencer                  — via CreditCardInvoice.due_date
  4. Receita média histórica                     — média dos últimos 3 meses de INCOME

Saída: lista de eventos diários + séries agrupadas por semana e por mês.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.account import CreditCardInvoice, InvoiceStatus
from backend.models.debt import Debt
from backend.models.recurring import Periodicity, RecurringExpense
from backend.repositories.transaction_repository import TransactionRepository


# -----------------------------------------------------------------
# Tipos de evento
# -----------------------------------------------------------------

EVENT_INCOME    = "income"
EVENT_RECURRING = "recurring"
EVENT_DEBT      = "debt"
EVENT_INVOICE   = "invoice"


@dataclass
class CashflowEvent:
    event_date:   date
    description:  str
    amount:       Decimal
    event_type:   str          # income | recurring | debt | invoice
    category:     str = ""
    is_confirmed: bool = False  # True = já existe no banco; False = projetado


@dataclass
class PeriodSummary:
    label:           str
    start:           date
    end:             date
    income:          Decimal = Decimal("0")
    expenses:        Decimal = Decimal("0")
    balance:         Decimal = Decimal("0")
    running_balance: Decimal = Decimal("0")
    events:          list[CashflowEvent] = field(default_factory=list)


# -----------------------------------------------------------------
# Helpers de data
# -----------------------------------------------------------------

def _advance_date(d: date, periodicity: Periodicity) -> date:
    """Retorna a próxima ocorrência a partir de `d` para a periodicidade."""
    if periodicity == Periodicity.WEEKLY:
        return d + timedelta(weeks=1)
    if periodicity == Periodicity.BIWEEKLY:
        return d + timedelta(weeks=2)
    if periodicity == Periodicity.MONTHLY:
        return _add_months(d, 1)
    if periodicity == Periodicity.BIMONTHLY:
        return _add_months(d, 2)
    if periodicity == Periodicity.QUARTERLY:
        return _add_months(d, 3)
    if periodicity == Periodicity.SEMIANNUAL:
        return _add_months(d, 6)
    if periodicity == Periodicity.ANNUAL:
        return _add_months(d, 12)
    return _add_months(d, 1)


def _add_months(d: date, months: int) -> date:
    month = d.month + months
    year  = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"Sem {iso.week:02d}/{iso.year}"


def _month_label(d: date) -> str:
    months = ["Jan","Fev","Mar","Abr","Mai","Jun",
              "Jul","Ago","Set","Out","Nov","Dez"]
    return f"{months[d.month-1]}/{d.year}"


# -----------------------------------------------------------------
# Serviço principal
# -----------------------------------------------------------------

class CashflowService:

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tx_repo = TransactionRepository(session)

    async def project(
        self,
        months: int = 3,
        mode: str = "weekly",   # "weekly" | "monthly"
    ) -> dict:
        today     = date.today()
        end_date  = _add_months(today, months)
        events:   list[CashflowEvent] = []

        # 1. Receita média projetada ────────────────────────────────
        avg_income = await self._avg_monthly_income()
        if avg_income > 0:
            # Projeta uma entrada de receita no dia 5 de cada mês
            d = _month_start(_add_months(today, 1))
            d = date(d.year, d.month, min(5, calendar.monthrange(d.year, d.month)[1]))
            while d <= end_date:
                events.append(CashflowEvent(
                    event_date=d,
                    description="Receita estimada (média histórica)",
                    amount=avg_income,
                    event_type=EVENT_INCOME,
                    category="receita",
                ))
                d = _add_months(d, 1)

        # 2. Gastos recorrentes ────────────────────────────────────
        recurring = await self._get_active_recurring()
        for exp in recurring:
            d = exp.next_due_date
            # Se já passou, já está vencido — começa na próxima ocorrência
            while d < today:
                d = _advance_date(d, exp.periodicity)
            while d <= end_date:
                events.append(CashflowEvent(
                    event_date=d,
                    description=exp.name,
                    amount=-exp.amount,   # saída = negativo
                    event_type=EVENT_RECURRING,
                    category=exp.category,
                ))
                d = _advance_date(d, exp.periodicity)

        # 3. Parcelas de dívidas ───────────────────────────────────
        debts = await self._get_active_debts()
        for debt in debts:
            remaining = int(debt.total_installments) - int(debt.paid_installments)
            if remaining <= 0:
                continue
            # Próximo vencimento: dia due_day no mês seguinte (ou este)
            day = min(debt.due_day, 28)
            d   = date(today.year, today.month, day)
            if d < today:
                d = _add_months(d, 1)
            count = 0
            while d <= end_date and count < remaining:
                events.append(CashflowEvent(
                    event_date=d,
                    description=f"Parcela: {debt.name}",
                    amount=-debt.installment_amount,
                    event_type=EVENT_DEBT,
                    category="financiamento",
                ))
                d = _add_months(d, 1)
                count += 1

        # 4. Faturas de cartão ─────────────────────────────────────
        invoices = await self._get_upcoming_invoices(end_date)
        for inv in invoices:
            if inv.due_date >= today:
                events.append(CashflowEvent(
                    event_date=inv.due_date,
                    description=f"Fatura cartão",
                    amount=-inv.total_amount,
                    event_type=EVENT_INVOICE,
                    category="cartao_credito",
                    is_confirmed=True,
                ))

        events.sort(key=lambda e: e.event_date)

        # 5. Agrupa em períodos ────────────────────────────────────
        if mode == "weekly":
            periods = self._group_weekly(events, today, end_date)
        else:
            periods = self._group_monthly(events, today, end_date)

        # Saldo inicial = hoje (não projetado — usa saldo real se disponível)
        running = Decimal("0")
        for p in periods:
            running += p.balance
            p.running_balance = running.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return {
            "mode":          mode,
            "months":        months,
            "avg_income":    float(avg_income),
            "periods":       [self._period_to_dict(p) for p in periods],
            "events":        [self._event_to_dict(e) for e in events],
            "total_income":  float(sum(p.income  for p in periods)),
            "total_expenses": float(sum(p.expenses for p in periods)),
        }

    # -----------------------------------------------------------------
    # Agrupamento por semana
    # -----------------------------------------------------------------

    def _group_weekly(
        self, events: list[CashflowEvent], start: date, end: date
    ) -> list[PeriodSummary]:
        # Começa na segunda-feira da semana atual
        monday = start - timedelta(days=start.weekday())
        periods: list[PeriodSummary] = []
        d = monday
        while d <= end:
            week_end = d + timedelta(days=6)
            p = PeriodSummary(
                label=_week_label(d),
                start=d,
                end=min(week_end, end),
            )
            for ev in events:
                if d <= ev.event_date <= week_end:
                    p.events.append(ev)
                    if ev.amount > 0:
                        p.income   += ev.amount
                    else:
                        p.expenses += abs(ev.amount)
            p.balance = p.income - p.expenses
            periods.append(p)
            d += timedelta(weeks=1)
        return periods

    # -----------------------------------------------------------------
    # Agrupamento por mês
    # -----------------------------------------------------------------

    def _group_monthly(
        self, events: list[CashflowEvent], start: date, end: date
    ) -> list[PeriodSummary]:
        periods: list[PeriodSummary] = []
        d = _month_start(start)
        while d <= end:
            last = calendar.monthrange(d.year, d.month)[1]
            month_end = date(d.year, d.month, last)
            p = PeriodSummary(
                label=_month_label(d),
                start=d,
                end=min(month_end, end),
            )
            for ev in events:
                if d <= ev.event_date <= month_end:
                    p.events.append(ev)
                    if ev.amount > 0:
                        p.income   += ev.amount
                    else:
                        p.expenses += abs(ev.amount)
            p.balance = p.income - p.expenses
            periods.append(p)
            d = _add_months(d, 1)
        return periods

    # -----------------------------------------------------------------
    # Serialização
    # -----------------------------------------------------------------

    def _period_to_dict(self, p: PeriodSummary) -> dict:
        return {
            "label":           p.label,
            "start":           p.start.isoformat(),
            "end":             p.end.isoformat(),
            "income":          float(p.income),
            "expenses":        float(p.expenses),
            "balance":         float(p.balance),
            "running_balance": float(p.running_balance),
            "event_count":     len(p.events),
        }

    def _event_to_dict(self, e: CashflowEvent) -> dict:
        return {
            "date":         e.event_date.isoformat(),
            "description":  e.description,
            "amount":       float(e.amount),
            "type":         e.event_type,
            "category":     e.category,
            "is_confirmed": e.is_confirmed,
        }

    # -----------------------------------------------------------------
    # Queries ao banco
    # -----------------------------------------------------------------

    async def _avg_monthly_income(self) -> Decimal:
        today  = date.today()
        totals = []
        for delta in range(3):
            m = today.month - delta
            y = today.year
            if m <= 0:
                m += 12; y -= 1
            last = calendar.monthrange(y, m)[1]
            result = await self._tx_repo.sum_income_and_expense(
                date(y, m, 1), date(y, m, last)
            )
            totals.append(result["income"])
        avg = sum(totals) / 3
        return avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def _get_active_recurring(self) -> list[RecurringExpense]:
        stmt = (
            select(RecurringExpense)
            .where(RecurringExpense.is_active == True)
            .order_by(RecurringExpense.next_due_date)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _get_active_debts(self) -> list[Debt]:
        stmt = (
            select(Debt)
            .where(
                Debt.is_active == True,
                Debt.paid_installments < Debt.total_installments,
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Resumo de gastos recorrentes (novo endpoint GET /cashflow/recurring)
    # -----------------------------------------------------------------

    async def get_recurring_summary(self) -> dict:
        """
        Resumo consolidado de todos os gastos recorrentes ativos.

        Retorna:
          items         → lista de cada recorrente com próximo vencimento
          monthly_total → soma dos valores mensalizados (anual/12, semanal×4.33…)
          weekly_total  → soma dos valores semanalizados
          annual_total  → soma dos valores anualizados
          by_category   → total mensal agrupado por categoria
          count         → número de recorrentes ativos
        """
        from backend.models.recurring import Periodicity

        # Fatores de conversão para mensalização
        _MONTHLY_FACTOR: dict[Periodicity, float] = {
            Periodicity.WEEKLY:     4.333,
            Periodicity.BIWEEKLY:   2.167,
            Periodicity.MONTHLY:    1.0,
            Periodicity.BIMONTHLY:  0.5,
            Periodicity.QUARTERLY:  1 / 3,
            Periodicity.SEMIANNUAL: 1 / 6,
            Periodicity.ANNUAL:     1 / 12,
        }

        items = await self._get_active_recurring()
        today = date.today()

        result_items = []
        monthly_total = Decimal("0")
        by_category: dict[str, Decimal] = {}

        for exp in items:
            # Próximo vencimento efetivo
            d = exp.next_due_date
            while d < today:
                d = _advance_date(d, exp.periodicity)

            monthly_equiv = (
                Decimal(str(_MONTHLY_FACTOR.get(exp.periodicity, 1.0))) * exp.amount
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            monthly_total += monthly_equiv
            cat = str(exp.category)
            by_category[cat] = by_category.get(cat, Decimal("0")) + monthly_equiv

            result_items.append({
                "id":               exp.id,
                "name":             exp.name,
                "amount":           float(exp.amount),
                "category":         cat,
                "periodicity":      exp.periodicity,
                "next_due_date":    d.isoformat(),
                "days_until_due":   (d - today).days,
                "monthly_equiv":    float(monthly_equiv),
            })

        # Ordena por próximo vencimento
        result_items.sort(key=lambda x: x["next_due_date"])

        annual_total = (monthly_total * 12).quantize(Decimal("0.01"))
        weekly_total = (monthly_total / Decimal("4.333")).quantize(Decimal("0.01"))

        return {
            "count":         len(result_items),
            "monthly_total": float(monthly_total),
            "weekly_total":  float(weekly_total),
            "annual_total":  float(annual_total),
            "by_category":   {k: float(v) for k, v in by_category.items()},
            "items":         result_items,
        }

    async def _get_upcoming_invoices(self, end_date: date) -> list[CreditCardInvoice]:
        today = date.today()
        stmt  = (
            select(CreditCardInvoice)
            .where(
                CreditCardInvoice.due_date >= today,
                CreditCardInvoice.due_date <= end_date,
                CreditCardInvoice.status   == InvoiceStatus.OPEN,
            )
            .order_by(CreditCardInvoice.due_date)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
