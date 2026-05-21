"""
Página de IR e DARF — controle de imposto de renda sobre renda variável.

Layout:
  ┌────────────────────────────────────────────────────────────┐
  │  Ano: [2026]  [↻ Atualizar]                [Card: prejuízo]│
  ├────────────────────────────────────────────────────────────┤
  │  Mês │ Vendas │ Lucro/Prej. │ IR Devido │ Status           │
  │  Jan │   0,00 │        0,00 │      0,00 │ [Isento]         │
  │  ...                                                       │
  ├────────────────────────────────────────────────────────────┤
  │  Informativo sobre as regras de IR                         │
  └────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from datetime import date
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError


# ======================================================================
# Workers
# ======================================================================

class TaxWorker(QThread):
    """Busca o resumo anual de IR em background."""

    data_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, year: int) -> None:
        super().__init__()
        self._client = client
        self._year = year

    def run(self) -> None:
        try:
            data = self._client.get_tax_annual_summary(self._year)
            self.data_ready.emit(data)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Colunas da tabela
# ======================================================================

_COL_MONTH  = 0
_COL_SALES  = 1
_COL_PROFIT = 2
_COL_TAX    = 3
_COL_STATUS = 4

_STATUS_COLORS = {
    "isento":   ("#4A9EFF", "Isento"),
    "pendente": ("#FF6B6B", "DARF Pendente"),
    "pago":     ("#00C896", "Pago"),
}

_C_WHITE = "#E8EAED"
_C_MUTED = "#8B90A7"
_C_POS   = "#00C896"
_C_NEG   = "#FF6B6B"


def _fmt_brl(value: float) -> str:
    try:
        f = abs(value)
        s = f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"-R$ {s}" if value < 0 else f"R$ {s}"
    except (TypeError, ValueError):
        return "—"


# ======================================================================
# Página principal
# ======================================================================

class TaxPage(QWidget):
    """Página de controle de IR sobre renda variável."""

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: TaxWorker | None = None
        self._current_summary: dict = {}
        self._paid_months: set[int] = set()  # meses marcados como pagos (em memória)
        self._build_ui()
        self.load_data()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("dashboardContent")
        main = QVBoxLayout(content)
        main.setContentsMargins(32, 24, 32, 32)
        main.setSpacing(20)

        # --- Cabeçalho com seletor de ano ---
        hdr = QHBoxLayout()
        hdr.setSpacing(12)

        year_lbl = QLabel("Ano:")
        year_lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 13px;")
        hdr.addWidget(year_lbl)

        self._year_combo = QComboBox()
        current_year = date.today().year
        for y in range(current_year, current_year - 6, -1):
            self._year_combo.addItem(str(y), userData=y)
        self._year_combo.currentIndexChanged.connect(self.load_data)
        hdr.addWidget(self._year_combo)

        refresh_btn = QPushButton("↻ Atualizar")
        refresh_btn.setProperty("class", "primary")
        refresh_btn.clicked.connect(self.load_data)
        hdr.addWidget(refresh_btn)

        hdr.addStretch()

        # Card de prejuízo acumulado
        self._card_loss = _InfoCard("Prejuízo Acumulado a Compensar", "—", "#FFB347")
        self._card_loss.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._card_loss.setMinimumWidth(260)
        hdr.addWidget(self._card_loss)

        main.addLayout(hdr)

        # --- Cards de totais anuais ---
        totals_row = QHBoxLayout()
        totals_row.setSpacing(16)
        self._card_total_sales = _InfoCard("Total de Vendas no Ano", "—", _C_WHITE)
        self._card_total_profit = _InfoCard("Lucro/Prejuízo no Ano", "—", _C_WHITE)
        self._card_total_tax = _InfoCard("IR Total Devido no Ano", "—", _C_NEG)
        for card in [self._card_total_sales, self._card_total_profit, self._card_total_tax]:
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            totals_row.addWidget(card)
        main.addLayout(totals_row)

        # --- Loading ---
        self._loading_lbl = QLabel("Calculando IR…")
        self._loading_lbl.setObjectName("loadingLabel")
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._loading_lbl)

        # --- Tabela mensal ---
        self._table = self._build_table()
        self._table.setVisible(False)
        main.addWidget(self._table)

        # --- Seção informativa ---
        main.addWidget(self._build_info_section())

        main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_table(self) -> QTableWidget:
        headers = ["Mês", "Vendas no Mês", "Lucro / Prejuízo", "IR Devido", "Status"]
        table = QTableWidget(12, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_STATUS, QHeaderView.ResizeMode.Stretch)
        return table

    def _build_info_section(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("summaryCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(8)

        title = QLabel("Como funciona o IR sobre Renda Variável")
        title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {_C_WHITE};")
        layout.addWidget(title)

        rules = [
            "Isenção: vendas de AÇÕES abaixo de R$ 20.000 no mês estão isentas de IR.",
            "Alíquota: 15% sobre o lucro líquido em operações de Swing Trade.",
            "Day Trade: alíquota de 20% — não diferenciado nesta versão.",
            "Compensação: prejuízos de meses anteriores podem ser abatidos do lucro futuro.",
            "FIIs: rendimentos mensais são isentos; ganho de capital na venda é tributado (20%).",
            "DARF: código 6015, vencimento no último dia útil do mês seguinte ao da apuração.",
            "Responsabilidade: o investidor deve emitir e pagar o DARF mensalmente quando devido.",
        ]
        for rule in rules:
            lbl = QLabel(f"• {rule}")
            lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        return frame

    # ------------------------------------------------------------------
    # Carregamento
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        year = self._year_combo.currentData()
        self._table.setVisible(False)
        self._loading_lbl.setText("Calculando IR…")
        self._loading_lbl.setVisible(True)

        self._worker = TaxWorker(self._client, year)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_data_ready(self, summary: dict) -> None:
        self._current_summary = summary
        self._loading_lbl.setVisible(False)
        self._table.setVisible(True)

        # Cards anuais
        self._card_total_sales.set_value(
            _fmt_brl(float(summary.get("total_sales", 0)))
        )
        profit = float(summary.get("total_profit", 0))
        self._card_total_profit.set_value(
            _fmt_brl(profit),
            color=_C_POS if profit >= 0 else _C_NEG,
        )
        total_tax = float(summary.get("total_tax_due", 0))
        self._card_total_tax.set_value(
            _fmt_brl(total_tax),
            color=_C_NEG if total_tax > 0 else _C_MUTED,
        )

        remaining_loss = float(summary.get("remaining_loss", 0))
        self._card_loss.set_value(
            _fmt_brl(remaining_loss) if remaining_loss > 0 else "Nenhum",
        )

        # Tabela
        months = summary.get("months", [])
        self._table.setRowCount(len(months))

        year = summary.get("year", date.today().year)
        today_month = date.today().month if date.today().year == year else 12

        for row, m in enumerate(months):
            month_num = m["month"]
            status_key = "pago" if month_num in self._paid_months else m.get("status", "isento")
            status_color, status_label = _STATUS_COLORS.get(status_key, (_C_MUTED, status_key))
            profit_val = float(m.get("total_profit", 0))
            tax_due = float(m.get("tax_due", 0))

            # Meses futuros ficam com células vazias
            is_future = month_num > today_month
            sales_str  = "—" if is_future else _fmt_brl(float(m.get("total_sales", 0)))
            profit_str = "—" if is_future else _fmt_brl(profit_val)
            tax_str    = "—" if is_future else _fmt_brl(tax_due)

            cells = [
                (m.get("month_label", ""), _C_WHITE, Qt.AlignmentFlag.AlignLeft),
                (sales_str,  _C_WHITE, Qt.AlignmentFlag.AlignRight),
                (profit_str, _C_POS if profit_val >= 0 else _C_NEG, Qt.AlignmentFlag.AlignRight),
                (tax_str,    _C_NEG if tax_due > 0 else _C_MUTED, Qt.AlignmentFlag.AlignRight),
            ]

            for col, (text, color, align) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, col, item)

            # Coluna de status com badge colorido
            if is_future:
                status_widget = QLabel("—")
                status_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
                status_widget.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
            else:
                status_widget = self._build_status_badge(
                    month_num, status_label, status_color, tax_due, status_key
                )
            self._table.setCellWidget(row, _COL_STATUS, status_widget)

    def _build_status_badge(
        self,
        month_num: int,
        status_label: str,
        color: str,
        tax_due: float,
        status_key: str,
    ) -> QWidget:
        container = QWidget()
        row_layout = QHBoxLayout(container)
        row_layout.setContentsMargins(8, 2, 8, 2)
        row_layout.setSpacing(8)

        badge = QLabel(status_label)
        badge.setStyleSheet(
            f"background: {color}22; color: {color}; font-size: 11px; "
            f"font-weight: 600; border-radius: 4px; padding: 2px 8px;"
        )
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedHeight(22)
        row_layout.addWidget(badge)

        # Botão "Marcar como Pago" apenas para DARF pendente
        if status_key == "pendente" and tax_due > 0:
            pay_btn = QPushButton("Marcar como Pago")
            pay_btn.setFixedHeight(22)
            pay_btn.setStyleSheet(
                "font-size: 10px; padding: 0 8px; "
                "background: #00C89622; color: #00C896; border: 1px solid #00C896; border-radius: 4px;"
            )
            pay_btn.clicked.connect(lambda _, mn=month_num: self._mark_as_paid(mn))
            row_layout.addWidget(pay_btn)

        row_layout.addStretch()
        return container

    def _mark_as_paid(self, month_num: int) -> None:
        self._paid_months.add(month_num)
        # Re-renderiza a tabela com o mês atualizado para "pago"
        if self._current_summary:
            self._on_data_ready(self._current_summary)

    def _on_error(self, message: str) -> None:
        self._loading_lbl.setText(f"Erro: {message}")
        self._table.setVisible(False)


# ======================================================================
# Componentes auxiliares
# ======================================================================

class _InfoCard(QFrame):
    def __init__(self, title: str, value: str, color: str = _C_WHITE) -> None:
        super().__init__()
        self.setObjectName("summaryCard")
        self._color = color
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(4)

        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("cardTitle")
        layout.addWidget(self._title_lbl)

        self._val_lbl = QLabel(value)
        self._val_lbl.setObjectName("cardValue")
        self._val_lbl.setStyleSheet(f"color: {color};")
        layout.addWidget(self._val_lbl)

    def set_value(self, value: str, color: str | None = None) -> None:
        self._val_lbl.setText(value)
        c = color or self._color
        self._val_lbl.setStyleSheet(f"color: {c};")
