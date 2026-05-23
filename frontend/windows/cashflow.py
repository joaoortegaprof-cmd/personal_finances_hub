"""
FinanceHub — Fluxo de Caixa
============================

Página de projeção de fluxo de caixa: receitas, despesas e saldo acumulado
projetados para as próximas semanas ou meses com base em recorrências e
histórico de transações.

Layout:
  ┌─────────────────────────────────────────────────────────────────┐
  │  Fluxo de Caixa          Modo:[Semanal▼]  [3m▼]  [↻ Atualizar] │
  ├─────────────────────────────────────────────────────────────────┤
  │  [Card Receitas]   [Card Despesas]   [Card Saldo Projetado]     │
  ├─────────────────────────────────────────────────────────────────┤
  │              Gráfico barras agrupadas + linha saldo             │
  ├─────────────────────────────────────────────────────────────────┤
  │  Eventos previstos                                              │
  │  Data  │  Descrição  │  Valor  │  Tipo  │  Confirmado           │
  └─────────────────────────────────────────────────────────────────┘

Endpoint: GET /cashflow/projection?mode={weekly|monthly}&months={1|3|6}
"""

from __future__ import annotations

from typing import Any

import matplotlib
matplotlib.use("qtagg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.colors import (
    COLOR_ASSET       as _GREEN,
    COLOR_EXPENSE     as _RED,
    COLOR_INVESTMENT  as _BLUE,
    COLOR_NEUTRAL     as _WHITE,
    COLOR_MUTED       as _MUTED,
    COLOR_BG, COLOR_SURFACE, COLOR_BORDER, COLOR_GRID,
    COLOR_ASSET_RGB, COLOR_EXPENSE_RGB, COLOR_INVESTMENT_RGB,
    hex_to_rgb,
)


# ======================================================================
# Helpers
# ======================================================================

def _fmt_brl(value: float) -> str:
    """Formata como moeda brasileira: R$ 1.234,56"""
    try:
        formatted = f"{abs(value):,.2f}"
        formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
        prefix = "-R$ " if value < 0 else "R$ "
        return f"{prefix}{formatted}"
    except (TypeError, ValueError):
        return "R$ 0,00"


def _short_label(label: str) -> str:
    """Encurta labels de período para o eixo X do gráfico."""
    # "Sem 21/2026" → "S21"   |   "Mai/2026" → "Mai"
    if label.startswith("Sem "):
        parts = label.split()  # ["Sem", "21/2026"]
        num = parts[1].split("/")[0]
        return f"S{num}"
    return label.split("/")[0]  # "Mai/2026" → "Mai"


_BALANCE_RGB = hex_to_rgb("#FFFFFF")  # branco para a linha de saldo
_CONFIRMED_GREEN = "#00C896"
_UNCONFIRMED_MUTED = "#8B90A7"


# ======================================================================
# Worker
# ======================================================================

class CashflowWorker(QThread):
    """Busca projeção de fluxo de caixa em background."""

    data_ready     = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, mode: str, months: int) -> None:
        super().__init__()
        self._client = client
        self._mode   = mode
        self._months = months

    def run(self) -> None:
        try:
            data = self._client.get_cashflow_projection(self._mode, self._months)
            self.data_ready.emit(data)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Canvas matplotlib
# ======================================================================

