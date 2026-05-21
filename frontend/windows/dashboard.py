"""
Página do Dashboard — visão geral do patrimônio e alertas ativos.

Layout:
  ┌──────────────────────────────────────────────────────────────────┐
  │  [Patrim D+0]  [Patrim Total]  [Reserva Emerg]  [Score Saúde]  │  ← Linha 1 (4 cards)
  ├──────────────────────────────────────────────────────────────────┤
  │  [Receitas]    [Despesas]      [Saldo do Mês]                   │  ← Linha 2 (3 cards)
  ├──────────────────────────────────────────────────────────────────┤
  │  Dívidas e Financiamentos                                        │
  │  ████████░░░░ Financiamento Carro — Bradesco  1.5%/m            │
  ├──────────────────────────────────────────────────────────────────┤
  │  ████████████ Gráfico de barras mensais ████████████            │
  ├──────────────────────────┬─────────────────────────────────────-┤
  │  Linha 10 anos (45%)     │  Donut + legenda top-5               │
  ├──────────────────────────────────────────────────────────────────┤
  │  Distribuição por Categoria                                      │
  │  [Donut Ações + lista] [Donut FIIs + lista]                     │
  ├──────────────────────────────────────────────────────────────────┤
  │  Alertas Ativos                                                  │
  └──────────────────────────────────────────────────────────────────┘

Threading:
  DashboardWorker        → dashboard + alerts + emergency_fund + debts
  PatrimonyHistoryWorker → transactions + portfolio + accounts + asset positions

Gráficos: matplotlib com backend Qt6Agg (sem OpenGL/QWebEngineView).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import matplotlib
matplotlib.use("qtagg")

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QPushButton,
)

from frontend.components.api_client import ApiClient, ApiError


# ======================================================================
# Constantes visuais
# ======================================================================

_BG    = "#1A1D2E"
_TEXT  = "#C8CAD8"
_GREEN = "#00C896"
_RED   = "#FF6B6B"
_BLUE  = "#4A9EFF"
_GRID  = "#2A2D3E"

_BG_RGB    = (26/255, 29/255, 46/255)
_TEXT_RGB  = (200/255, 202/255, 216/255)
_GREEN_RGB = (0/255, 200/255, 150/255)
_RED_RGB   = (255/255, 107/255, 107/255)
_BLUE_RGB  = (74/255, 158/255, 255/255)
_GRID_RGB  = (42/255, 45/255, 62/255)

_CATEGORIES = ["Ações", "FIIs", "ETFs", "Tesouro", "Renda Fixa", "Contas", "Cripto", "Outros"]
_CAT_COLORS = ["#4A9EFF", "#00C896", "#FFB347", "#A78BFA", "#F59E0B", "#6EE7B7", "#F97316", "#94A3B8"]
_COLOR_MAP  = dict(zip(_CATEGORIES, _CAT_COLORS))

_TYPE_TO_CAT: dict[str, str] = {
    "acao":               "Ações",
    "fii":                "FIIs",
    "etf":                "ETFs",
    "tesouro_direto":     "Tesouro",
    "renda_fixa":         "Renda Fixa",
    "criptomoeda":        "Cripto",
    "acao_internacional": "Ações",
    "previdencia":        "Outros",
    "outros":             "Outros",
}

# Paleta de cores para ativos individuais dentro de cada donut de categoria
_ASSET_SLOT_COLORS = [
    "#4A9EFF", "#00C896", "#FFB347", "#A78BFA", "#F59E0B",
    "#6EE7B7", "#F97316", "#EC4899", "#34D399", "#FBBF24",
]

_MONTH_ABBR = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
               "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


# ======================================================================
# Workers — execução HTTP em background
# ======================================================================

class DashboardWorker(QThread):
    """
    Busca dashboard + alertas + reserva de emergência + dívidas.
    Emite todos de uma vez para evitar múltiplas atualizações parciais.
    """

    data_ready     = pyqtSignal(dict, dict, dict, list)  # dashboard, alerts, ef, debts
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            dashboard = self._client.get_dashboard()
            alerts    = self._client.get_alerts()

            try:
                emergency_fund = self._client.get_emergency_fund()
            except ApiError:
                emergency_fund = {"saldo_total": 0, "media_gastos_6m": 0, "meses_cobertos": 0}

            try:
                debts = self._client.get_debts()
            except ApiError:
                debts = []

            self.data_ready.emit(dashboard, alerts, emergency_fund, debts)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class PatrimonyHistoryWorker(QThread):
    """
    Busca e processa dados para gráficos e donuts de categoria.
    """

    patrimony_ready = pyqtSignal(dict)
    error_occurred  = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            today         = date.today()
            ten_years_ago = date(today.year - 10, today.month, today.day)

            dashboard  = self._client.get_dashboard()
            current_nw = float(dashboard.get("net_worth", {}).get("net_worth", 0))

            try:
                transactions = self._client.get_transactions(
                    start_date=ten_years_ago, end_date=today
                )
            except ApiError:
                transactions = []

            try:
                portfolio = self._client.get_portfolio_summary()
            except ApiError:
                portfolio = {"by_type": [], "total_invested": 0}

            try:
                accounts = self._client.get_accounts()
            except ApiError:
                accounts = []

            # Posições individuais para donuts por categoria
            try:
                assets = self._client.get_assets()
                asset_positions: list[dict] = []
                for asset in assets:
                    try:
                        pos = self._client.get_asset_position(asset["id"])
                        asset_positions.append(pos)
                    except ApiError:
                        pass
            except ApiError:
                asset_positions = []

            self.patrimony_ready.emit({
                "monthly_bars":    _build_monthly_series(current_nw, transactions, 12),
                "yearly_line":     _build_yearly_series(current_nw, transactions, 10),
                "distribution":    _build_distribution(portfolio, accounts),
                "category_donuts": _build_category_donuts(asset_positions),
            })

        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro ao carregar gráficos: {exc}")


# ======================================================================
# Processamento de dados (funções puras)
# ======================================================================

def _build_monthly_series(
    current_nw: float, transactions: list[dict], months_back: int
) -> list[dict]:
    flows: dict[tuple[int, int], float] = defaultdict(float)
    for tx in transactions:
        try:
            d = date.fromisoformat(str(tx["transaction_date"]))
        except (KeyError, ValueError):
            continue
        amount = float(tx.get("amount", 0))
        if tx.get("transaction_type") == "receita":
            flows[(d.year, d.month)] += amount
        else:
            flows[(d.year, d.month)] -= amount

    today  = date.today()
    months: list[tuple[int, int]] = []
    for i in range(months_back - 1, -1, -1):
        m, y = today.month - i, today.year
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))

    nw: dict[tuple[int, int], float] = {months[-1]: current_nw}
    for i in range(len(months) - 1, 0, -1):
        curr, prev = months[i], months[i - 1]
        nw[prev] = nw[curr] - flows.get(curr, 0.0)

    return [
        {"label": f"{_MONTH_ABBR[m - 1]}/{str(y)[2:]}", "value": nw.get((y, m), 0.0)}
        for y, m in months
    ]


def _build_yearly_series(
    current_nw: float, transactions: list[dict], years_back: int
) -> list[dict]:
    flows: dict[int, float] = defaultdict(float)
    for tx in transactions:
        try:
            d = date.fromisoformat(str(tx["transaction_date"]))
        except (KeyError, ValueError):
            continue
        amount = float(tx.get("amount", 0))
        if tx.get("transaction_type") == "receita":
            flows[d.year] += amount
        else:
            flows[d.year] -= amount

    today = date.today()
    years = list(range(today.year - years_back + 1, today.year + 1))

    nw: dict[int, float] = {today.year: current_nw}
    for i in range(len(years) - 1, 0, -1):
        curr, prev = years[i], years[i - 1]
        nw[prev] = nw[curr] - flows.get(curr, 0.0)

    return [{"label": str(y), "value": nw.get(y, 0.0)} for y in years]


def _build_distribution(portfolio: dict, accounts: list[dict]) -> list[dict]:
    totals: dict[str, float] = defaultdict(float)

    for entry in portfolio.get("by_type", []):
        cat = _TYPE_TO_CAT.get(str(entry.get("asset_type", "")), "Outros")
        totals[cat] += float(entry.get("net_invested", 0))

    accounts_total = sum(float(a.get("balance", 0)) for a in accounts)
    if accounts_total > 0:
        totals["Contas"] += accounts_total

    return [
        {"category": c, "value": v, "color": _COLOR_MAP.get(c, "#94A3B8")}
        for c, v in sorted(totals.items(), key=lambda x: x[1], reverse=True)
        if v > 0
    ]


def _build_category_donuts(asset_positions: list[dict]) -> dict[str, list[dict]]:
    """
    Agrupa posições de ativos por categoria para os donuts individuais.

    Retorna dict: categoria → lista de {ticker, name, value, color}
    """
    cat_assets: dict[str, list[dict]] = {}

    for pos in asset_positions:
        asset_type = pos.get("asset_type", "outros")
        cat  = _TYPE_TO_CAT.get(str(asset_type), "Outros")
        value = float(pos.get("estimated_cost", 0))
        if value <= 0:
            continue

        label = pos.get("ticker") or pos.get("name", "?")
        cat_assets.setdefault(cat, []).append({
            "ticker": label,
            "name":   pos.get("name", label),
            "value":  value,
        })

    # Ordena por valor descendente e atribui cores fixas por posição
    for cat, items in cat_assets.items():
        items.sort(key=lambda x: x["value"], reverse=True)
        for i, item in enumerate(items):
            item["color"] = _ASSET_SLOT_COLORS[i % len(_ASSET_SLOT_COLORS)]

    return cat_assets


# ======================================================================
# Widgets de gráfico matplotlib
# ======================================================================

def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


class BarsCanvas(FigureCanvas):
    """Gráfico de barras — patrimônio líquido nos últimos 12 meses."""

    def __init__(self, parent=None) -> None:
        self._fig = Figure(figsize=(8, 2.6), facecolor=_BG_RGB)
        super().__init__(self._fig)
        self.setParent(parent)
        self.setMinimumHeight(260)
        self.setMaximumHeight(290)
        self._ax = self._fig.add_subplot(111)
        self._style_axes(self._ax)

    def update_data(self, monthly: list[dict]) -> None:
        ax = self._ax
        ax.clear()
        self._style_axes(ax)

        labels = [d["label"] for d in monthly]
        values = [d["value"] for d in monthly]
        colors = [_GREEN_RGB if v >= 0 else _RED_RGB for v in values]

        x    = np.arange(len(labels))
        ax.bar(x, values, color=colors, width=0.6, zorder=3)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"R$ {v:,.0f}".replace(",", "."))
        )
        ax.axhline(0, color=_GRID_RGB, linewidth=0.8, zorder=2)

        self._fig.tight_layout(pad=0.4)
        self.draw()

    def _style_axes(self, ax) -> None:
        ax.set_facecolor(_BG_RGB)
        ax.tick_params(colors=_TEXT_RGB, labelsize=9)
        ax.spines[:].set_color(_GRID_RGB)
        ax.xaxis.label.set_color(_TEXT_RGB)
        ax.yaxis.label.set_color(_TEXT_RGB)
        ax.grid(axis="y", color=_GRID_RGB, linewidth=0.6, zorder=0)


class LineCanvas(FigureCanvas):
    """Gráfico de linha — patrimônio acumulado nos últimos 10 anos."""

    def __init__(self, parent=None) -> None:
        self._fig = Figure(figsize=(5, 2.0), facecolor=_BG_RGB)
        super().__init__(self._fig)
        self.setParent(parent)
        self.setMinimumHeight(195)
        self.setMaximumHeight(220)
        self._ax = self._fig.add_subplot(111)
        self._style_axes(self._ax)

    def update_data(self, yearly: list[dict]) -> None:
        ax = self._ax
        ax.clear()
        self._style_axes(ax)

        labels = [d["label"] for d in yearly]
        values = [d["value"] for d in yearly]
        x = np.arange(len(labels))

        ax.plot(x, values, color=_BLUE_RGB, linewidth=2, marker="o",
                markersize=4, zorder=3)
        ax.fill_between(x, values, alpha=0.12, color=_BLUE_RGB, zorder=2)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8, rotation=30, ha="right")
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"R${v/1000:.0f}k" if abs(v) >= 1000 else f"R${v:.0f}")
        )

        self._fig.tight_layout(pad=0.4)
        self.draw()

    def _style_axes(self, ax) -> None:
        ax.set_facecolor(_BG_RGB)
        ax.tick_params(colors=_TEXT_RGB, labelsize=8)
        ax.spines[:].set_color(_GRID_RGB)
        ax.grid(color=_GRID_RGB, linewidth=0.5, zorder=0)


class DonutCanvas(FigureCanvas):
    """
    Gráfico de donut com hover — distribuição por categoria ou por ativo.
    """

    def __init__(self, figsize=(2.5, 2.0), parent=None) -> None:
        self._fig = Figure(figsize=figsize, facecolor=_BG_RGB)
        super().__init__(self._fig)
        self.setParent(parent)
        self.setMinimumHeight(195)
        self.setMaximumHeight(220)
        self._ax     = self._fig.add_subplot(111)
        self._wedges: list = []
        self._labels: list[str] = []
        self._values: list[float] = []
        self._annot  = None
        self.mpl_connect("motion_notify_event", self._on_hover)

    def update_data(self, distribution: list[dict]) -> None:
        ax = self._ax
        ax.clear()
        ax.set_facecolor(_BG_RGB)

        if not distribution:
            ax.text(0.5, 0.5, "Sem dados", ha="center", va="center",
                    color=_TEXT_RGB, fontsize=10, transform=ax.transAxes)
            self._wedges = []
            self._annot  = None
            self.draw()
            return

        self._labels = [d["category"] for d in distribution]
        self._values = [d["value"]    for d in distribution]
        colors       = [_hex_to_rgb(d["color"]) for d in distribution]

        wedges, _ = ax.pie(
            self._values,
            colors=colors,
            startangle=90,
            wedgeprops=dict(width=0.48, edgecolor=_BG_RGB, linewidth=1.5),
        )
        self._wedges = wedges

        self._annot = ax.annotate(
            "", xy=(0, 0),
            xytext=(0.05, -0.12),
            textcoords="axes fraction",
            fontsize=8,
            color=_TEXT_RGB,
            ha="center",
            bbox=dict(boxstyle="round,pad=0.3", fc=_GRID_RGB, ec="none", alpha=0.9),
            visible=False,
        )

        self._fig.tight_layout(pad=0.2)
        self.draw()

    def _on_hover(self, event) -> None:
        if event.inaxes != self._ax or not self._wedges or self._annot is None:
            return
        for i, wedge in enumerate(self._wedges):
            if wedge.contains_point([event.x, event.y]):
                total = sum(self._values) or 1
                pct   = self._values[i] / total * 100
                self._annot.set_text(
                    f"{self._labels[i]}\n"
                    f"{_fmt_brl(self._values[i])}  ({pct:.1f}%)"
                )
                self._annot.set_visible(True)
                self.draw_idle()
                return
        if self._annot.get_visible():
            self._annot.set_visible(False)
            self.draw_idle()


# ======================================================================
# Widgets de UI reutilizáveis
# ======================================================================

class SummaryCard(QFrame):
    """Card compacto com título, valor principal e subtítulo opcional."""

    def __init__(self, title: str, default_color: str = "#E8EAED") -> None:
        super().__init__()
        self.setObjectName("summaryCard")
        self._default_color = default_color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(6)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("cardTitle")

        self._value_label = QLabel("—")
        self._value_label.setObjectName("cardValue")
        self._value_label.setStyleSheet(f"color: {default_color};")

        self._sub_label = QLabel("")
        self._sub_label.setObjectName("cardSub")
        self._sub_label.setStyleSheet("color: #8B90A7; font-size: 11px;")
        self._sub_label.setVisible(False)

        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)
        layout.addWidget(self._sub_label)

    def set_value(self, value: str, color: str | None = None, sub: str = "") -> None:
        self._value_label.setText(value)
        self._value_label.setStyleSheet(f"color: {color or self._default_color};")
        self._sub_label.setText(sub)
        self._sub_label.setVisible(bool(sub))


class DebtProgressRow(QFrame):
    """
    Linha de progresso para uma dívida:
      Nome + instituição | barra colorida (parcelas_pagas/total) | valor pago vs total | taxa%
    """

    def __init__(self, debt: dict) -> None:
        super().__init__()
        self.setObjectName("debtRow")
        self.setStyleSheet(
            f"QFrame#debtRow {{ background: {_GRID}; border-radius: 8px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        # Cabeçalho: nome + taxa
        header = QHBoxLayout()
        name_lbl = QLabel(f"{debt.get('name', '')}  ·  {debt.get('institution', '')}")
        name_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px; font-weight: 600; background: transparent;")
        rate_lbl = QLabel(f"{float(debt.get('interest_rate', 0)):.2f}% a.m.")
        rate_lbl.setStyleSheet("color: #FF6B6B; font-size: 11px; background: transparent;")
        rate_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(name_lbl)
        header.addStretch()
        header.addWidget(rate_lbl)

        # Barra de progresso
        paid  = int(debt.get("paid_installments", 0))
        total = int(debt.get("total_installments", 1))
        pct   = int(paid / max(total, 1) * 100)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setFormat(f"{paid}/{total} parcelas  ({pct}%)")
        bar.setFixedHeight(18)
        bar.setTextVisible(True)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background: #2A2D3E;
                border: none;
                border-radius: 4px;
                color: white;
                font-size: 10px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background: #FF6B6B;
                border-radius: 4px;
            }}
        """)

        # Rodapé: valor pago vs total
        remaining = float(debt.get("remaining_amount", 0))
        total_amt = float(debt.get("total_amount", 0))
        paid_amt  = total_amt - remaining
        footer_lbl = QLabel(
            f"Pago: {_fmt_brl(paid_amt)}  ·  Restante: {_fmt_brl(remaining)}  ·  Total: {_fmt_brl(total_amt)}"
        )
        footer_lbl.setStyleSheet("color: #8B90A7; font-size: 10px; background: transparent;")

        layout.addLayout(header)
        layout.addWidget(bar)
        layout.addWidget(footer_lbl)


