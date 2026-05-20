"""
Página do Dashboard — visão geral do patrimônio e alertas ativos.

Layout:
  ┌───────────────────────────────────────────────────────┐
  │  [Patrimônio]  [Receitas]  [Despesas]  [Score Saúde]  │  ← cards de resumo
  ├───────────────────────────────────────────────────────┤
  │  ████████████ Gráfico de barras mensais ████████████  │  ← 280px, largura total
  ├──────────────────────────┬────────────────────────────┤
  │  Linha 10 anos (45%)     │  Donut + legenda top-5     │  ← 200px cada
  └──────────────────────────┴────────────────────────────┘
  │  Alertas Ativos                                       │
  └───────────────────────────────────────────────────────┘

Threading:
  DashboardWorker        → dashboard + alertas (cards e alertas)
  PatrimonyHistoryWorker → 10 anos de transações + portfolio + contas
                           (gráficos de evolução)
  Ambos são iniciados em paralelo no load_data.

Gráficos: matplotlib com backend Qt6Agg (sem OpenGL/QWebEngineView).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import matplotlib
matplotlib.use("qtagg")  # backend Qt sem OpenGL

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
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
# Constantes visuais — centralizadas para fácil customização
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
_COLOR_MAP   = dict(zip(_CATEGORIES, _CAT_COLORS))

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

_MONTH_ABBR = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
               "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


# ======================================================================
# Workers — execução HTTP em background
# ======================================================================


class DashboardWorker(QThread):
    """
    Busca dados de cards e alertas em thread separada.

    Qt só permite atualizar widgets na thread principal (a que criou o
    QApplication). Por isso, o worker apenas emite sinais com os dados
    brutos; os slots na DashboardPage atualizam os widgets.
    """

    data_ready     = pyqtSignal(dict, dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            dashboard = self._client.get_dashboard()
            alerts    = self._client.get_alerts()
            self.data_ready.emit(dashboard, alerts)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class PatrimonyHistoryWorker(QThread):
    """
    Busca e processa dados para os três gráficos de evolução patrimonial.
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
            current_nw = float(
                dashboard.get("net_worth", {}).get("net_worth", 0)
            )

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

            self.patrimony_ready.emit({
                "monthly_bars": _build_monthly_series(current_nw, transactions, 12),
                "yearly_line":  _build_yearly_series(current_nw,  transactions, 10),
                "distribution": _build_distribution(portfolio, accounts),
            })

        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro ao carregar gráficos: {exc}")


# ======================================================================
# Processamento de dados para os gráficos (funções puras, sem efeitos)
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


# ======================================================================
# Widgets de gráfico matplotlib
# ======================================================================


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Converte '#RRGGBB' para tupla (r, g, b) normalizada [0..1]."""
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

        x = np.arange(len(labels))
        bars = ax.bar(x, values, color=colors, width=0.6, zorder=3)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"R$ {v:,.0f}".replace(",", "."))
        )

        # Linha de zero visível
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
    Gráfico de donut — distribuição do patrimônio por categoria.
    Suporta hover: exibe anotação flutuante com nome, valor e %.
    """

    def __init__(self, parent=None) -> None:
        self._fig = Figure(figsize=(2.5, 2.0), facecolor=_BG_RGB)
        super().__init__(self._fig)
        self.setParent(parent)
        self.setMinimumHeight(195)
        self.setMaximumHeight(220)
        self._ax    = self._fig.add_subplot(111)
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

        # Anotação oculta — mostrada no hover
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
                label = (
                    f"{self._labels[i]}\n"
                    f"{_fmt_brl(self._values[i])}  ({pct:.1f}%)"
                )
                self._annot.set_text(label)
                self._annot.set_visible(True)
                self.draw_idle()
                return
        # Fora de qualquer fatia — esconde anotação
        if self._annot.get_visible():
            self._annot.set_visible(False)
            self.draw_idle()


# ======================================================================
# Componentes de UI reutilizáveis
# ======================================================================


class SummaryCard(QFrame):
    """Card compacto que exibe um único indicador financeiro."""

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

        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)
        layout.addWidget(self._sub_label)

    def set_value(self, value: str, color: str | None = None, sub: str = "") -> None:
        self._value_label.setText(value)
        used_color = color if color else self._default_color
        self._value_label.setStyleSheet(f"color: {used_color};")
        self._sub_label.setText(sub)
        self._sub_label.setVisible(bool(sub))


