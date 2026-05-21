"""Schemas Pydantic para os endpoints de IR e DARF (/tax)."""

from decimal import Decimal

from pydantic import BaseModel


class MonthlyTaxOut(BaseModel):
    month: int                          # 1-12
    month_label: str                    # "Janeiro", "Fevereiro"…
    total_sales: Decimal                # total de vendas de ações no mês
    total_profit: Decimal               # lucro (pode ser negativo = prejuízo)
    tax_due: Decimal                    # IR devido (0 se isento ou prejuízo)
    is_exempt: bool                     # True se vendas <= R$20.000
    accumulated_loss: Decimal           # prejuízo acumulado antes deste mês
    status: str                         # "isento" | "pendente" | "pago"


class AnnualTaxSummaryOut(BaseModel):
    year: int
    months: list[MonthlyTaxOut]
    total_sales: Decimal
    total_profit: Decimal
    total_tax_due: Decimal
    remaining_loss: Decimal             # prejuízo acumulado a compensar no final do ano


class DarfOut(BaseModel):
    year: int
    month: int
    code: str = "6015"                  # Código DARF para renda variável
    period: str                         # "MM/YYYY"
    tax_due: Decimal
    can_generate: bool                  # False se isento ou zerado
    reason: str                         # Explicação do status