class CashflowCanvas(FigureCanvasQTAgg):
    """
    Gráfico de fluxo de caixa:
      - Barras agrupadas: receitas (verde) e despesas (vermelho)
      - Linha tracejada: saldo acumulado (branco)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        bg = COLOR_BG
        self._fig, self._ax = plt.subplots(figsize=(10, 4))
        self._fig.patch.set_facecolor(bg)
        self._ax.set_facecolor(COLOR_GRID)
        super().__init__(self._fig)
        if parent:
            self.setParent(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._draw_empty()

    def _draw_empty(self) -> None:
        self._ax.clear()
        self._ax.set_facecolor(COLOR_GRID)
        self._ax.tick_params(colors="#8B90A7", labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_edgecolor("#2E3250")
        self._ax.text(
            0.5, 0.5, "Carregando projeção…",
            transform=self._ax.transAxes,
            ha="center", va="center",
            color="#8B90A7", fontsize=12,
        )
        self.draw()

    def update_data(self, periods: list[dict]) -> None:
        """Atualiza o gráfico com os períodos de projeção."""
        if not periods:
            self._draw_empty()
            return

        self._ax.clear()
        self._ax.set_facecolor(COLOR_GRID)

        labels  = [_short_label(p["label"]) for p in periods]
        incomes  = [float(p.get("income", 0))         for p in periods]
        expenses = [float(p.get("expenses", p.get("expense", 0))) for p in periods]
        balances = [float(p.get("running_balance", 0)) for p in periods]

        n = len(labels)
        xs = list(range(n))
        w  = 0.35

        # Barras agrupadas
        self._ax.bar(
            [x - w / 2 for x in xs], incomes, w,
            color=COLOR_ASSET_RGB, alpha=0.85, label="Receitas",
        )
        self._ax.bar(
            [x + w / 2 for x in xs], expenses, w,
            color=COLOR_EXPENSE_RGB, alpha=0.85, label="Despesas",
        )

        # Linha de saldo acumulado (eixo secundário)
        ax2 = self._ax.twinx()
        ax2.set_facecolor(COLOR_GRID)
        ax2.plot(
            xs, balances,
            color=(1.0, 1.0, 1.0, 0.8),
            linestyle="--", linewidth=1.5,
            marker="o", markersize=3,
            label="Saldo acum.",
        )
        ax2.tick_params(colors="#8B90A7", labelsize=7)
        ax2.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"R$ {v/1000:.0f}k" if abs(v) >= 1000 else f"R$ {v:.0f}")
        )
        for spine in ax2.spines.values():
            spine.set_edgecolor("#2E3250")

        # Estilo eixo primário
        self._ax.set_xticks(xs)
        self._ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8, color="#C8CAD8")
        self._ax.tick_params(colors="#8B90A7", labelsize=8)
        self._ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"R$ {v/1000:.0f}k" if abs(v) >= 1000 else f"R$ {v:.0f}")
        )
        self._ax.set_ylabel("Receitas / Despesas", color="#8B90A7", fontsize=8)
        ax2.set_ylabel("Saldo acumulado", color="#8B90A7", fontsize=8)
        for spine in self._ax.spines.values():
            spine.set_edgecolor("#2E3250")
        self._ax.grid(axis="y", color="#2E3250", linewidth=0.5, alpha=0.6)

        # Legenda combinada
        h1, l1 = self._ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        self._ax.legend(
            h1 + h2, l1 + l2,
            loc="upper left", fontsize=8,
            facecolor="#222640", edgecolor="#2E3250",
            labelcolor="#C8CAD8", ncol=3,
        )

        self._fig.tight_layout(pad=0.8)
        self.draw()


# ======================================================================
# Página principal
# ======================================================================

class CashflowPage(QWidget):
    """
    Página de Fluxo de Caixa — projeção semanal ou mensal de receitas,
    despesas e saldo acumulado.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client  = ApiClient()
        self._worker: CashflowWorker | None = None
        self._data:   dict = {}

        self._build_ui()
        self._load_data()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        # Scroll area para a página inteira
        scroll = QScrollArea()
        scroll.setObjectName("dashboardScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        content = QWidget()
        content.setObjectName("dashboardContent")
        scroll.setWidget(content)

        main = QVBoxLayout(content)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(20)

        # ---- Cabeçalho com controles ----
        main.addLayout(self._build_header())

        # ---- Cards de resumo ----
        main.addLayout(self._build_summary_cards())

        # ---- Gráfico ----
        self._canvas = CashflowCanvas()
        self._canvas.setMinimumHeight(300)
        main.addWidget(self._canvas)

        # ---- Tabela de eventos ----
        main.addWidget(self._build_events_section())

        main.addStretch()

    def _build_header(self) -> QHBoxLayout:
        hdr = QHBoxLayout()
        hdr.setSpacing(12)

        title = QLabel("Fluxo de Caixa")
        title.setObjectName("page-title")
        hdr.addWidget(title)
        hdr.addStretch()

        # ComboBox — Modo
        mode_label = QLabel("Modo:")
        mode_label.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        hdr.addWidget(mode_label)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Semanal", "weekly")
        self._mode_combo.addItem("Mensal", "monthly")
        self._mode_combo.setMinimumWidth(110)
        self._mode_combo.currentIndexChanged.connect(self._load_data)
        hdr.addWidget(self._mode_combo)

        # ComboBox — Horizonte
        horizon_label = QLabel("Horizonte:")
        horizon_label.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        hdr.addWidget(horizon_label)

        self._months_combo = QComboBox()
        self._months_combo.addItem("1 mês",  1)
        self._months_combo.addItem("3 meses", 3)
        self._months_combo.addItem("6 meses", 6)
        self._months_combo.setCurrentIndex(1)  # 3 meses como padrão
        self._months_combo.setMinimumWidth(100)
        self._months_combo.currentIndexChanged.connect(self._load_data)
        hdr.addWidget(self._months_combo)

        # Botão atualizar
        refresh_btn = QPushButton("↻  Atualizar")
        refresh_btn.clicked.connect(self._load_data)
        hdr.addWidget(refresh_btn)

        return hdr

    def _build_summary_cards(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)

        # Receitas projetadas
        self._card_income = self._make_card(
            "RECEITAS PROJETADAS", "R$ 0,00", _GREEN
        )

        # Despesas projetadas
        self._card_expense = self._make_card(
            "DESPESAS PROJETADAS", "R$ 0,00", _RED
        )

        # Saldo projetado
        self._card_balance = self._make_card(
            "SALDO PROJETADO", "R$ 0,00", _WHITE
        )

        for card, _ in [self._card_income, self._card_expense, self._card_balance]:
            row.addWidget(card)

        return row

    def _make_card(
        self,
        title: str,
        value: str,
        value_color: str,
    ) -> tuple[QFrame, QLabel]:
        """Cria um card de resumo. Retorna (frame, value_label)."""
        card = QFrame()
        card.setObjectName("summaryCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setMinimumHeight(90)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("cardTitle")
        lay.addWidget(title_lbl)

        val_lbl = QLabel(value)
        val_lbl.setObjectName("cardValue")
        val_lbl.setStyleSheet(f"color: {value_color};")
        lay.addWidget(val_lbl)

        return card, val_lbl

    def _build_events_section(self) -> QWidget:
        section = QWidget()
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        title = QLabel("Eventos previstos")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)

        self._events_table = QTableWidget()
        self._events_table.setObjectName("cashflowEventsTable")
        self._events_table.setColumnCount(5)
        self._events_table.setHorizontalHeaderLabels(
            ["Data", "Descrição", "Valor", "Tipo", "Confirmado"]
        )
        self._events_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._events_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._events_table.setAlternatingRowColors(True)
        self._events_table.horizontalHeader().setStretchLastSection(False)
        hh = self._events_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Data
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)            # Descrição
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Valor
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Tipo
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Confirmado
        self._events_table.verticalHeader().setVisible(False)
        self._events_table.setMinimumHeight(200)
        lay.addWidget(self._events_table)

        return section

    # ------------------------------------------------------------------
    # Carregamento de dados
    # ------------------------------------------------------------------

    def _load_data(self) -> None:
        """Dispara o worker para buscar a projeção de fluxo de caixa."""
        if self._worker and self._worker.isRunning():
            return

        mode   = self._mode_combo.currentData()
        months = self._months_combo.currentData()

        self._worker = CashflowWorker(self._client, mode, months)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_data_ready(self, data: dict) -> None:
        self._data = data
        self._update_cards(data)
        self._canvas.update_data(data.get("periods", []))
        self._populate_events(data.get("events", []))

    def _on_error(self, msg: str) -> None:
        # Mostra erro sutil no primeiro card
        _, val_lbl = self._card_income
        val_lbl.setText("Erro ao carregar")
        val_lbl.setStyleSheet(f"color: {_RED};")

    # ------------------------------------------------------------------
    # Atualização de componentes
    # ------------------------------------------------------------------

    def _update_cards(self, data: dict) -> None:
        total_income  = float(data.get("total_income",  0))
        total_expense = float(data.get("total_expenses", data.get("total_expense", 0)))
        balance       = total_income - total_expense

        _, income_lbl = self._card_income
        income_lbl.setText(_fmt_brl(total_income))
        income_lbl.setStyleSheet(f"color: {_GREEN};")

        _, expense_lbl = self._card_expense
        expense_lbl.setText(_fmt_brl(total_expense))
        expense_lbl.setStyleSheet(f"color: {_RED};")

        _, balance_lbl = self._card_balance
        balance_lbl.setText(_fmt_brl(balance))
        color = _GREEN if balance >= 0 else _RED
        balance_lbl.setStyleSheet(f"color: {color};")

    def _populate_events(self, events: list[dict]) -> None:
        tbl = self._events_table
        tbl.setRowCount(0)
        tbl.setRowCount(len(events))

        _TYPE_LABEL: dict[str, tuple[str, str]] = {
            "income":  ("Receita",      _GREEN),
            "expense": ("Despesa",      _RED),
            "debt":    ("Dívida",       _RED),
            "invoice": ("Fatura",       _MUTED),
            "debit":   ("Débito",       _RED),
        }

        for row, ev in enumerate(events):
            raw_date = ev.get("date", "")
            # "2026-06-05" → "05/06/2026"
            try:
                y, m, d = raw_date.split("-")
                date_str = f"{d}/{m}/{y}"
            except Exception:
                date_str = raw_date

            description = ev.get("description", "—")
            amount      = float(ev.get("amount", 0))
            ev_type     = ev.get("type", "")
            confirmed   = ev.get("is_confirmed", False)

            label_text, color = _TYPE_LABEL.get(ev_type, (ev_type.capitalize(), _WHITE))

            # Colunas
            def _cell(text: str, align=Qt.AlignmentFlag.AlignVCenter) -> QTableWidgetItem:
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignLeft)
                return item

            tbl.setItem(row, 0, _cell(date_str))
            tbl.setItem(row, 1, _cell(description))

            # Valor com cor semântica
            val_item = QTableWidgetItem(_fmt_brl(amount))
            val_item.setFlags(val_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            val_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
            )
            val_item.setForeground(
                __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(color)
            )
            tbl.setItem(row, 2, val_item)

            # Tipo
            type_item = QTableWidgetItem(label_text)
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            type_item.setForeground(
                __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(color)
            )
            tbl.setItem(row, 3, type_item)

            # Confirmado
            confirmed_text = "✓ Confirmado" if confirmed else "Estimado"
            conf_color = _CONFIRMED_GREEN if confirmed else _UNCONFIRMED_MUTED
            conf_item = QTableWidgetItem(confirmed_text)
            conf_item.setFlags(conf_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            conf_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            conf_item.setForeground(
                __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(conf_color)
            )
            tbl.setItem(row, 4, conf_item)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: ANN001
        """Recarrega ao tornar-se visível."""
        super().showEvent(event)
        self._load_data()
