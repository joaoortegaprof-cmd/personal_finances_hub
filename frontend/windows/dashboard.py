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
    QHeaderView,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QPushButton,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.colors import (
    COLOR_ASSET, COLOR_EXPENSE, COLOR_INVESTMENT, COLOR_WARNING,
    COLOR_INCOME, COLOR_NEUTRAL, COLOR_PATRIMONY, COLOR_BALANCE,
    COLOR_MUTED, COLOR_BG, COLOR_GRID,
    COLOR_STOCK, COLOR_FII, COLOR_ETF, COLOR_TREASURY,
    COLOR_FIXED_INCOME, COLOR_CRYPTO, COLOR_INTERNATIONAL,
    COLOR_PENSION, COLOR_OTHER, COLOR_CASH,
    COLOR_ASSET_RGB, COLOR_EXPENSE_RGB, COLOR_INVESTMENT_RGB,
    CATEGORY_COLOR, CATEGORY_PALETTE, hex_to_rgb,
)
from frontend.components.icons import icon as _svg_icon
from frontend.components.signals import app_signals
from frontend.windows.settings_page import load_settings


# ======================================================================
# Constantes visuais
# ======================================================================

_BG    = COLOR_BG
_TEXT  = COLOR_NEUTRAL
_GREEN = COLOR_ASSET
_RED   = COLOR_EXPENSE
_BLUE  = COLOR_INVESTMENT
_GRID  = COLOR_GRID

_BG_RGB    = hex_to_rgb(COLOR_BG)
_TEXT_RGB  = hex_to_rgb(COLOR_NEUTRAL)
_GREEN_RGB = COLOR_ASSET_RGB
_RED_RGB   = COLOR_EXPENSE_RGB
_BLUE_RGB  = COLOR_INVESTMENT_RGB
_GRID_RGB  = hex_to_rgb(COLOR_GRID)

