"""
Serviço de geração de relatórios PDF do FinanceHub.

Usa a biblioteca `reportlab` para gerar PDFs profissionais com:
  - Cabeçalho com logotipo (texto) e data de geração
  - Tabelas com estilização alternada de linhas
  - Seções de resumo com cards coloridos
  - Gráfico de barras embutido (canvas do reportlab)
  - Rodapé com numeração de páginas

Três tipos de relatório:
  generate_monthly_report(data)   → resumo mensal de receitas/despesas
  generate_portfolio_report(data) → carteira de investimentos
  generate_annual_report(data)    → IR anual por mês com totais
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

# ReportLab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import KeepTogether

# ─────────────────────────────────────────────────────────────────
# Paleta de cores (em sintaxe reportlab)
# ─────────────────────────────────────────────────────────────────

_BLUE_DARK  = colors.HexColor("#1a3a6e")
_BLUE_MED   = colors.HexColor("#2e6abf")
_GREEN      = colors.HexColor("#0a7a4a")
_RED        = colors.HexColor("#b71c1c")
_GRAY_LIGHT = colors.HexColor("#f5f7fa")
_GRAY_MED   = colors.HexColor("#c5cae9")
_GRAY_TEXT  = colors.HexColor("#555555")
_WHITE      = colors.white
_BLACK      = colors.black

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm


# ─────────────────────────────────────────────────────────────────
# Estilos
# ─────────────────────────────────────────────────────────────────

_STYLES = getSampleStyleSheet()

_TITLE_STYLE = ParagraphStyle(
    "FHTitle",
    parent=_STYLES["Heading1"],
    fontSize=18,
    textColor=_BLUE_DARK,
    spaceAfter=4,
)
_SUBTITLE_STYLE = ParagraphStyle(
    "FHSubtitle",
    parent=_STYLES["Normal"],
    fontSize=10,
    textColor=_GRAY_TEXT,
    spaceAfter=12,
)
_SECTION_STYLE = ParagraphStyle(
    "FHSection",
    parent=_STYLES["Heading2"],
    fontSize=13,
    textColor=_BLUE_DARK,
    spaceBefore=14,
    spaceAfter=6,
)
_BODY_STYLE = ParagraphStyle(
    "FHBody",
    parent=_STYLES["Normal"],
    fontSize=9,
    textColor=_BLACK,
    leading=13,
)
_SMALL_STYLE = ParagraphStyle(
    "FHSmall",
    parent=_STYLES["Normal"],
    fontSize=8,
    textColor=_GRAY_TEXT,
    leading=11,
)
_CELL_RIGHT = ParagraphStyle("FHCellRight", parent=_BODY_STYLE, alignment=TA_RIGHT)
_CELL_CENTER = ParagraphStyle("FHCellCenter", parent=_BODY_STYLE, alignment=TA_CENTER)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _fmt(value: float | Decimal, prefix: str = "R$ ") -> str:
    """Formata um valor monetário no padrão brasileiro (ponto milhar, vírgula decimal)."""
    v = float(value)
    formatted = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "-" if v < 0 else ""
    return f"{sign}{prefix}{formatted}"


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def _header_footer(canvas, doc):
    """Callback chamado pelo SimpleDocTemplate antes de cada página."""
    canvas.saveState()
    w = PAGE_W - 2 * MARGIN

    # ── Cabeçalho ────────────────────────────────────────────────
    canvas.setFillColor(_BLUE_DARK)
    canvas.rect(MARGIN, PAGE_H - 14 * mm, w, 10 * mm, fill=1, stroke=0)

    canvas.setFillColor(_WHITE)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(MARGIN + 4 * mm, PAGE_H - 9.5 * mm, "💰 FinanceHub")

    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(
        PAGE_W - MARGIN - 2 * mm,
        PAGE_H - 9.5 * mm,
        f"Gerado em {date.today().strftime('%d/%m/%Y')}",
    )

    # ── Rodapé ───────────────────────────────────────────────────
    canvas.setFillColor(_GRAY_TEXT)
    canvas.setFont("Helvetica", 7)
    canvas.drawCentredString(
        PAGE_W / 2,
        8 * mm,
        f"FinanceHub — Relatório confidencial   •   Página {doc.page}",
    )
    canvas.setStrokeColor(_GRAY_MED)
    canvas.line(MARGIN, 12 * mm, PAGE_W - MARGIN, 12 * mm)

    canvas.restoreState()


def _summary_table(items: list[tuple[str, str, colors.Color | None]]) -> Table:
    """
    Cria uma tabela horizontal de cards de resumo.
    items: list of (label, value, value_color)
    """
    col_w = (PAGE_W - 2 * MARGIN) / len(items)
    header_row = [
        Paragraph(label, ParagraphStyle("SLabel", parent=_SMALL_STYLE, alignment=TA_CENTER))
        for label, _, _ in items
    ]
    value_row = [
        Paragraph(
            value,
            ParagraphStyle(
                "SValue",
                parent=ParagraphStyle(
                    "SValueBase",
                    parent=_BODY_STYLE,
                    fontSize=14,
                    textColor=color or _BLUE_DARK,
                    alignment=TA_CENTER,
                    fontName="Helvetica-Bold",
                ),
            ),
        )
        for _, value, color in items
    ]

    table = Table(
        [header_row, value_row],
        colWidths=[col_w] * len(items),
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _GRAY_LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.5, _GRAY_MED),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [_GRAY_LIGHT, _WHITE]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("ROUNDEDCORNERS", [4]),
    ]))
    return table


def _data_table(
    headers: list[str],
    rows: list[list[str]],
    col_widths: list[float] | None = None,
) -> Table:
    """Cria uma tabela de dados com cabeçalho azul e linhas alternadas."""
    all_rows = [headers] + rows
    total_w = PAGE_W - 2 * MARGIN

    if col_widths is None:
        col_widths = [total_w / len(headers)] * len(headers)

    table = Table(all_rows, colWidths=col_widths, repeatRows=1)
    n = len(rows)
    style = [
        # Cabeçalho
        ("BACKGROUND", (0, 0), (-1, 0), _BLUE_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), _WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        # Dados
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, _GRAY_MED),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    # Linhas alternadas
    for i in range(1, n + 1, 2):
        style.append(("BACKGROUND", (0, i), (-1, i), _GRAY_LIGHT))

    table.setStyle(TableStyle(style))
    return table


# ─────────────────────────────────────────────────────────────────
# Relatório Mensal
# ─────────────────────────────────────────────────────────────────

def generate_monthly_report(
    data: dict[str, Any],
    output_path: str | Path,
) -> Path:
    """
    Gera relatório mensal de receitas e despesas em PDF.

    data (esperado da API /transactions + /accounts):
      transactions: list[dict]  → lançamentos do período
      accounts:     list[dict]  → contas para lookup de nome
      start_date:   str (ISO)
      end_date:     str (ISO)
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    transactions = data.get("transactions", [])
    accounts = {a["id"]: a["name"] for a in data.get("accounts", [])}
    start_str = data.get("start_date", "")
    end_str = data.get("end_date", "")

    # Calcula totais
    income  = sum(float(t["amount"]) for t in transactions if t.get("transaction_type") == "income")
    expense = sum(float(t["amount"]) for t in transactions if t.get("transaction_type") in ("debit", "credit", "investment"))
    balance = income - expense
    rate    = (balance / income * 100) if income > 0 else 0.0

    # Por categoria
    cats: dict[str, float] = {}
    for t in transactions:
        if t.get("transaction_type") in ("debit", "credit"):
            cat = t.get("category", "outros")
            cats[cat] = cats.get(cat, 0) + float(t.get("amount", 0))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=20 * mm, bottomMargin=18 * mm,
    )

    def _fmt_date(iso: str) -> str:
        try:
            return date.fromisoformat(iso).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            return iso

    story = [
        Spacer(1, 6 * mm),
        Paragraph("Relatório Financeiro Mensal", _TITLE_STYLE),
        Paragraph(f"Período: {_fmt_date(start_str)} a {_fmt_date(end_str)}", _SUBTITLE_STYLE),
        HRFlowable(width="100%", thickness=1, color=_BLUE_DARK, spaceAfter=8),

        # Cards de resumo
        _summary_table([
            ("Receitas", _fmt(income), _GREEN),
            ("Despesas", _fmt(expense), _RED),
            ("Saldo",    _fmt(balance), _GREEN if balance >= 0 else _RED),
            ("Taxa de Poupança", _pct(rate), _BLUE_MED),
        ]),
        Spacer(1, 8 * mm),

        # Tabela de despesas por categoria
        Paragraph("Despesas por Categoria", _SECTION_STYLE),
    ]

    if cats:
        cat_rows = [
            [cat.replace("_", " ").title(), _fmt(val)]
            for cat, val in sorted(cats.items(), key=lambda x: -x[1])
        ]
        story.append(_data_table(
            ["Categoria", "Total"],
            cat_rows,
            col_widths=[(PAGE_W - 2 * MARGIN) * 0.65, (PAGE_W - 2 * MARGIN) * 0.35],
        ))
    else:
        story.append(Paragraph("Nenhuma despesa no período.", _BODY_STYLE))

    story.append(Spacer(1, 8 * mm))

    # Tabela de lançamentos
    story.append(Paragraph("Detalhamento de Lançamentos", _SECTION_STYLE))

    if transactions:
        tx_rows = []
        for t in sorted(transactions, key=lambda x: x.get("transaction_date", "")):
            tx_type = t.get("transaction_type", "")
            amount  = float(t.get("amount", 0))
            sign    = "+" if tx_type == "income" else "-"
            acc     = accounts.get(t.get("account_id"), "—")
            tx_rows.append([
                t.get("transaction_date", "")[:10],
                t.get("description", "")[:40],
                acc[:20],
                f"{sign}{_fmt(amount)}",
            ])

        w = PAGE_W - 2 * MARGIN
        story.append(_data_table(
            ["Data", "Descrição", "Conta", "Valor"],
            tx_rows,
            col_widths=[w * 0.12, w * 0.40, w * 0.25, w * 0.23],
        ))
    else:
        story.append(Paragraph("Nenhum lançamento no período.", _BODY_STYLE))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return output_path


