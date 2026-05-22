"""
Serviço de simulação financeira do FinanceHub.

Fornece projeções para:
  - Aposentadoria / independência financeira
  - Objetivos financeiros com prazo
  - Quitação antecipada de dívidas
  - Cálculo do número FIRE (Financial Independence, Retire Early)

Toda a matemática é feita mês a mês (sem atalhos de fórmula fechada) para
que o chamador receba a série temporal completa e possa plotá-la.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


# -----------------------------------------------------------------
# Auxiliares
# -----------------------------------------------------------------

def _monthly_rate(annual_rate: Decimal) -> Decimal:
    """Converte taxa anual percentual para fator mensal: (1 + r/100)^(1/12) - 1."""
    return (1 + annual_rate / 100) ** (Decimal("1") / 12) - 1


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# -----------------------------------------------------------------
# 1. Simulação de aposentadoria
# -----------------------------------------------------------------

def simulate_retirement(
    monthly_contribution: Decimal,
    current_portfolio: Decimal,
    target_amount: Decimal,
    annual_return_rate: Decimal,
    inflation_rate: Decimal,
    months: int,
) -> dict:
    """
    Projeta o crescimento patrimonial mês a mês até `months` ou até atingir
    `target_amount`, o que vier primeiro.

    Retorna:
      months_to_target        – None se não atingir no prazo
      projected_values        – lista de Decimal, comprimento = months
      inflation_adjusted_values – valores em poder de compra de hoje
      total_contributed       – soma dos aportes realizados
      total_return            – rendimentos acumulados
      success                 – True se target_amount foi atingido dentro do prazo
    """
    monthly_nominal = _monthly_rate(annual_return_rate)
    monthly_inflation = _monthly_rate(inflation_rate)

    portfolio = current_portfolio
    total_contributed = Decimal("0")
    projected_values: list[Decimal] = []
    inflation_adjusted_values: list[Decimal] = []
    months_to_target: Optional[int] = None

    for m in range(1, months + 1):
        portfolio = portfolio * (1 + monthly_nominal) + monthly_contribution
        total_contributed += monthly_contribution

        projected_values.append(_round2(portfolio))

        # Deflaciona pelo acúmulo de inflação desde o mês 0
        deflation_factor = (1 + monthly_inflation) ** m
        inflation_adjusted_values.append(_round2(portfolio / deflation_factor))

        if months_to_target is None and portfolio >= target_amount:
            months_to_target = m

    total_return = portfolio - current_portfolio - total_contributed

    return {
        "months_to_target": months_to_target,
        "projected_values": [float(v) for v in projected_values],
        "inflation_adjusted_values": [float(v) for v in inflation_adjusted_values],
        "total_contributed": float(_round2(total_contributed)),
        "total_return": float(_round2(total_return)),
        "final_portfolio": float(_round2(portfolio)),
        "success": months_to_target is not None,
    }


# -----------------------------------------------------------------
# 2. Simulação de objetivo financeiro
# -----------------------------------------------------------------

def simulate_goal(
    goal_name: str,
    goal_amount: Decimal,
    monthly_contribution: Decimal,
    current_savings: Decimal,
    annual_return_rate: Decimal,
    deadline_months: int,
) -> dict:
    """
    Projeta a evolução de uma poupança dedicada a um objetivo (viagem,
    imóvel, educação, etc.) e calcula o aporte necessário para atingi-lo
    no prazo.

    Retorna:
      goal_name
      projected_values      – lista mês a mês
      months_to_goal        – meses reais para atingir (None se não atingir no prazo)
      viable                – True se o aporte atual é suficiente para o prazo
      required_contribution – aporte mensal necessário para bater o prazo
      shortfall             – quanto vai faltar ao fim do prazo (0 se viável)
    """
    monthly_nominal = _monthly_rate(annual_return_rate)
    savings = current_savings
    projected_values: list[Decimal] = []
    months_to_goal: Optional[int] = None

    for m in range(1, deadline_months + 1):
        savings = savings * (1 + monthly_nominal) + monthly_contribution
        projected_values.append(_round2(savings))
        if months_to_goal is None and savings >= goal_amount:
            months_to_goal = m

    # Calcular aporte necessário via fórmula de valor futuro da anuidade:
    # FV = PV*(1+r)^n + PMT * [(1+r)^n - 1] / r
    # PMT = (FV - PV*(1+r)^n) * r / [(1+r)^n - 1]
    r = monthly_nominal
    n = deadline_months
    fv_pv = current_savings * (1 + r) ** n
    if r == 0:
        required_contribution = (goal_amount - fv_pv) / n
    else:
        required_contribution = (goal_amount - fv_pv) * r / ((1 + r) ** n - 1)

    required_contribution = max(Decimal("0"), _round2(required_contribution))
    shortfall = max(Decimal("0"), _round2(goal_amount - savings))

    return {
        "goal_name": goal_name,
        "projected_values": [float(v) for v in projected_values],
        "months_to_goal": months_to_goal,
        "viable": months_to_goal is not None,
        "required_contribution": float(required_contribution),
        "shortfall": float(shortfall),
        "final_savings": float(_round2(savings)),
    }


# -----------------------------------------------------------------
# 3. Simulação de quitação de dívida
# -----------------------------------------------------------------

def simulate_debt_payoff(
    debt_amount: Decimal,
    monthly_rate: Decimal,
    monthly_payment: Decimal,
    extra_payment: Decimal = Decimal("0"),
) -> dict:
    """
    Compara dois cenários de amortização:
      A) pagamento padrão
      B) pagamento padrão + aporte extra mensal

    Retorna:
      standard   – {months, total_paid, total_interest, schedule}
      accelerated – {months, total_paid, total_interest, schedule}
      months_saved   – diferença de prazo
      interest_saved – economia de juros
    """
    def _amortize(balance: Decimal, rate: Decimal, payment: Decimal) -> dict:
        total_paid = Decimal("0")
        total_interest = Decimal("0")
        schedule: list[dict] = []
        month = 0

        while balance > Decimal("0.01") and month < 1200:
            month += 1
            interest = _round2(balance * rate / 100)
            principal = min(_round2(payment - interest), balance)

            if principal <= 0:
                # Pagamento menor que os juros — dívida nunca quitada
                break

            balance = _round2(balance - principal)
            total_interest += interest
            total_paid += interest + principal
            schedule.append({
                "month": month,
                "balance": float(_round2(balance)),
                "interest": float(interest),
                "principal": float(principal),
            })

        return {
            "months": month,
            "total_paid": float(_round2(total_paid)),
            "total_interest": float(_round2(total_interest)),
            "schedule": schedule,
        }

    standard = _amortize(debt_amount, monthly_rate, monthly_payment)
    accelerated = _amortize(debt_amount, monthly_rate, monthly_payment + extra_payment)

    months_saved = standard["months"] - accelerated["months"]
    interest_saved = _round2(
        Decimal(str(standard["total_interest"])) -
        Decimal(str(accelerated["total_interest"]))
    )

    return {
        "standard": standard,
        "accelerated": accelerated,
        "months_saved": months_saved,
        "interest_saved": float(interest_saved),
    }


# -----------------------------------------------------------------
# 4. Número FIRE
# -----------------------------------------------------------------

def calculate_fire_number(
    monthly_expenses: Decimal,
    withdrawal_rate: Decimal = Decimal("0.04"),
) -> dict:
    """
    Calcula o patrimônio necessário para independência financeira (FIRE).

    Regra base: patrimônio = despesas_anuais / taxa_de_retirada
    Retorna variações para diferentes taxas de retirada (3%, 3.5%, 4%, 4.5%, 5%).
    """
    annual_expenses = monthly_expenses * 12
    fire_number = _round2(annual_expenses / withdrawal_rate)

    variations = []
    for rate in [Decimal("0.03"), Decimal("0.035"), Decimal("0.04"), Decimal("0.045"), Decimal("0.05")]:
        variations.append({
            "withdrawal_rate_pct": float(rate * 100),
            "fire_number": float(_round2(annual_expenses / rate)),
        })

    return {
        "monthly_expenses": float(monthly_expenses),
        "annual_expenses": float(_round2(annual_expenses)),
        "fire_number": float(fire_number),
        "withdrawal_rate_pct": float(withdrawal_rate * 100),
        "variations": variations,
    }