# Categorias semânticas do dashboard
_CATEGORIES = ["Ações", "FIIs", "ETFs", "Tesouro", "Renda Fixa", "Contas", "Cripto", "Outros"]
_CAT_COLORS = [
    COLOR_STOCK, COLOR_FII, COLOR_ETF, COLOR_TREASURY,
    COLOR_FIXED_INCOME, COLOR_CASH, COLOR_CRYPTO, COLOR_OTHER,
]
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
    COLOR_STOCK, COLOR_FII, COLOR_ETF, COLOR_TREASURY,
    COLOR_FIXED_INCOME, COLOR_CRYPTO, COLOR_WARNING, COLOR_PENSION,
    COLOR_INTERNATIONAL, COLOR_CASH,
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

    data_ready     = pyqtSignal(dict, dict, dict, list, dict)  # dashboard, alerts, ef, debts, essential_cost
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            dashboard = self._client.get_dashboard()
            cfg = load_settings()
            alerts = self._client.get_alerts(
                invoice_due_days   = cfg.get("invoice_alert_days",   3),
                maturity_days_ahead= cfg.get("maturity_alert_days",  30),
                savings_goal_pct   = cfg.get("savings_alert_pct",    15.0),
                debt_due_days      = cfg.get("debt_alert_days",       3),
                recurring_due_days = cfg.get("recurring_alert_days",  7),
                min_liquidity      = cfg.get("min_liquidity",         0.0),
                enabled            = cfg.get("enabled_alerts",        None),
            )

            try:
                emergency_fund = self._client.get_emergency_fund()
            except ApiError:
                emergency_fund = {"saldo_total": 0, "media_gastos_6m": 0, "meses_cobertos": 0}

            try:
                debts = self._client.get_debts()
            except ApiError:
                debts = []

            try:
                essential_cost = self._client.get_essential_cost()
            except ApiError:
                essential_cost = {"monthly_average": 0, "breakdown": []}

            self.data_ready.emit(dashboard, alerts, emergency_fund, debts, essential_cost)
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
    """
    Agrega receitas e despesas por mês nos últimos `months_back` meses.

    Retorna lista de dicts com:
      - label:   "Mai/26"
      - income:  total de receitas do mês
      - expense: total de despesas do mês (valor positivo)
      - balance: income - expense
    """
    income_map:  dict[tuple[int, int], float] = defaultdict(float)
    expense_map: dict[tuple[int, int], float] = defaultdict(float)

    today = date.today()
    cutoff_year  = today.year  - (months_back // 12 + 1)
    cutoff_month = today.month

    investment_map: dict[tuple[int, int], float] = defaultdict(float)

    for tx in transactions:
        try:
            d = date.fromisoformat(str(tx["transaction_date"]))
        except (KeyError, ValueError):
            continue

        # Ignora meses fora da janela
        months_ago = (today.year - d.year) * 12 + (today.month - d.month)
        if not (0 <= months_ago < months_back):
            continue

        amount  = float(tx.get("amount", 0))
        tx_type = tx.get("transaction_type", "")
        key     = (d.year, d.month)

        if tx_type == "income":
            income_map[key] += amount
        elif tx_type in ("debit", "expense", "transfer"):
            expense_map[key] += amount
        elif tx_type == "investment":
            investment_map[key] += amount
        # invoice payments: ignorados (já estão no crédito do cartão)

    months: list[tuple[int, int]] = []
    for i in range(months_back - 1, -1, -1):
        m, y = today.month - i, today.year
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))

    return [
        {
            "label":      f"{_MONTH_ABBR[m - 1]}/{str(y)[2:]}",
            "income":     income_map.get((y, m), 0.0),
            "expense":    expense_map.get((y, m), 0.0),
            "investment": investment_map.get((y, m), 0.0),
            "balance":    income_map.get((y, m), 0.0) - expense_map.get((y, m), 0.0),
        }
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
        tx_type = tx.get("transaction_type")
        if tx_type == "income":
            flows[d.year] += amount
        elif tx_type != "invoice":
            flows[d.year] -= amount

    today = date.today()
    years = list(range(today.year - years_back + 1, today.year + 1))

    nw: dict[int, float] = {today.year: current_nw}
    for i in range(len(years) - 1, 0, -1):
        curr, prev = years[i], years[i - 1]
        nw[prev] = nw[curr] - flows.get(curr, 0.0)

    return [{"label": str(y), "value": nw.get(y, 0.0)} for y in years]


def _build_distribution(portfolio: dict, accounts: list[dict]) -> list[dict]:
    """
    Distribuição geral por categoria — usa a cor semântica principal de cada tipo.
    """
    totals: dict[str, float] = defaultdict(float)
    # Mapa categoria → asset_type para recuperar a cor do CATEGORY_COLOR
    _cat_to_type: dict[str, str] = {
        "Ações":     "acao",
        "FIIs":      "fii",
        "ETFs":      "etf",
        "Tesouro":   "tesouro_direto",
        "Renda Fixa":"renda_fixa",
        "Cripto":    "criptomoeda",
        "Contas":    "outros",
        "Outros":    "outros",
    }

    for entry in portfolio.get("by_type", []):
        cat = _TYPE_TO_CAT.get(str(entry.get("asset_type", "")), "Outros")
        totals[cat] += float(entry.get("net_invested", 0))

    accounts_total = sum(float(a.get("balance", 0)) for a in accounts)
    if accounts_total > 0:
        totals["Contas"] += accounts_total

    return [
        {
            "category": c,
            "value":    v,
            # Cor da categoria via CATEGORY_COLOR → cor semântica consistente
            "color": CATEGORY_COLOR.get(_cat_to_type.get(c, "outros"), _COLOR_MAP.get(c, COLOR_OTHER)),
        }
        for c, v in sorted(totals.items(), key=lambda x: x[1], reverse=True)
        if v > 0
    ]


def _build_category_donuts(asset_positions: list[dict]) -> dict[str, list[dict]]:
    """
    Agrupa posições de ativos por categoria para os donuts individuais.

    Retorna dict: categoria → lista de {ticker, name, value, color}

    Cada categoria usa sua própria PALETTE_* (tons do escuro ao claro),
    garantindo que ativos do mesmo tipo tenham cores consistentes.
    """
    # Mapeia categoria display → asset_type para recuperar a paleta
    _cat_to_type: dict[str, str] = {
        "Ações":      "acao",
        "FIIs":       "fii",
        "ETFs":       "etf",
        "Tesouro":    "tesouro_direto",
        "Renda Fixa": "renda_fixa",
        "Cripto":     "criptomoeda",
        "Outros":     "outros",
    }

    cat_assets: dict[str, list[dict]] = {}

    for pos in asset_positions:
        asset_type = pos.get("asset_type", "outros")
        cat   = _TYPE_TO_CAT.get(str(asset_type), "Outros")
        value = float(pos.get("estimated_cost", 0))
        if value <= 0:
            continue

        label = pos.get("ticker") or pos.get("name", "?")
        cat_assets.setdefault(cat, []).append({
            "ticker": label,
            "name":   pos.get("name", label),
            "value":  value,
        })

    # Ordena por valor descendente e atribui cores da paleta da categoria
    for cat, items in cat_assets.items():
        items.sort(key=lambda x: x["value"], reverse=True)
        atype   = _cat_to_type.get(cat, "outros")
        palette = CATEGORY_PALETTE.get(atype, _ASSET_SLOT_COLORS)
        for i, item in enumerate(items):
            item["color"] = palette[i % len(palette)]

    return cat_assets


# ======================================================================
# Widgets de gráfico matplotlib
# ======================================================================

def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


class BarsCanvas(FigureCanvas):
    """
    Gráfico de barras agrupadas — Receitas vs Despesas mensais (últimos 12 meses).

    Duas barras por mês (verde = receitas, vermelho = despesas) com uma linha
    de saldo (receita − despesa) em branco.  Muito mais acionável do que a
    reconstrução retroativa de patrimônio.
    """

    def __init__(self, parent=None) -> None:
        self._fig = Figure(figsize=(8, 2.8), facecolor=_BG_RGB)
        super().__init__(self._fig)
        self.setParent(parent)
        self.setMinimumHeight(270)
        self.setMaximumHeight(300)
        self._ax = self._fig.add_subplot(111)
        self._style_axes(self._ax)

    def update_data(self, monthly: list[dict]) -> None:
        ax = self._ax
        ax.clear()
        self._style_axes(ax)

        labels      = [d["label"]   for d in monthly]
        incomes     = [d.get("income",      d.get("value", 0)) for d in monthly]
        expenses    = [d.get("expense",     0)                  for d in monthly]
        investments = [d.get("investment",  0)                  for d in monthly]
        balances    = [d.get("balance", i - e) for d, i, e in zip(monthly, incomes, expenses)]

        x     = np.arange(len(labels))
        # 3 barras por mês: cada uma tem largura 0.22, centradas em x
        w = 0.22
        offsets = (-w, 0, w)

        ax.bar(x + offsets[0], incomes,     w, color=_GREEN_RGB,      alpha=0.88,
               label="Receitas",     zorder=3)
        ax.bar(x + offsets[1], expenses,    w, color=_RED_RGB,         alpha=0.88,
               label="Despesas",     zorder=3)
        ax.bar(x + offsets[2], investments, w, color=_BLUE_RGB,        alpha=0.88,
               label="Investimentos", zorder=3)

        # Linha de saldo tracejada
        ax.plot(x, balances, color=(1.0, 1.0, 1.0, 0.75), linewidth=1.2,
                linestyle="--", marker="o", markersize=3, zorder=4, label="Saldo")

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"R$ {v:,.0f}".replace(",", "."))
        )
        ax.axhline(0, color=_GRID_RGB, linewidth=0.8, zorder=2)

        ax.legend(
            loc="upper left", fontsize=7,
            facecolor=_BG_RGB, labelcolor=_TEXT_RGB,
            framealpha=0.85, edgecolor=_GRID_RGB,
            ncol=4,
        )

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

    O tooltip usa um QLabel flutuante (filho do canvas Qt) em vez de anotação
    matplotlib, evitando problemas de z-order e renderização no backend qtagg.
    """

    def __init__(self, figsize=(2.5, 2.0), parent=None, bg_color=None) -> None:
        self._bg = bg_color if bg_color is not None else _BG_RGB
        self._fig = Figure(figsize=figsize, facecolor=self._bg)
        super().__init__(self._fig)
        self.setParent(parent)
        self.setStyleSheet("background: transparent;")
        self.setMinimumHeight(195)
        self.setMaximumHeight(220)
        self._ax     = self._fig.add_subplot(111)
        self._ax.set_facecolor(self._bg)
        self._wedges: list = []
        self._labels: list[str] = []
        self._values: list[float] = []

        # Tooltip flutuante como widget Qt (não matplotlib) — fica sempre na frente
        self._tooltip_label = QLabel(self)
        self._tooltip_label.setStyleSheet(
            "background-color: #2A2E4A;"
            "color: #FFFFFF;"
            "padding: 8px;"
            "border-radius: 6px;"
            "border: 1px solid #4A9EFF;"
            "font-size: 11px;"
        )
        self._tooltip_label.setWordWrap(False)
        self._tooltip_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._tooltip_label.hide()

        self.mpl_connect("motion_notify_event", self._on_hover)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._tooltip_label.hide()
        super().leaveEvent(event)

    def update_data(self, distribution: list[dict]) -> None:
        ax = self._ax
        ax.clear()
        ax.set_facecolor(self._bg)

        if not distribution:
            ax.text(0.5, 0.5, "Sem dados", ha="center", va="center",
                    color=_TEXT_RGB, fontsize=10, transform=ax.transAxes)
            self._wedges = []
            self.draw()
            return

        self._labels = [d["category"] for d in distribution]
        self._values = [d["value"]    for d in distribution]
        colors       = [_hex_to_rgb(d["color"]) for d in distribution]

        wedges, _ = ax.pie(
            self._values,
            colors=colors,
            startangle=90,
            wedgeprops=dict(width=0.48, edgecolor=self._bg, linewidth=1.5),
        )
        self._wedges = wedges

        self._fig.tight_layout(pad=0.2)
        self.draw()

    def _on_hover(self, event) -> None:
        if event.inaxes != self._ax or not self._wedges:
            self._tooltip_label.hide()
            return
        for i, wedge in enumerate(self._wedges):
            if wedge.contains_point([event.x, event.y]):
                total = sum(self._values) or 1
                pct   = self._values[i] / total * 100
                self._tooltip_label.setText(
                    f"<b>{self._labels[i]}</b><br>"
                    f"{_fmt_brl(self._values[i])}&nbsp;&nbsp;({pct:.1f}%)"
                )
                self._tooltip_label.adjustSize()
                # Converte coords matplotlib (origem: canto inferior esquerdo)
                # para coords Qt (origem: canto superior esquerdo)
                qt_x = int(event.x) + 12
                qt_y = int(self.height() - event.y) - self._tooltip_label.height() - 12
                # Limita para não sair dos limites do widget
                qt_x = min(qt_x, self.width()  - self._tooltip_label.width()  - 4)
                qt_y = max(qt_y, 4)
                self._tooltip_label.move(qt_x, qt_y)
                self._tooltip_label.raise_()
                self._tooltip_label.show()
                return
        self._tooltip_label.hide()


# ======================================================================
# Widgets de UI reutilizáveis
# ======================================================================

class SummaryCard(QFrame):
    """
    Card compacto com título, valor principal e subtítulo opcional.

    Recebe um ``accent`` (cor hex) que aparece como borda superior colorida
    de 3 px, igual ao estilo dos AccountCard da página Contas.
    """

    def __init__(
        self,
        title: str,
        default_color: str = "#E8EAED",
        accent: str | None = None,
        icon_name: str | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("summaryCard")
        self._default_color = default_color
        _accent = accent or default_color

        # Borda superior colorida (3 px) + fundo e borda lateral QSS
        self.setStyleSheet(f"""
            QFrame#summaryCard {{
                background-color: #222640;
                border: 1px solid #2E3250;
                border-top: 3px solid {_accent};
                border-radius: 14px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 18)
        layout.setSpacing(6)

        # Linha do título: ícone SVG (opcional) + texto
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        if icon_name:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(_svg_icon(icon_name, "#8B90A7", 14).pixmap(14, 14))
            icon_lbl.setStyleSheet("background: transparent;")
            title_row.addWidget(icon_lbl)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("cardTitle")
        title_row.addWidget(self._title_label)
        title_row.addStretch()
        layout.addLayout(title_row)

        self._value_label = QLabel("—")
        self._value_label.setObjectName("cardValue")
        self._value_label.setStyleSheet(f"color: {default_color};")

        self._sub_label = QLabel("")
        self._sub_label.setObjectName("cardSub")
        self._sub_label.setStyleSheet("color: #8B90A7; font-size: 11px;")
        self._sub_label.setVisible(False)

        layout.addWidget(self._value_label)
        layout.addWidget(self._sub_label)

    def set_value(self, value: str, color: str | None = None, sub: str = "") -> None:
        self._value_label.setText(value)
        self._value_label.setStyleSheet(f"color: {color or self._default_color};")
        self._sub_label.setText(sub)
        self._sub_label.setVisible(bool(sub))


