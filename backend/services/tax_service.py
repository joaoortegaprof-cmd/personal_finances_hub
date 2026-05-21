"""
Serviço de cálculo de IR sobre renda variável.

Regras aplicadas:
  - Isenção: vendas de AÇÕES <= R$20.000 no mês (FIIs NÃO têm isenção)
  - Alíquota: 15% para swing trade, 20% para day trade (usamos 15% como padrão)
  - Compensação de perdas: prejuízo de meses anteriores abate o lucro futuro
  - Base de cálculo: (preço venda - preço médio de aquisição) × quantidade vendida

Limitação atual:
  As operações são registradas como AssetPosition com operation_type=SELL.
  O preço médio é calculado acumulando todas as compras anteriores.
  Day trade requer granularidade intraday não disponível neste modelo — usamos 15%.
"""

from calendar import month_name
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.asset import Asset, AssetPosition, AssetType, OperationType


_STOCK_TYPES = {AssetType.STOCK, AssetType.ETF, AssetType.INTL_STOCK}
_EXEMPTION_LIMIT = Decimal("20000.00")
_ALIQUOTA = Decimal("0.15")  # 15% swing trade padrão


async def calculate_monthly_stock_sales(
    db: AsyncSession, year: int, month: int
) -> dict:
    """
    Calcula vendas de ações (não FIIs) em um mês específico.

    Retorna:
      total_sales: soma dos valores brutos de venda
      total_profit: lucro/prejuízo líquido (sem considerar compensações)
      is_exempt: True se total_sales <= R$20.000
      tax_before_compensation: IR bruto antes de compensar prejuízos anteriores
    """
    from datetime import date

    month_start = date(year, month, 1)
    # Último dia do mês
    if month == 12:
        month_end = date(year + 1, 1, 1)
    else:
        month_end = date(year, month + 1, 1)
    import datetime as dt
    month_end = month_end - dt.timedelta(days=1)

    # Busca todas as vendas de ações no mês (inclui ETF, exclui FII)
    stmt = (
        select(AssetPosition)
        .join(Asset, AssetPosition.asset_id == Asset.id)
        .where(AssetPosition.operation_type == OperationType.SELL)
        .where(AssetPosition.operation_date >= month_start)
        .where(AssetPosition.operation_date <= month_end)
        .where(Asset.asset_type.in_([t.value for t in _STOCK_TYPES]))
        .options(selectinload(AssetPosition.asset))
    )
    result = await db.execute(stmt)
    sells = result.scalars().all()

    total_sales = Decimal("0")
    total_profit = Decimal("0")

    for sell in sells:
        qty = abs(sell.quantity)
        sale_price = sell.unit_price
        sale_value = qty * sale_price - sell.fees

        # Calcula preço médio histórico das compras até esta venda
        avg = await _get_avg_price_at_date(db, sell.asset_id, sell.operation_date)

        cost = qty * avg
        profit = sale_value - cost

        total_sales += sale_value
        total_profit += profit

    is_exempt = total_sales <= _EXEMPTION_LIMIT
    tax_before_compensation = Decimal("0")
    if not is_exempt and total_profit > 0:
        tax_before_compensation = (total_profit * _ALIQUOTA).quantize(Decimal("0.01"))

    return {
        "total_sales": total_sales.quantize(Decimal("0.01")),
        "total_profit": total_profit.quantize(Decimal("0.01")),
        "is_exempt": is_exempt,
        "tax_before_compensation": tax_before_compensation,
    }


async def _get_avg_price_at_date(
    db: AsyncSession, asset_id: int, reference_date
) -> Decimal:
    """
    Calcula o preço médio de aquisição acumulado até reference_date (exclusive).

    Percorre todas as compras anteriores à data de referência e calcula
    a média ponderada pelo método FIFO/custo médio.
    """
    stmt = (
        select(AssetPosition)
        .where(AssetPosition.asset_id == asset_id)
        .where(AssetPosition.operation_date < reference_date)
        .where(
            AssetPosition.operation_type.in_(
                [OperationType.BUY.value, OperationType.SELL.value]
            )
        )
        .order_by(AssetPosition.operation_date, AssetPosition.id)
    )
    result = await db.execute(stmt)
    ops = result.scalars().all()

    total_qty = Decimal("0")
    total_cost = Decimal("0")
    avg = Decimal("0")

    for op in ops:
        if op.operation_type == OperationType.BUY:
            total_cost += op.quantity * op.unit_price + op.fees
            total_qty += op.quantity
            if total_qty > 0:
                avg = total_cost / total_qty
        elif op.operation_type == OperationType.SELL:
            qty_sold = abs(op.quantity)
            total_cost -= qty_sold * avg
            total_qty -= qty_sold
            if total_qty <= 0:
                total_qty = Decimal("0")
                total_cost = Decimal("0")
                avg = Decimal("0")

    return avg.quantize(Decimal("0.000001"))


async def get_ir_summary(db: AsyncSession, year: int) -> dict:
    """
    Resumo mensal de IR para o ano inteiro.

    Para cada mês calcula vendas, lucro, IR devido e aplica compensação
    de perdas acumuladas de meses anteriores.
    """
    _PT_MONTHS = [
        "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    ]

    months_data = []
    accumulated_loss = Decimal("0")  # prejuízo acumulado a compensar

    total_sales_year = Decimal("0")
    total_profit_year = Decimal("0")
    total_tax_year = Decimal("0")

    for month in range(1, 13):
        data = await calculate_monthly_stock_sales(db, year, month)

        total_sales = data["total_sales"]
        raw_profit = data["total_profit"]
        is_exempt = data["is_exempt"]
        tax_before = data["tax_before_compensation"]

        # Aplica compensação de perdas
        taxable_profit = raw_profit - accumulated_loss
        if taxable_profit < 0:
            accumulated_loss = abs(taxable_profit)
            taxable_profit = Decimal("0")
        else:
            accumulated_loss = Decimal("0")

        # Recalcula IR após compensação
        if is_exempt or taxable_profit <= 0:
            tax_due = Decimal("0")
        else:
            tax_due = (taxable_profit * _ALIQUOTA).quantize(Decimal("0.01"))

        # Status: isento, pendente ou pago (pago é gerenciado no frontend)
        if is_exempt:
            status = "isento"
        elif tax_due <= 0:
            status = "isento"
        else:
            status = "pendente"

        total_sales_year += total_sales
        total_profit_year += raw_profit
        total_tax_year += tax_due

        months_data.append({
            "month": month,
            "month_label": _PT_MONTHS[month],
            "total_sales": total_sales,
            "total_profit": raw_profit,
            "tax_due": tax_due,
            "is_exempt": is_exempt,
            "accumulated_loss": accumulated_loss,
            "status": status,
        })

    return {
        "year": year,
        "months": months_data,
        "total_sales": total_sales_year.quantize(Decimal("0.01")),
        "total_profit": total_profit_year.quantize(Decimal("0.01")),
        "total_tax_due": total_tax_year.quantize(Decimal("0.01")),
        "remaining_loss": accumulated_loss,
    }