class AlertRow(QFrame):
    """
    Linha de alerta com indicador colorido de prioridade.

    Prioridade → cor do marcador:
      ALTA  → #FF6B6B (vermelho)
      MÉDIA → #FFB347 (laranja)
      BAIXA → #4A9EFF (azul)
    """

    _PRIORITY_COLOR: dict[str, str] = {
        "ALTA": "#FF6B6B",
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


# ======================================================================
# Página principal do Dashboard
# ======================================================================


class DashboardPage(QWidget):
    """
    Dashboard completo com cards de resumo, gráficos de evolução
    patrimonial e lista de alertas.
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
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

        scroll = QScrollArea()
        scroll.setObjectName("dashboardScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("dashboardContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(32, 28, 32, 32)
        self._content_layout.setSpacing(24)

        self._loading_label = QLabel("Carregando dados do dashboard…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_layout.addWidget(self._loading_label)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)

        self._card_patrimonio = SummaryCard("Patrimônio Líquido", "#4A9EFF")
        self._card_receitas   = SummaryCard("Receitas do Mês",    "#00C896")
        self._card_despesas   = SummaryCard("Despesas do Mês",    "#FF6B6B")
        self._card_score      = SummaryCard("Score de Saúde",     "#00C896")

        for card in self._cards():
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.setVisible(False)
            cards_row.addWidget(card)

        self._content_layout.addLayout(cards_row)

        self._charts_widget = self._build_charts_area()
        self._charts_widget.setVisible(False)
        self._content_layout.addWidget(self._charts_widget)

        self._alerts_title = QLabel("Alertas Ativos")
        self._alerts_title.setObjectName("sectionTitle")
        self._alerts_title.setVisible(False)
        self._content_layout.addWidget(self._alerts_title)

        self._alerts_area = QVBoxLayout()
        self._alerts_area.setSpacing(8)
        self._content_layout.addLayout(self._alerts_area)

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
        """
        Monta o container com os três gráficos matplotlib:
          Linha 1: barras mensais (largura total)
          Linha 2: linha anual (45%) | donut + legenda top-5 (55%)
        """
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

        # Gráfico 1: barras mensais
        self._bars_canvas = BarsCanvas()
        vbox.addWidget(self._bars_canvas)

        # Linha 2: linha anual + donut
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
    # Slots — DashboardWorker (cards + alertas)
    # ------------------------------------------------------------------

    def _on_data_ready(self, dashboard: dict, alerts: dict) -> None:
        self._loading_label.setVisible(False)
        self._set_content_visible(True)

        net_worth = dashboard.get("net_worth", {})
        monthly   = dashboard.get("monthly_summary", {})
        health    = dashboard.get("health_score", {})

        nw = float(net_worth.get("net_worth", 0))
        nw_color = "#4A9EFF" if nw >= 0 else "#FF6B6B"
        self._card_patrimonio.set_value(
            _fmt_brl(nw),
            color=nw_color,
            sub=f"Ativos: {_fmt_brl(float(net_worth.get('total_assets', 0)))}",
        )

        self._card_receitas.set_value(
            _fmt_brl(float(monthly.get("income", 0))),
            color="#00C896",
            sub=monthly.get("reference_month", ""),
        )

        self._card_despesas.set_value(
            _fmt_brl(float(monthly.get("expense", 0))),
            color="#FF6B6B",
            sub=f"Taxa poupança: {float(monthly.get('savings_rate', 0)):.1f}%",
        )

        score = int(health.get("total", 0))
        score_color = "#00C896" if score >= 60 else ("#FFB347" if score >= 40 else "#FF6B6B")
        self._card_score.set_value(f"{score} / 100", color=score_color)

        self._populate_alerts(alerts.get("alerts", []))
        self._reload_btn.setVisible(True)

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._loading_label.setVisible(True)
        self._set_content_visible(False)
        self._reload_btn.setVisible(True)

    # ------------------------------------------------------------------
    # Slots — PatrimonyHistoryWorker (gráficos)
    # ------------------------------------------------------------------

    def _on_patrimony_ready(self, data: dict) -> None:
        monthly = data["monthly_bars"]
        yearly  = data["yearly_line"]
        distrib = data["distribution"]

        has_data = (
            any(abs(d["value"]) > 0.01 for d in monthly)
            or bool(distrib)
        )

        if not has_data:
            self._charts_empty_msg.setText(
                "Adicione lançamentos para ver a evolução patrimonial"
            )
            self._charts_empty_msg.setVisible(True)
            self._bars_canvas.setVisible(False)
            self._row2_widget.setVisible(False)
            return

        self._charts_empty_msg.setVisible(False)
        self._bars_canvas.setVisible(True)
        self._row2_widget.setVisible(True)

        self._bars_canvas.update_data(monthly)
        self._line_canvas.update_data(yearly)
        self._donut_canvas.update_data(distrib)
        self._populate_legend(distrib[:5], sum(d["value"] for d in distrib))

    def _on_patrimony_error(self, message: str) -> None:
        self._charts_empty_msg.setText(f"Erro ao carregar gráficos: {message}")
        self._charts_empty_msg.setVisible(True)
        self._bars_canvas.setVisible(False)
        self._row2_widget.setVisible(False)

    # ------------------------------------------------------------------
    # Helpers de UI
    # ------------------------------------------------------------------

    def _cards(self) -> list[SummaryCard]:
        return [
            self._card_patrimonio,
            self._card_receitas,
            self._card_despesas,
            self._card_score,
        ]

    def _set_content_visible(self, visible: bool) -> None:
        for card in self._cards():
            card.setVisible(visible)
        self._charts_widget.setVisible(visible)
        self._alerts_title.setVisible(visible)

    def _populate_alerts(self, alerts: list[dict]) -> None:
        while self._alerts_area.count():
            item = self._alerts_area.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

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
            val_lbl.setStyleSheet(
                f"color: {_TEXT}; font-size: 10px; background: transparent;"
            )
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