class DebtProgressRow(QFrame):
    """
    Linha de progresso para uma dívida com cronograma de amortização expansível.
    """

    def __init__(self, debt: dict, client: "ApiClient | None" = None) -> None:
        super().__init__()
        self._debt_id        = debt.get("id")
        self._client         = client
        self._schedule_loaded = False
        self._show_all        = False
        self._full_schedule: list[dict] = []

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
        rate_lbl.setStyleSheet(f"color: {_RED}; font-size: 11px; background: transparent;")
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
                background: #FF4D4D;
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

        # Botão "Ver cronograma"
        self._schedule_btn = QPushButton("Ver cronograma ▾")
        self._schedule_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #4A9EFF;
                border: none;
                font-size: 11px;
                text-align: left;
                padding: 2px 0px;
            }
            QPushButton:hover { color: #7BBFFF; }
        """)
        self._schedule_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._schedule_btn.clicked.connect(self._toggle_schedule)

        # Contêiner do cronograma (oculto por padrão)
        self._schedule_container = QWidget()
        self._schedule_container.setVisible(False)
        sc_layout = QVBoxLayout(self._schedule_container)
        sc_layout.setContentsMargins(0, 4, 0, 0)
        sc_layout.setSpacing(4)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Nº", "Data", "Parcela", "Amortização", "Juros", "Saldo Devedor"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("""
            QTableWidget {
                background: #1E2130;
                alternate-background-color: #252840;
                color: #FFFFFF;
                border: none;
                font-size: 11px;
                gridline-color: #3A3D50;
            }
            QHeaderView::section {
                background: #2A2D3E;
                color: #8B90A7;
                font-size: 10px;
                border: none;
                padding: 4px;
            }
        """)

        self._show_all_btn = QPushButton("Ver todas as parcelas")
        self._show_all_btn.setStyleSheet("""
            QPushButton {
                background: #2A2D3E;
                color: #8B90A7;
                border: none;
                border-radius: 4px;
                font-size: 10px;
                padding: 4px 10px;
            }
            QPushButton:hover { color: #C8CAD8; background: #353850; }
        """)
        self._show_all_btn.clicked.connect(self._toggle_show_all)
        self._show_all_btn.setVisible(False)

        sc_layout.addWidget(self._table)
        sc_layout.addWidget(self._show_all_btn, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addLayout(header)
        layout.addWidget(bar)
        layout.addWidget(footer_lbl)
        layout.addWidget(self._schedule_btn)
        layout.addWidget(self._schedule_container)

    def _toggle_schedule(self) -> None:
        visible = not self._schedule_container.isVisible()
        if visible and not self._schedule_loaded:
            self._load_schedule()
        self._schedule_container.setVisible(visible)
        self._schedule_btn.setText("Ver cronograma ▴" if visible else "Ver cronograma ▾")

    def _load_schedule(self) -> None:
        if not self._client or not self._debt_id:
            return
        try:
            data = self._client.get_debt_schedule(self._debt_id)
            self._full_schedule = data.get("schedule", [])
            self._populate_table(self._full_schedule[:12])
            self._show_all_btn.setVisible(len(self._full_schedule) > 12)
            self._schedule_loaded = True
        except Exception:
            pass

    def _toggle_show_all(self) -> None:
        self._show_all = not self._show_all
        if self._show_all:
            self._populate_table(self._full_schedule)
            self._show_all_btn.setText("Mostrar menos")
        else:
            self._populate_table(self._full_schedule[:12])
            self._show_all_btn.setText("Ver todas as parcelas")

    def _populate_table(self, rows: list[dict]) -> None:
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            cells = [
                str(row.get("installment_number", "")),
                str(row.get("due_date", ""))[:10],
                _fmt_brl(float(row.get("installment_amount", 0))),
                _fmt_brl(float(row.get("principal", 0))),
                _fmt_brl(float(row.get("interest", 0))),
                _fmt_brl(float(row.get("remaining_balance", 0))),
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(i, j, item)
        row_h = 24
        self._table.setFixedHeight(min(len(rows), 12) * row_h + 30)


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
            "QFrame#categoryDonutCard { background: #222640; border-radius: 8px; }"
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

        # Donut — usa #222640 para coincidir com o fundo do card
        _cat_bg = (34/255, 38/255, 64/255)  # #222640
        canvas = DonutCanvas(figsize=(2.5, 2.2), bg_color=_cat_bg)
        canvas.setMinimumHeight(220)
        canvas.setMaximumHeight(260)
        canvas.setStyleSheet("background-color: #222640;")
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
            dot_lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")
            dot_lbl.setFixedWidth(14)
            ticker_lbl = QLabel(asset["ticker"])
            ticker_lbl.setStyleSheet(f"color: #FFFFFF; font-size: 11px; font-weight: 600; background: transparent;")
            val_lbl = QLabel(_fmt_brl(asset["value"]))
            val_lbl.setStyleSheet("color: #E0E2EA; font-size: 11px; background: transparent;")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hdr.addWidget(dot_lbl)
            hdr.addWidget(ticker_lbl)
            hdr.addStretch()
            hdr.addWidget(val_lbl)

            pct_lbl = QLabel(f"{pct:.1f}%")
            pct_lbl.setStyleSheet("color: #E0E2EA; font-size: 11px; background: transparent;")
            pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

            item_layout.addLayout(hdr)
            item_layout.addWidget(pct_lbl)
            legend_layout.addWidget(item)

        legend_layout.addStretch()
        row.addWidget(legend, stretch=50)
        outer.addLayout(row)


# ======================================================================
# Worker + Widget — Histórico de Alertas
# ======================================================================


class AlertHistoryWorker(QThread):
    """Busca o histórico de alertas em background (GET /dashboard/alert-history)."""

    data_ready     = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, limit: int = 30) -> None:
        super().__init__()
        self._client = client
        self._limit  = limit

    def run(self) -> None:
        try:
            data = self._client.get_alert_history(limit=self._limit)
            self.data_ready.emit(data)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class AlertHistoryRow(QFrame):
    """
    Linha do histórico de alertas — semelhante ao AlertRow mas com timestamp
    e estilo mais compacto para acomodar muitas entradas.
    """

    _PRIORITY_COLOR: dict[str, str] = {
        "alta":  "#FF6B6B",
        "media": "#FFB347",
        "baixa": "#4A9EFF",
    }

    def __init__(self, entry: dict) -> None:
        super().__init__()
        self.setObjectName("alertItem")

        priority  = str(entry.get("priority", "baixa")).lower()
        dot_color = self._PRIORITY_COLOR.get(priority, "#4A9EFF")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        dot = QLabel("●")
        dot.setFixedWidth(12)
        dot.setStyleSheet(f"color: {dot_color}; font-size: 10px;")
        dot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_label = QLabel(entry.get("title", ""))
        title_label.setStyleSheet("color: #E8EAED; font-size: 12px; font-weight: 600;")

        msg_label = QLabel(entry.get("message", ""))
        msg_label.setStyleSheet("color: #8B90A7; font-size: 11px;")
        msg_label.setWordWrap(True)

        text_col.addWidget(title_label)
        text_col.addWidget(msg_label)

        layout.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_col, stretch=1)

        # Timestamp (lado direito) — formato dd/mm HH:MM
        ts_raw = entry.get("triggered_at", "")
        ts_display = ""
        if ts_raw:
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(ts_raw.replace("Z", ""))
                ts_display = ts.strftime("%d/%m %H:%M")
            except Exception:
                ts_display = ts_raw[:16]

        ts_label = QLabel(ts_display)
        ts_label.setStyleSheet("color: #8B90A7; font-size: 10px;")
        ts_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        layout.addWidget(ts_label, alignment=Qt.AlignmentFlag.AlignTop)


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
        self._client      = ApiClient()
        self._worker:      DashboardWorker       | None = None
        self._pat_worker:  PatrimonyHistoryWorker | None = None
        self._hist_worker: AlertHistoryWorker     | None = None

        self._build_ui()
        app_signals.data_changed.connect(self.load_data)
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

        # ── Linha 1: 4 cards patrimoniais (ordem especificada) ─────────
        # 1. Patrimônio Total → COLOR_PATRIMONY (branco)
        # 2. Patrimônio D+0  → COLOR_BALANCE   (cinza claro)
        # 3. Reserva Emerg.  → condicional verde/laranja/vermelho
        # 4. Score de Saúde  → condicional verde/laranja/vermelho
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        self._card_patrimonio = SummaryCard("Patrimônio Total",      COLOR_PATRIMONY, accent=COLOR_INVESTMENT,  icon_name="trending_up")
        self._card_d0         = SummaryCard("Patrimônio D+0",        COLOR_BALANCE,   accent=COLOR_INVESTMENT,  icon_name="wallet")
        self._card_reserva    = SummaryCard("Reserva de Emergência", COLOR_ASSET,     accent=COLOR_ASSET,       icon_name="dollar")
        self._card_score      = SummaryCard("Score de Saúde",        COLOR_ASSET,     accent=COLOR_PENSION,     icon_name="check")
        for c in [self._card_patrimonio, self._card_d0, self._card_reserva, self._card_score]:
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            c.setVisible(False)
            row1.addWidget(c)
        self._content_layout.addLayout(row1)

        # ── Linha 2: 4 cards mensais ───────────────────────────────────
        # 5. Receitas      → COLOR_INCOME  (verde)
        # 6. Despesas      → COLOR_EXPENSE (vermelho)
        # 7. Investimentos → COLOR_INVESTMENT (azul) — novo
        # 8. Saldo         → condicional verde/vermelho
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        self._card_receitas     = SummaryCard("Receitas do Mês",      COLOR_INCOME,     accent=COLOR_INCOME,     icon_name="income")
        self._card_despesas     = SummaryCard("Despesas do Mês",      COLOR_EXPENSE,    accent=COLOR_EXPENSE,    icon_name="expense")
        self._card_investimentos = SummaryCard("Investimentos do Mês", COLOR_INVESTMENT, accent=COLOR_INVESTMENT, icon_name="trending_up")
        self._card_saldo        = SummaryCard("Saldo do Mês",         COLOR_ASSET,      accent=COLOR_ASSET,      icon_name="transfer")
        for c in [self._card_receitas, self._card_despesas, self._card_investimentos, self._card_saldo]:
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            c.setVisible(False)
            row2.addWidget(c)
        self._content_layout.addLayout(row2)

        # ── Custo Essencial — card separado abaixo da linha 2 ──────────
        self._card_essential = SummaryCard("Custo Essencial", COLOR_WARNING, accent=COLOR_WARNING, icon_name="card")
        self._card_essential.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._card_essential.setVisible(False)
        # Adicionado no final da lista mas não numa linha separada — reutilizado inline abaixo

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

        # ── Histórico de Alertas ───────────────────────────────────────
        self._hist_header = QHBoxLayout()
        self._hist_title = QLabel("Histórico de Alertas")
        self._hist_title.setObjectName("sectionTitle")
        self._hist_title.setVisible(False)
        self._hist_toggle_btn = QPushButton(" Mostrar")
        self._hist_toggle_btn.setIcon(_svg_icon("chevron_down", "#8B90A7", 12))
        self._hist_toggle_btn.setFixedWidth(110)
        self._hist_toggle_btn.setCheckable(True)
        self._hist_toggle_btn.setChecked(False)
        self._hist_toggle_btn.setVisible(False)
        self._hist_toggle_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #8B90A7; border: none; font-size: 11px; }"
            "QPushButton:hover { color: #C5CAE9; }"
        )
        self._hist_toggle_btn.clicked.connect(self._toggle_history)
        self._hist_header.addWidget(self._hist_title)
        self._hist_header.addStretch()
        self._hist_header.addWidget(self._hist_toggle_btn)
        self._content_layout.addLayout(self._hist_header)

        self._hist_area = QVBoxLayout()
        self._hist_area.setSpacing(6)
        self._hist_container = QWidget()
        self._hist_container.setLayout(self._hist_area)
        self._hist_container.setVisible(False)
        self._content_layout.addWidget(self._hist_container)

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

        self._worker = DashboardWorker(self._client)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

        self._pat_worker = PatrimonyHistoryWorker(self._client)
        self._pat_worker.patrimony_ready.connect(self._on_patrimony_ready)
        self._pat_worker.error_occurred.connect(self._on_patrimony_error)
        self._pat_worker.start()

        # Histórico de alertas — carrega em paralelo sem bloquear o dashboard
        if not (self._hist_worker and self._hist_worker.isRunning()):
            self._hist_worker = AlertHistoryWorker(self._client, limit=30)
            self._hist_worker.data_ready.connect(self._on_history_ready)
            self._hist_worker.start()

    # ------------------------------------------------------------------
    # Slots — DashboardWorker
    # ------------------------------------------------------------------

    def _on_data_ready(
        self,
        dashboard: dict,
        alerts: dict,
        emergency_fund: dict,
        debts: list,
        essential_cost: dict,
    ) -> None:
        self._loading_label.setVisible(False)
        self._set_content_visible(True)

        net_worth = dashboard.get("net_worth", {})
        monthly   = dashboard.get("monthly_summary", {})
        health    = dashboard.get("health_score", {})

        # ── Linha 1 ────────────────────────────────────────────────────

        # ── Linha 1 — Patrimônio ───────────────────────────────────────
        nw = float(net_worth.get("net_worth", 0))
        self._card_patrimonio.set_value(
            _fmt_brl(nw),
            color=COLOR_PATRIMONY if nw >= 0 else COLOR_EXPENSE,
            sub=f"Ativos: {_fmt_brl(float(net_worth.get('total_assets', 0)))}",
        )

        d0 = float(net_worth.get("account_balance", 0))
        self._card_d0.set_value(
            _fmt_brl(d0),
            color=COLOR_BALANCE,
            sub="Liquidez imediata (contas)",
        )

        ef_saldo = float(emergency_fund.get("saldo_total", 0))
        ef_meses = float(emergency_fund.get("meses_cobertos", 0))
        if ef_meses >= 6:
            ef_color, ef_status = COLOR_ASSET,   f"{ef_meses:.1f} meses cobertos ✓"
        elif ef_meses >= 3:
            ef_color, ef_status = COLOR_WARNING, f"{ef_meses:.1f} meses cobertos"
        else:
            ef_color, ef_status = COLOR_EXPENSE, f"{ef_meses:.1f} meses cobertos ⚠"
        self._card_reserva.set_value(_fmt_brl(ef_saldo), color=ef_color, sub=ef_status)

        score = int(health.get("total", 0))
        if score >= 70:
            score_color = COLOR_ASSET
        elif score >= 40:
            score_color = COLOR_WARNING
        else:
            score_color = COLOR_EXPENSE
        self._card_score.set_value(f"{score} / 100", color=score_color)

        # ── Linha 2 — Fluxo mensal ─────────────────────────────────────
        income  = float(monthly.get("income",  0))
        expense = float(monthly.get("expense", 0))
        balance = float(monthly.get("balance", 0))
        ref     = monthly.get("reference_month", "")
        savings_rate = float(monthly.get("savings_rate", 0))

        self._card_receitas.set_value(_fmt_brl(income), color=COLOR_INCOME, sub=ref)
        self._card_despesas.set_value(
            _fmt_brl(expense), color=COLOR_EXPENSE,
            sub=f"Taxa poupança: {savings_rate:.1f}%",
        )
        # Investimentos do mês: vem do monthly_summary se disponível, senão 0
        inv_month = float(monthly.get("investment", monthly.get("investments", 0)))
        self._card_investimentos.set_value(
            _fmt_brl(inv_month), color=COLOR_INVESTMENT,
            sub=ref,
        )
        self._card_saldo.set_value(
            _fmt_brl(balance),
            color=COLOR_ASSET if balance >= 0 else COLOR_EXPENSE,
            sub=ref,
        )

        # ── Custo essencial ────────────────────────────────────────────
        ec_avg = float(essential_cost.get("monthly_average", 0))
        self._card_essential.set_value(
            _fmt_brl(ec_avg),
            color=COLOR_INVESTMENT,
            sub="Média dos últimos 3 meses",
        )

        # ── Dívidas ────────────────────────────────────────────────────
        self._populate_debts(debts)

        # ── Alertas ────────────────────────────────────────────────────
        self._populate_alerts(alerts.get("alerts", []))

        # Garante que o scroll retorne ao topo após todos os widgets aparecerem
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(0))

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._loading_label.setVisible(True)
        self._set_content_visible(False)

    # ------------------------------------------------------------------
    # Slots — PatrimonyHistoryWorker
    # ------------------------------------------------------------------

    def _on_patrimony_ready(self, data: dict) -> None:
        monthly = data["monthly_bars"]
        yearly  = data["yearly_line"]
        distrib = data["distribution"]
        cat_donuts = data.get("category_donuts", {})

        has_data = (
            any(d.get("income", 0) + d.get("expense", 0) > 0.01 for d in monthly)
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
            self._card_patrimonio,
            self._card_d0,
            self._card_reserva,
            self._card_score,
            self._card_receitas,
            self._card_despesas,
            self._card_investimentos,
            self._card_saldo,
            self._card_essential,
        ]

    def _set_content_visible(self, visible: bool) -> None:
        for card in self._all_cards():
            card.setVisible(visible)
        self._charts_widget.setVisible(visible)
        self._alerts_title.setVisible(visible)
        self._debts_title.setVisible(visible)
        self._hist_title.setVisible(visible)
        self._hist_toggle_btn.setVisible(visible)

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
            self._debts_area.addWidget(DebtProgressRow(debt, client=self._client))

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

    def _on_history_ready(self, data: dict) -> None:
        """Chamado quando AlertHistoryWorker termina. Popula a seção de histórico."""
        self._populate_alert_history(data.get("history", []))

    def _populate_alert_history(self, history: list[dict]) -> None:
        """Preenche a área de histórico de alertas."""
        while self._hist_area.count():
            item = self._hist_area.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        if not history:
            empty = QLabel("Nenhum alerta registrado no histórico.")
            empty.setStyleSheet("color: #8B90A7; font-size: 12px; padding: 8px 0;")
            self._hist_area.addWidget(empty)
            return

        # Exibe os 30 mais recentes (já vêm ordenados do mais novo para o mais antigo)
        for entry in history:
            self._hist_area.addWidget(AlertHistoryRow(entry))

    def _toggle_history(self, checked: bool) -> None:
        """Mostra/oculta o container do histórico de alertas."""
        self._hist_container.setVisible(checked)
        if checked:
            self._hist_toggle_btn.setText(" Ocultar")
            self._hist_toggle_btn.setIcon(_svg_icon("chevron_up", "#8B90A7", 12))
        else:
            self._hist_toggle_btn.setText(" Mostrar")
            self._hist_toggle_btn.setIcon(_svg_icon("chevron_down", "#8B90A7", 12))

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