# ─────────────────────────────────────────────────────────────────
# Relatório de Carteira
# ─────────────────────────────────────────────────────────────────

def generate_portfolio_report(
    data: dict[str, Any],
    output_path: str | Path,
) -> Path:
    """
    Gera relatório da carteira de investimentos em PDF.

    data (esperado da API /assets + /portfolio):
      assets:    list[dict]
      portfolio: dict com total_invested, by_type, etc.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    assets    = data.get("assets", [])
    portfolio = data.get("portfolio", {})
    total     = float(portfolio.get("total_invested", 0))

    by_type: dict[str, float] = {}
    for a in assets:
        t   = a.get("asset_type", "outros")
        val = float(a.get("invested_value", 0))
        by_type[t] = by_type.get(t, 0) + val

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=20 * mm, bottomMargin=18 * mm,
    )

    story = [
        Spacer(1, 6 * mm),
        Paragraph("Relatório de Carteira de Investimentos", _TITLE_STYLE),
        Paragraph(f"Gerado em {date.today().strftime('%d/%m/%Y')}", _SUBTITLE_STYLE),
        HRFlowable(width="100%", thickness=1, color=_BLUE_DARK, spaceAfter=8),

        _summary_table([
            ("Ativos Cadastrados", str(len(assets)), _BLUE_DARK),
            ("Total Investido",    _fmt(total),        _BLUE_MED),
        ]),
        Spacer(1, 8 * mm),
    ]

    # Por tipo de ativo
    if by_type:
        story.append(Paragraph("Composição por Tipo de Ativo", _SECTION_STYLE))
        type_rows = []
        for t, val in sorted(by_type.items(), key=lambda x: -x[1]):
            pct_v = (val / total * 100) if total > 0 else 0
            type_rows.append([t.replace("_", " ").title(), _fmt(val), _pct(pct_v)])
        story.append(_data_table(
            ["Tipo", "Total Investido", "% da Carteira"],
            type_rows,
        ))
        story.append(Spacer(1, 8 * mm))

    # Lista de ativos
    story.append(Paragraph("Ativos na Carteira", _SECTION_STYLE))
    if assets:
        asset_rows = [
            [
                a.get("ticker", "—"),
                a.get("name", "")[:35],
                a.get("asset_type", "").replace("_", " ").title(),
                a.get("liquidity", ""),
            ]
            for a in assets
        ]
        w = PAGE_W - 2 * MARGIN
        story.append(_data_table(
            ["Ticker", "Nome", "Tipo", "Liquidez"],
            asset_rows,
            col_widths=[w * 0.12, w * 0.43, w * 0.28, w * 0.17],
        ))
    else:
        story.append(Paragraph("Nenhum ativo cadastrado.", _BODY_STYLE))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return output_path


# ─────────────────────────────────────────────────────────────────
# Relatório Anual de IR
# ─────────────────────────────────────────────────────────────────

def generate_annual_report(
    data: dict[str, Any],
    output_path: str | Path,
    year: int | None = None,
) -> Path:
    """
    Gera relatório anual de IR sobre renda variável em PDF.

    data (esperado da API /tax/ir?year=YYYY):
      year:       int
      months:     list[dict] com month, month_label, total_sales,
                  total_profit, tax_due, is_exempt, status
      total_sales, total_profit, total_tax_due
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report_year = year or data.get("year", date.today().year)
    months      = data.get("months", [])
    total_sales = float(data.get("total_sales", 0))
    total_profit= float(data.get("total_profit", 0))
    total_tax   = float(data.get("total_tax_due", 0))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=20 * mm, bottomMargin=18 * mm,
    )

    story = [
        Spacer(1, 6 * mm),
        Paragraph(f"Relatório Anual de IR — {report_year}", _TITLE_STYLE),
        Paragraph("Renda variável — isenção até R$20.000/mês em ações", _SUBTITLE_STYLE),
        HRFlowable(width="100%", thickness=1, color=_BLUE_DARK, spaceAfter=8),

        _summary_table([
            ("Vendas no Ano",   _fmt(total_sales),  _BLUE_DARK),
            ("Lucro / Prejuízo",_fmt(total_profit),  _GREEN if total_profit >= 0 else _RED),
            ("IR Total Devido", _fmt(total_tax),     _RED if total_tax > 0 else _GREEN),
        ]),
        Spacer(1, 8 * mm),
        Paragraph("Apuração Mensal", _SECTION_STYLE),
    ]

    if months:
        ir_rows = []
        for m in months:
            status_icon = "✓ isento" if m.get("is_exempt") else (
                "✓ isento" if float(m.get("tax_due", 0)) == 0 else "⚠ pendente"
            )
            ir_rows.append([
                m.get("month_label", ""),
                _fmt(float(m.get("total_sales", 0))),
                _fmt(float(m.get("total_profit", 0))),
                _fmt(float(m.get("tax_due", 0))),
                status_icon,
            ])

        w = PAGE_W - 2 * MARGIN
        story.append(_data_table(
            ["Mês", "Vendas", "Lucro/Prej.", "IR Devido", "Status"],
            ir_rows,
            col_widths=[w * 0.15, w * 0.22, w * 0.22, w * 0.22, w * 0.19],
        ))
    else:
        story.append(Paragraph("Sem operações de venda no ano.", _BODY_STYLE))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "* IR calculado à alíquota de 15% (swing trade). Compensação de perdas aplicada mês a mês.",
        _SMALL_STYLE,
    ))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return output_path