class AlertRow(QFrame):
    """Linha de alerta com indicador de prioridade colorido."""

    _PRIORITY_COLOR: dict[str, str] = {
        "ALTA":  "#FF6B6B",
        "MÉDIA": "#FFB347",
        "BAIXA": "#4A9EFF",
    }

    def __init__(self, alert: dict) -> None:
        super().__init__()
        self.setObjectName("alertItem")

        priority  = alert.get("priority", "BAIXA")
        dot_color = self._PRIORITY_COLOR.get(priority, "#4A9EFF")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)

        dot = QLabel("●")
        dot.setFixedWidth(14)
        dot.setStyleSheet(f"color: {dot_color}; font-size: 11px;")
        dot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)

        title_label = QLabel(alert.get("title", ""))
        title_label.setObjectName("alertTitle")

        msg_label = QLabel(alert.get("message", ""))
        msg_label.setObjectName("alertMessage")
        msg_label.setWordWrap(True)

        text_col.addWidget(title_label)
        text_col.addWidget(msg_label)

        layout.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_col)
        layout.addStretch()


class CategoryDonutWidget(QFrame):
    """
    Widget composto: título da categoria + donut dos ativos + legenda lateral.

    Exibe a distribuição de ativos individuais dentro de uma categoria
    (ex: todas as ações da carteira com suas proporções).
    """

    def __init__(self, category: str, assets: list[dict]) -> None:
        super().__init__()
        self.setObjectName("categoryDonutCard")
        self.setStyleSheet(
            f"QFrame#categoryDonutCard {{ background: {_GRID}; border-radius: 8px; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        cat_color = _COLOR_MAP.get(category, "#94A3B8")
        title = QLabel(category)
        title.setStyleSheet(
            f"color: {cat_color}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        outer.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(12)

        # Donut
        canvas = DonutCanvas(figsize=(2.2, 1.8))
        canvas.setMinimumHeight(170)
        canvas.setMaximumHeight(190)
        distribution = [
            {"category": a["ticker"], "value": a["value"], "color": a["color"]}
            for a in assets
        ]
        canvas.update_data(distribution)
        row.addWidget(canvas, stretch=50)

        # Legenda
        legend = QWidget()
        legend.setStyleSheet("background: transparent;")
        legend_layout = QVBoxLayout(legend)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(4)

        total = sum(a["value"] for a in assets) or 1
        for asset in assets[:8]:  # máximo 8 itens
            pct = asset["value"] / total * 100
            color = asset["color"]

            item = QWidget()
            item.setStyleSheet("background: transparent;")
            item_layout = QVBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 2)
            item_layout.setSpacing(2)

            hdr = QHBoxLayout()
            dot_lbl = QLabel("●")
            dot_lbl.setStyleSheet(f"color: {color}; font-size: 9px; background: transparent;")
            dot_lbl.setFixedWidth(12)
            ticker_lbl = QLabel(asset["ticker"])
            ticker_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 10px; font-weight: 600; background: transparent;")
            val_lbl = QLabel(_fmt_brl(asset["value"]))
            val_lbl.setStyleSheet("color: #8B90A7; font-size: 9px; background: transparent;")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hdr.addWidget(dot_lbl)
            hdr.addWidget(ticker_lbl)
            hdr.addStretch()
            hdr.addWidget(val_lbl)

            pct_lbl = QLabel(f"{pct:.1f}%")
            pct_lbl.setStyleSheet("color: #8B90A7; font-size: 9px; background: transparent;")
            pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

            item_layout.addLayout(hdr)
            item_layout.addWidget(pct_lbl)
            legend_layout.addWidget(item)

        legend_layout.addStretch()
        row.addWidget(legend, stretch=50)
        outer.addLayout(row)


# ======================================================================
# Página principal do Dashboard
# ======================================================================

class DashboardPage(QWidget):
    """
    Dashboard completo com duas linhas de cards, seção de dívidas,
    gráficos de evolução patrimonial, donuts por categoria e alertas.
    """

    def __init__(self) -> None:
        super().__init__()
        self._client     = ApiClient()
        self._worker:     DashboardWorker        | None = None
        self._pat_worker: PatrimonyHistoryWorker | None = None

        self._build_ui()
        self.load_data()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        scroll = self._scroll
        scroll.setObjectName("dashboardScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("dashboardContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(32, 28, 32, 32)
        self._content_layout.setSpacing(24)

        # Loading label
        self._loading_label = QLabel("Carregando dados do dashboard…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_layout.addWidget(self._loading_label)

        # ── Linha 1: 4 cards patrimoniais ──────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        self._card_d0         = SummaryCard("Patrimônio D+0",         "#4A9EFF")
        self._card_patrimonio = SummaryCard("Patrimônio Total",        "#4A9EFF")
        self._card_reserva    = SummaryCard("Reserva de Emergência",   "#00C896")
        self._card_score      = SummaryCard("Score de Saúde",          "#00C896")
        for c in [self._card_d0, self._card_patrimonio, self._card_reserva, self._card_score]:
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            c.setVisible(False)
            row1.addWidget(c)
        self._content_layout.addLayout(row1)

        # ── Linha 2: 3 cards mensais ───────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        self._card_receitas = SummaryCard("Receitas do Mês",  "#00C896")
        self._card_despesas = SummaryCard("Despesas do Mês",  "#FF6B6B")
        self._card_saldo    = SummaryCard("Saldo do Mês",     "#4A9EFF")
        for c in [self._card_receitas, self._card_despesas, self._card_saldo]:
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            c.setVisible(False)
            row2.addWidget(c)
        # Espaçador para não esticar os 3 cards como se fossem 4
        row2.addStretch(1)
        self._content_layout.addLayout(row2)

        # ── Seção de dívidas ───────────────────────────────────────────
        self._debts_title = QLabel("Dívidas e Financiamentos")
        self._debts_title.setObjectName("sectionTitle")
        self._debts_title.setVisible(False)
        self._content_layout.addWidget(self._debts_title)

        self._debts_area = QVBoxLayout()
        self._debts_area.setSpacing(10)
        self._content_layout.addLayout(self._debts_area)

        # ── Área de gráficos principais ────────────────────────────────
        self._charts_widget = self._build_charts_area()
        self._charts_widget.setVisible(False)
        self._content_layout.addWidget(self._charts_widget)

        # ── Seção de donuts por categoria ──────────────────────────────
        self._cat_donuts_title = QLabel("Distribuição por Categoria")
        self._cat_donuts_title.setObjectName("sectionTitle")
        self._cat_donuts_title.setVisible(False)
        self._content_layout.addWidget(self._cat_donuts_title)

        self._cat_donuts_grid_widget = QWidget()
        self._cat_donuts_grid_widget.setVisible(False)
        self._cat_donuts_grid = QGridLayout(self._cat_donuts_grid_widget)
        self._cat_donuts_grid.setSpacing(16)
        self._content_layout.addWidget(self._cat_donuts_grid_widget)

        # ── Alertas ────────────────────────────────────────────────────
        self._alerts_title = QLabel("Alertas Ativos")
        self._alerts_title.setObjectName("sectionTitle")
        self._alerts_title.setVisible(False)
        self._content_layout.addWidget(self._alerts_title)

        self._alerts_area = QVBoxLayout()
        self._alerts_area.setSpacing(8)
        self._content_layout.addLayout(self._alerts_area)

        # ── Botão atualizar ────────────────────────────────────────────
        self._reload_btn = QPushButton("Atualizar")
        self._reload_btn.setFixedWidth(120)
        self._reload_btn.setVisible(False)
        self._reload_btn.clicked.connect(self.load_data)
        self._content_layout.addWidget(
            self._reload_btn, alignment=Qt.AlignmentFlag.AlignRight
        )

        self._content_layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_charts_area(self) -> QWidget:
        container = QWidget()
        container.setObjectName("chartsArea")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(12)

        self._charts_empty_msg = QLabel(
            "Adicione lançamentos para ver a evolução patrimonial"
        )
        self._charts_empty_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._charts_empty_msg.setStyleSheet(
            f"color: {_TEXT}; font-size: 13px; padding: 40px;"
            f"background: {_BG}; border-radius: 8px;"
        )
        self._charts_empty_msg.setVisible(False)
        vbox.addWidget(self._charts_empty_msg)

        # Barras mensais
        self._bars_canvas = BarsCanvas()
        vbox.addWidget(self._bars_canvas)

        # Linha + donut principal
        self._row2_widget = QWidget()
        row2_hbox = QHBoxLayout(self._row2_widget)
        row2_hbox.setContentsMargins(0, 0, 0, 0)
        row2_hbox.setSpacing(12)

        self._line_canvas = LineCanvas()
        row2_hbox.addWidget(self._line_canvas, stretch=45)

        donut_container = QWidget()
        donut_hbox = QHBoxLayout(donut_container)
        donut_hbox.setContentsMargins(0, 0, 0, 0)
        donut_hbox.setSpacing(12)

        self._donut_canvas = DonutCanvas()
        donut_hbox.addWidget(self._donut_canvas, stretch=55)

        self._legend_widget = QWidget()
        self._legend_widget.setStyleSheet(f"background: {_BG}; border-radius: 8px;")
        self._legend_layout = QVBoxLayout(self._legend_widget)
        self._legend_layout.setContentsMargins(8, 8, 8, 8)
        self._legend_layout.setSpacing(6)
        donut_hbox.addWidget(self._legend_widget, stretch=45)

        row2_hbox.addWidget(donut_container, stretch=55)
        vbox.addWidget(self._row2_widget)

        return container

    # ------------------------------------------------------------------
    # Carregamento de dados
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        if self._pat_worker and self._pat_worker.isRunning():
            return

        self._set_content_visible(False)
        self._loading_label.setText("Carregando dados do dashboard…")
        self._loading_label.setVisible(True)
        self._reload_btn.setVisible(False)

        self._worker = DashboardWorker(self._client)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

        self._pat_worker = PatrimonyHistoryWorker(self._client)
        self._pat_worker.patrimony_ready.connect(self._on_patrimony_ready)
        self._pat_worker.error_occurred.connect(self._on_patrimony_error)
        self._pat_worker.start()

    # ------------------------------------------------------------------
    # Slots — DashboardWorker
    # ------------------------------------------------------------------

    def _on_data_ready(
        self,
        dashboard: dict,
        alerts: dict,
        emergency_fund: dict,
        debts: list,
    ) -> None:
        self._loading_label.setVisible(False)
        self._set_content_visible(True)

        net_worth = dashboard.get("net_worth", {})
        monthly   = dashboard.get("monthly_summary", {})
        health    = dashboard.get("health_score", {})

        # ── Linha 1 ────────────────────────────────────────────────────

        # D+0: saldo em contas (proxy — idealmente viria de /portfolio/liquidity)
        d0 = float(net_worth.get("account_balance", 0))
        self._card_d0.set_value(
            _fmt_brl(d0),
            color="#4A9EFF",
            sub="Liquidez imediata (contas)",
        )

        nw = float(net_worth.get("net_worth", 0))
        self._card_patrimonio.set_value(
            _fmt_brl(nw),
            color="#4A9EFF" if nw >= 0 else "#FF6B6B",
            sub=f"Ativos: {_fmt_brl(float(net_worth.get('total_assets', 0)))}",
        )

        ef_saldo   = float(emergency_fund.get("saldo_total", 0))
        ef_meses   = float(emergency_fund.get("meses_cobertos", 0))
        if ef_meses >= 6:
            ef_color, ef_status = "#00C896", f"{ef_meses:.1f} meses cobertos ✓"
        elif ef_meses >= 3:
            ef_color, ef_status = "#FFB347", f"{ef_meses:.1f} meses cobertos"
        else:
            ef_color, ef_status = "#FF6B6B", f"{ef_meses:.1f} meses cobertos ⚠"
        self._card_reserva.set_value(_fmt_brl(ef_saldo), color=ef_color, sub=ef_status)

        score = int(health.get("total", 0))
        score_color = "#00C896" if score >= 60 else ("#FFB347" if score >= 40 else "#FF6B6B")
        self._card_score.set_value(f"{score} / 100", color=score_color)

        # ── Linha 2 ────────────────────────────────────────────────────

        income  = float(monthly.get("income", 0))
        expense = float(monthly.get("expense", 0))
        balance = float(monthly.get("balance", 0))
        ref     = monthly.get("reference_month", "")

        self._card_receitas.set_value(_fmt_brl(income),  color="#00C896", sub=ref)
        self._card_despesas.set_value(
            _fmt_brl(expense), color="#FF6B6B",
            sub=f"Taxa poupança: {float(monthly.get('savings_rate', 0)):.1f}%",
        )
        self._card_saldo.set_value(
            _fmt_brl(balance),
            color="#00C896" if balance >= 0 else "#FF6B6B",
            sub=ref,
        )

        # ── Dívidas ────────────────────────────────────────────────────
        self._populate_debts(debts)

        # ── Alertas ────────────────────────────────────────────────────
        self._populate_alerts(alerts.get("alerts", []))
        self._reload_btn.setVisible(True)

        # Garante que o scroll retorne ao topo após todos os widgets aparecerem
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(0))

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._loading_label.setVisible(True)
        self._set_content_visible(False)
        self._reload_btn.setVisible(True)

    # ------------------------------------------------------------------
    # Slots — PatrimonyHistoryWorker
    # ------------------------------------------------------------------

    def _on_patrimony_ready(self, data: dict) -> None:
        monthly = data["monthly_bars"]
        yearly  = data["yearly_line"]
        distrib = data["distribution"]
        cat_donuts = data.get("category_donuts", {})

        has_data = (
            any(abs(d["value"]) > 0.01 for d in monthly)
            or bool(distrib)
        )

        if not has_data:
            self._charts_empty_msg.setVisible(True)
            self._bars_canvas.setVisible(False)
            self._row2_widget.setVisible(False)
        else:
            self._charts_empty_msg.setVisible(False)
            self._bars_canvas.setVisible(True)
            self._row2_widget.setVisible(True)
            self._bars_canvas.update_data(monthly)
            self._line_canvas.update_data(yearly)
            self._donut_canvas.update_data(distrib)
            self._populate_legend(distrib[:5], sum(d["value"] for d in distrib))

        self._populate_category_donuts(cat_donuts)

    def _on_patrimony_error(self, message: str) -> None:
        self._charts_empty_msg.setText(f"Erro ao carregar gráficos: {message}")
        self._charts_empty_msg.setVisible(True)
        self._bars_canvas.setVisible(False)
        self._row2_widget.setVisible(False)

    # ------------------------------------------------------------------
    # Helpers de UI
    # ------------------------------------------------------------------

    def _all_cards(self) -> list[SummaryCard]:
        return [
            self._card_d0,
            self._card_patrimonio,
            self._card_reserva,
            self._card_score,
            self._card_receitas,
            self._card_despesas,
            self._card_saldo,
        ]

    def _set_content_visible(self, visible: bool) -> None:
        for card in self._all_cards():
            card.setVisible(visible)
        self._charts_widget.setVisible(visible)
        self._alerts_title.setVisible(visible)
        self._debts_title.setVisible(visible)

    def _populate_debts(self, debts: list[dict]) -> None:
        # Limpa rows anteriores
        while self._debts_area.count():
            item = self._debts_area.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        if not debts:
            empty = QLabel("Nenhuma dívida cadastrada")
            empty.setObjectName("noAlertsLabel")
            self._debts_area.addWidget(empty)
            return

        for debt in debts:
            self._debts_area.addWidget(DebtProgressRow(debt))

    def _populate_alerts(self, alerts: list[dict]) -> None:
        while self._alerts_area.count():
            item = self._alerts_area.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        if not alerts:
            no_alerts = QLabel("Nenhum alerta ativo no momento.")
            no_alerts.setObjectName("noAlertsLabel")
            self._alerts_area.addWidget(no_alerts)
            return

        for alert_data in alerts:
            self._alerts_area.addWidget(AlertRow(alert_data))

    def _populate_legend(self, top5: list[dict], total: float) -> None:
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        for entry in top5:
            cat   = entry["category"]
            value = entry["value"]
            color = entry["color"]
            pct   = value / total * 100 if total > 0 else 0

            row = QWidget()
            row.setStyleSheet("background: transparent;")
            vbox = QVBoxLayout(row)
            vbox.setContentsMargins(0, 2, 0, 2)
            vbox.setSpacing(3)

            header = QHBoxLayout()
            name_lbl = QLabel(cat)
            name_lbl.setStyleSheet(
                f"color: {color}; font-size: 11px; font-weight: 600; background: transparent;"
            )
            val_lbl = QLabel(_fmt_brl(value))
            val_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 10px; background: transparent;")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            header.addWidget(name_lbl)
            header.addStretch()
            header.addWidget(val_lbl)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(pct))
            bar.setFormat(f"{pct:.1f}%")
            bar.setFixedHeight(14)
            bar.setTextVisible(True)
            bar.setStyleSheet(f"""
                QProgressBar {{
                    background: #2A2D3E;
                    border: none;
                    border-radius: 3px;
                    color: white;
                    font-size: 9px;
                    text-align: center;
                }}
                QProgressBar::chunk {{
                    background: {color};
                    border-radius: 3px;
                }}
            """)

            vbox.addLayout(header)
            vbox.addWidget(bar)
            self._legend_layout.addWidget(row)

        self._legend_layout.addStretch()

    def _populate_category_donuts(self, cat_donuts: dict[str, list[dict]]) -> None:
        # Limpa grade anterior
        while self._cat_donuts_grid.count():
            item = self._cat_donuts_grid.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        if not cat_donuts:
            self._cat_donuts_title.setVisible(False)
            self._cat_donuts_grid_widget.setVisible(False)
            return

        self._cat_donuts_title.setVisible(True)
        self._cat_donuts_grid_widget.setVisible(True)

        # Ordena categorias por valor total descendente
        sorted_cats = sorted(
            cat_donuts.items(),
            key=lambda kv: sum(a["value"] for a in kv[1]),
            reverse=True,
        )

        for idx, (cat, assets) in enumerate(sorted_cats):
            row_idx = idx // 2
            col_idx = idx % 2
            widget  = CategoryDonutWidget(cat, assets)
            self._cat_donuts_grid.addWidget(widget, row_idx, col_idx)


# ======================================================================
# Utilitário de formatação
# ======================================================================

def _fmt_brl(value: float) -> str:
    """Formata número como moeda brasileira: R$ 1.234,56"""
    try:
        formatted = f"{abs(value):,.2f}"
        formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
        prefix = "-R$ " if value < 0 else "R$ "
        return f"{prefix}{formatted}"
    except (TypeError, ValueError):
        return "—"
