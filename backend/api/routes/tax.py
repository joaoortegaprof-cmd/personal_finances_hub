"""
Endpoints de controle de IR sobre renda variável.

Rotas:
    GET /tax/monthly-summary/{year}   — resumo anual mês a mês
    GET /tax/darf/{year}/{month}      — dados para emissão de DARF
"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.tax import AnnualTaxSummaryOut, DarfOut, MonthlyTaxOut
from backend.core.database import get_db
from backend.services.tax_service import get_ir_summary, calculate_monthly_stock_sales

router = APIRouter(prefix="/tax", tags=["IR e DARF"])


@router.get("/monthly-summary/{year}", response_model=AnnualTaxSummaryOut)
async def get_annual_summary(year: int, db: AsyncSession = Depends(get_db)):
    """
    Retorna o resumo de IR mês a mês para o ano solicitado.

    Inclui vendas totais, lucro/prejuízo, IR devido e status de cada mês.
    """
    current_year = date.today().year
    if year < 2000 or year > current_year + 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ano inválido: {year}. Informe entre 2000 e {current_year + 1}.",
        )

    summary = await get_ir_summary(db, year)

    months = [MonthlyTaxOut(**m) for m in summary["months"]]
    return AnnualTaxSummaryOut(
        year=summary["year"],
        months=months,
        total_sales=summary["total_sales"],
        total_profit=summary["total_profit"],
        total_tax_due=summary["total_tax_due"],
        remaining_loss=summary["remaining_loss"],
    )


@router.get("/darf/{year}/{month}", response_model=DarfOut)
async def get_darf(year: int, month: int, db: AsyncSession = Depends(get_db)):
    """
    Retorna os dados necessários para emissão do DARF do mês.

    Código DARF 6015 — Ganhos Líquidos em Operações em Bolsa.
    Vencimento: último dia útil do mês seguinte ao apurado.
    """
    if month < 1 or month > 12:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mês deve estar entre 1 e 12.",
        )

    # Calcula o resumo do mês específico incluindo compensação acumulada
    summary = await get_ir_summary(db, year)
    month_data = next((m for m in summary["months"] if m["month"] == month), None)
    if month_data is None:
        raise HTTPException(status_code=500, detail="Erro ao calcular mês.")

    tax_due = month_data["tax_due"]
    is_exempt = month_data["is_exempt"]
    period = f"{month:02d}/{year}"

    if is_exempt:
        reason = f"Isento — vendas de R$ {month_data['total_sales']:.2f} abaixo do limite de R$ 20.000,00/mês."
        can_generate = False
    elif tax_due <= 0:
        reason = "Sem IR devido (prejuízo no mês compensou o lucro)."
        can_generate = False
    else:
        reason = (
            f"IR devido sobre lucro de R$ {month_data['total_profit']:.2f}. "
            f"Vencimento: último dia útil do mês seguinte."
        )
        can_generate = True

    return DarfOut(
        year=year,
        month=month,
        code="6015",
        period=period,
        tax_due=tax_due,
        can_generate=can_generate,
        reason=reason,
    )
