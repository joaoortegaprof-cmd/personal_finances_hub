"""
Página de Simulações Financeiras — FinanceHub.

4 abas:
  1. Aposentadoria / FIRE  — projeção patrimonial com gráficos
  2. Objetivos Financeiros — metas com progresso e cartões visuais
  3. Quitação de Dívidas   — comparação normal vs acelerada
  4. Calculadoras Rápidas  — juros compostos, CDI, inflação, aporte
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("qtagg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.icons import icon as _svg_icon

# -----------------------------------------------------------------
# Constantes visuais
# -----------------------------------------------------------------

_BG      = "#1A1D2E"
_BG_CARD = "#222640"
_TEXT    = "#C8CAD8"
_GREEN   = "#00C896"
_RED     = "#FF6B6B"
_BLUE    = "#4A9EFF"
_ORANGE  = "#FFB347"
_PURPLE  = "#A78BFA"
_GRID    = "#2A2D3E"

_BG_RGB   = tuple(int(_BG.lstrip("#")[i:i+2], 16) / 255 for i in (0, 2, 4))
_TEXT_RGB = tuple(int(_TEXT.lstrip("#")[i:i+2], 16) / 255 for i in (0, 2, 4))
_GRID_RGB = tuple(int(_GRID.lstrip("#")[i:i+2], 16) / 255 for i in (0, 2, 4))

_GOALS_FILE = Path(__file__).parent.parent.parent / "data" / "goals.json"

_GOAL_ICONS = {
    "imóvel": "🏠", "viagem": "✈️", "educação": "🎓", "carro": "🚗",
    "emergência": "🛡️", "casamento": "💍", "negócio": "💼",
}


# -----------------------------------------------------------------
# Workers
# -----------------------------------------------------------------

class RetirementWorker(QThread):
    done           = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict) -> None:
        super().__init__()
        self._client  = client
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client._post("/simulation/retirement", self._payload)
            self.done.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro: {exc}")


class FireWorker(QThread):
    done           = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            result = self._client._get("/simulation/fire")
            self.done.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro: {exc}")


class DebtPayoffWorker(QThread):
    done           = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict) -> None:
        super().__init__()
        self._client  = client
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client._post("/simulation/debt-payoff", self._payload)
            self.done.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro: {exc}")


class DashboardFetchWorker(QThread):
    """Busca patrimônio atual e média de investimentos para pré-preencher campos."""
    done           = pyqtSignal(float, float)  # (net_worth, avg_investment)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            dashboard = self._client.get_dashboard()
            net_worth = float(dashboard.get("net_worth", {}).get("net_worth", 0))

            today = date.today()
            total_inv = 0.0
            count = 0
            for delta in range(3):
                m = today.month - delta
                y = today.year
                if m <= 0:
                    m += 12
                    y -= 1
                try:
                    txs = self._client.get_transactions(
                        start_date=date(y, m, 1),
                        end_date=date(y, m, 28),
                    )
                    inv = sum(
                        float(t.get("amount", 0))
                        for t in txs
                        if t.get("transaction_type") == "investment"
                    )
                    total_inv += inv
                    count += 1
                except Exception:
                    pass

            avg_inv = total_inv / count if count > 0 else 0.0
            self.done.emit(net_worth, avg_inv)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


# -----------------------------------------------------------------
# Helpers visuais
# -----------------------------------------------------------------

def _fmt_brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _card(title: str, value: str, color: str = _BLUE, subtitle: str = "") -> QFrame:
    frame = QFrame()
    frame.setObjectName("simCard")
    frame.setStyleSheet(
        f"QFrame#simCard {{ background: {_BG_CARD}; border-radius: 8px; "
        f"border-left: 3px solid {color}; }}"
    )
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setStyleSheet(f"color: {_TEXT}; font-size: 11px;")
    v = QLabel(value)
    v.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: 700;")
    v.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(v)
    if subtitle:
        s = QLabel(subtitle)
        s.setStyleSheet(f"color: {_TEXT}; font-size: 10px;")
        lay.addWidget(s)
    return frame


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: #E8EAED; font-size: 13px; font-weight: 700; "
        f"padding-top: 8px; padding-bottom: 4px;"
    )
    return lbl


def _slider_row(label: str, lo: int, hi: int, default: int, suffix: str = "%") -> tuple[QWidget, QSlider, QLabel]:
    """Retorna (container, slider, value_label)."""
    container = QWidget()
    lay = QHBoxLayout(container)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
    lbl.setFixedWidth(170)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(lo, hi)
    slider.setValue(default)
    slider.setTickInterval((hi - lo) // 10 or 1)
    val_lbl = QLabel(f"{default}{suffix}")
    val_lbl.setStyleSheet(f"color: {_BLUE}; font-size: 12px; font-weight: 600;")
    val_lbl.setFixedWidth(48)
    val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    slider.valueChanged.connect(lambda v: val_lbl.setText(f"{v}{suffix}"))
    lay.addWidget(lbl)
    lay.addWidget(slider)
    lay.addWidget(val_lbl)
    return container, slider, val_lbl


# -----------------------------------------------------------------
# Gráficos matplotlib — Aposentadoria
# -----------------------------------------------------------------

class RetirementLineCanvas(FigureCanvas):
    """Evolução patrimonial: nominal (azul), real (verde), objetivo (vermelho tracejado)."""

    def __init__(self) -> None:
        self._fig = Figure(figsize=(7, 3.5), facecolor=_BG_RGB)
        super().__init__(self._fig)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ax = self._fig.add_subplot(111)
        self._style_ax()

    def _style_ax(self) -> None:
        ax = self._ax
        ax.set_facecolor(_BG_RGB)
        ax.tick_params(colors=_TEXT_RGB, labelsize=8)
        ax.spines[:].set_color(_GRID_RGB)
        ax.grid(color=_GRID_RGB, linewidth=0.5, zorder=0)

    def update_data(self, projected: list[float], real: list[float], target: float) -> None:
        ax = self._ax
        ax.clear()
        self._style_ax()

        x = list(range(1, len(projected) + 1))
        ax.plot(x, projected, color=_BLUE,  linewidth=2, label="Nominal", zorder=3)
        ax.plot(x, real,      color=_GREEN, linewidth=2, label="Real (hoje)", zorder=3,
                linestyle="--")
        ax.axhline(target, color=_RED, linewidth=1.2, linestyle=":", label="Objetivo", zorder=2)

        ax.fill_between(x, projected, alpha=0.08, color=_BLUE)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"R${v/1e6:.1f}M" if abs(v) >= 1e6 else f"R${v/1e3:.0f}k")
        )
        ax.set_xlabel("Meses", color=_TEXT_RGB, fontsize=9)
        ax.legend(fontsize=8, labelcolor=_TEXT_RGB, facecolor=_BG_RGB, edgecolor=_GRID_RGB)
        self._fig.tight_layout(pad=0.5)
        self.draw()


class RetirementAreaCanvas(FigureCanvas):
    """Composição do patrimônio final: aportado vs rendimentos."""

    def __init__(self) -> None:
        self._fig = Figure(figsize=(7, 2.0), facecolor=_BG_RGB)
        super().__init__(self._fig)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(180)
        self._ax = self._fig.add_subplot(111)
        self._style_ax()

    def _style_ax(self) -> None:
        ax = self._ax
        ax.set_facecolor(_BG_RGB)
        ax.tick_params(colors=_TEXT_RGB, labelsize=8)
        ax.spines[:].set_color(_GRID_RGB)
        ax.grid(color=_GRID_RGB, linewidth=0.5, zorder=0)

    def update_data(self, projected: list[float], total_contributed: float) -> None:
        ax = self._ax
        ax.clear()
        self._style_ax()

        x = list(range(1, len(projected) + 1))
        contrib_curve = [min(total_contributed * (i / len(projected)), total_contributed) for i in x]
        returns_curve = [max(0.0, projected[i-1] - contrib_curve[i-1]) for i in x]

        ax.stackplot(x, contrib_curve, returns_curve,
                     labels=["Aportado", "Rendimentos"],
                     colors=[_BLUE + "99", _GREEN + "99"], zorder=2)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"R${v/1e6:.1f}M" if abs(v) >= 1e6 else f"R${v/1e3:.0f}k")
        )
        ax.legend(fontsize=8, labelcolor=_TEXT_RGB, facecolor=_BG_RGB, edgecolor=_GRID_RGB)
        self._fig.tight_layout(pad=0.5)
        self.draw()


# -----------------------------------------------------------------
# Aba 1 — Aposentadoria / FIRE
# -----------------------------------------------------------------

class RetirementTab(QWidget):
    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client          = client
        self._ret_worker: RetirementWorker | None = None
        self._fire_worker: FireWorker | None = None
        self._fetch_worker: DashboardFetchWorker | None = None
        self._build_ui()
        self._auto_fill()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(24)

        # ── Coluna esquerda — parâmetros ──────────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setFixedWidth(400)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        left_widget = QWidget()
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 8, 0)
        left.setSpacing(10)

        # Cards de resultado
        self._card_fire = _card("Número FIRE", "—", _ORANGE)
        self._card_months = _card("Tempo estimado", "—", _BLUE)
        self._card_target = _card("Patrimônio objetivo", "—", _GREEN)

        cards_lay = QGridLayout()
        cards_lay.setSpacing(8)
        cards_lay.addWidget(self._card_fire,   0, 0)
        cards_lay.addWidget(self._card_months, 0, 1)
        cards_lay.addWidget(self._card_target, 1, 0, 1, 2)
        left.addLayout(cards_lay)

        left.addWidget(_section_label("Parâmetros da simulação"))

        # Patrimônio atual
        row_port = QHBoxLayout()
        lbl_port = QLabel("Patrimônio atual (R$)")
        lbl_port.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
        lbl_port.setFixedWidth(170)
        self._portfolio_spin = QDoubleSpinBox()
        self._portfolio_spin.setRange(0, 100_000_000)
        self._portfolio_spin.setDecimals(2)
        self._portfolio_spin.setSingleStep(1000)
        self._portfolio_spin.setGroupSeparatorShown(True)
        self._portfolio_spin.setValue(0)
        row_port.addWidget(lbl_port)
        row_port.addWidget(self._portfolio_spin)
        left.addLayout(row_port)

        # Aporte mensal
        row_cont = QHBoxLayout()
        lbl_cont = QLabel("Aporte mensal (R$)")
        lbl_cont.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
        lbl_cont.setFixedWidth(170)
        self._contribution_spin = QDoubleSpinBox()
        self._contribution_spin.setRange(0, 500_000)
        self._contribution_spin.setDecimals(2)
        self._contribution_spin.setSingleStep(100)
        self._contribution_spin.setGroupSeparatorShown(True)
        self._contribution_spin.setValue(1000)
        row_cont.addWidget(lbl_cont)
        row_cont.addWidget(self._contribution_spin)
        left.addLayout(row_cont)

        # Objetivo R$
        row_target = QHBoxLayout()
        lbl_target = QLabel("Objetivo (R$)")
        lbl_target.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
        lbl_target.setFixedWidth(170)
        self._target_spin = QDoubleSpinBox()
        self._target_spin.setRange(1, 100_000_000)
        self._target_spin.setDecimals(2)
        self._target_spin.setSingleStep(10000)
        self._target_spin.setGroupSeparatorShown(True)
        self._target_spin.setValue(1_000_000)
        row_target.addWidget(lbl_target)
        row_target.addWidget(self._target_spin)
        left.addLayout(row_target)

        # Sliders
        ret_row, self._ret_slider, _ = _slider_row("Rentabilidade anual", 4, 20, 10, "%")
        inf_row, self._inf_slider, _ = _slider_row("Inflação anual",       2, 10,  5, "%")
        yrs_row, self._years_slider, self._years_lbl = _slider_row("Prazo (anos)", 5, 40, 20, " anos")
        left.addWidget(ret_row)
        left.addWidget(inf_row)
        left.addWidget(yrs_row)

        # Botões
        btn_row = QHBoxLayout()
        self._simulate_btn = QPushButton("  Simular")
        self._simulate_btn.setIcon(_svg_icon("trending_up", "#FFFFFF", 16))
        self._simulate_btn.setProperty("class", "primary")
        self._simulate_btn.style().unpolish(self._simulate_btn)
        self._simulate_btn.style().polish(self._simulate_btn)
        self._simulate_btn.setFixedHeight(40)
        self._simulate_btn.clicked.connect(self._run_simulation)
        self._fire_btn = QPushButton("🔥  Usar FIRE")
        self._fire_btn.setToolTip("Preenche o objetivo com seu número FIRE calculado")
        self._fire_btn.clicked.connect(self._load_fire)
        btn_row.addWidget(self._simulate_btn)
        btn_row.addWidget(self._fire_btn)
        left.addLayout(btn_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 11px;")
        self._status_lbl.setWordWrap(True)
        left.addWidget(self._status_lbl)
        left.addStretch()
        left_scroll.setWidget(left_widget)

        # ── Coluna direita — gráficos ─────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(12)

        self._line_canvas = RetirementLineCanvas()
        self._area_canvas = RetirementAreaCanvas()

        right.addWidget(_section_label("Evolução patrimonial"))
        right.addWidget(self._line_canvas)
        right.addWidget(_section_label("Composição do patrimônio final"))
        right.addWidget(self._area_canvas)
        right.addStretch()

        root.addWidget(left_scroll)
        right_widget = QWidget()
        right_widget.setLayout(right)
        root.addWidget(right_widget, stretch=1)

    def _auto_fill(self) -> None:
        if self._fetch_worker and self._fetch_worker.isRunning():
            return
        self._fetch_worker = DashboardFetchWorker(self._client)
        self._fetch_worker.done.connect(self._on_fetch_done)
        self._fetch_worker.error_occurred.connect(lambda _: None)
        self._fetch_worker.start()

    def _on_fetch_done(self, net_worth: float, avg_inv: float) -> None:
        if net_worth > 0:
            self._portfolio_spin.setValue(net_worth)
        if avg_inv > 0:
            self._contribution_spin.setValue(avg_inv)
        # Dispara a simulação automaticamente com os dados pré-preenchidos
        QTimer.singleShot(150, self._run_simulation)

    def _load_fire(self) -> None:
        if self._fire_worker and self._fire_worker.isRunning():
            return
        self._fire_btn.setEnabled(False)
        self._fire_worker = FireWorker(self._client)
        self._fire_worker.done.connect(self._on_fire_done)
        self._fire_worker.error_occurred.connect(
            lambda msg: (self._fire_btn.setEnabled(True), self._status_lbl.setText(f"Erro FIRE: {msg}"))
        )
        self._fire_worker.start()

    def _on_fire_done(self, data: dict) -> None:
        self._fire_btn.setEnabled(True)
        fire_num = data.get("fire_number", 0)
        self._target_spin.setValue(fire_num)
        portfolio = data.get("current_portfolio", 0)
        if portfolio > 0:
            self._portfolio_spin.setValue(portfolio)
        avg_inv = data.get("avg_monthly_investment", 0)
        if avg_inv > 0:
            self._contribution_spin.setValue(avg_inv)
        self._card_fire.findChildren(QLabel)[1].setText(_fmt_brl(fire_num))

    def _run_simulation(self) -> None:
        if self._ret_worker and self._ret_worker.isRunning():
            return
        payload = {
            "monthly_contribution": self._contribution_spin.value(),
            "current_portfolio":    self._portfolio_spin.value(),
            "target_amount":        self._target_spin.value(),
            "annual_return_rate":   self._ret_slider.value(),
            "inflation_rate":       self._inf_slider.value(),
            "months":               self._years_slider.value() * 12,
        }
        self._simulate_btn.setEnabled(False)
        self._status_lbl.setText("Simulando…")
        self._ret_worker = RetirementWorker(self._client, payload)
        self._ret_worker.done.connect(self._on_result)
        self._ret_worker.error_occurred.connect(self._on_error)
        self._ret_worker.start()

    def _on_result(self, data: dict) -> None:
        self._simulate_btn.setEnabled(True)
        projected = data.get("projected_values", [])
        real      = data.get("inflation_adjusted_values", [])
        target    = self._target_spin.value()

        self._line_canvas.update_data(projected, real, target)
        self._area_canvas.update_data(projected, data.get("total_contributed", 0))

        mtt = data.get("months_to_target")
        if mtt:
            years, months = divmod(mtt, 12)
            time_str = f"{years}a {months}m" if months else f"{years} anos"
            self._card_months.findChildren(QLabel)[1].setText(time_str)
        else:
            self._card_months.findChildren(QLabel)[1].setText("Não atinge no prazo")

        self._card_target.findChildren(QLabel)[1].setText(_fmt_brl(target))
        self._card_fire.findChildren(QLabel)[1].setText(
            _fmt_brl(data.get("final_portfolio", 0))
        )

        ok = data.get("success", False)
        color = _GREEN if ok else _RED
        msg   = f"✅ Objetivo atingido em {mtt} meses" if ok else "⚠️ Objetivo não atingido no prazo"
        self._status_lbl.setText(msg)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")

    def _on_error(self, msg: str) -> None:
        self._simulate_btn.setEnabled(True)
        self._status_lbl.setText(f"Erro: {msg}")
        self._status_lbl.setStyleSheet(f"color: {_RED}; font-size: 11px;")


# -----------------------------------------------------------------
# Aba 2 — Objetivos Financeiros
# -----------------------------------------------------------------

class GoalDialog(QDialog):
    CATEGORIES = ["viagem", "imóvel", "educação", "carro", "emergência", "casamento", "negócio", "outro"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Novo Objetivo")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(16)
        form = QFormLayout()
        form.setSpacing(10)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Ex: Viagem Europa")
        form.addRow("Nome:", self._name)

        self._category = QComboBox()
        self._category.addItems(self.CATEGORIES)
        form.addRow("Categoria:", self._category)

        self._amount = QDoubleSpinBox()
        self._amount.setRange(1, 10_000_000)
        self._amount.setDecimals(2)
        self._amount.setSingleStep(1000)
        self._amount.setGroupSeparatorShown(True)
        self._amount.setValue(10000)
        form.addRow("Valor alvo (R$):", self._amount)

        self._savings = QDoubleSpinBox()
        self._savings.setRange(0, 10_000_000)
        self._savings.setDecimals(2)
        self._savings.setGroupSeparatorShown(True)
        form.addRow("Já guardado (R$):", self._savings)

        self._monthly = QDoubleSpinBox()
        self._monthly.setRange(0, 100_000)
        self._monthly.setDecimals(2)
        self._monthly.setSingleStep(100)
        self._monthly.setGroupSeparatorShown(True)
        form.addRow("Aporte mensal (R$):", self._monthly)

        self._months = QSpinBox()
        self._months.setRange(1, 600)
        self._months.setValue(24)
        self._months.setSuffix(" meses")
        form.addRow("Prazo:", self._months)

        ret_row, self._ret_slider, self._ret_lbl = _slider_row("Rentabilidade anual", 0, 20, 8)
        form.addRow("", ret_row)

        lay.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _validate(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe o nome do objetivo.")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "name":         self._name.text().strip(),
            "category":     self._category.currentText(),
            "amount":       self._amount.value(),
            "savings":      self._savings.value(),
            "monthly":      self._monthly.value(),
            "months":       self._months.value(),
            "return_rate":  self._ret_slider.value(),
        }


class GoalCard(QFrame):
    edit_requested   = pyqtSignal(dict)
    delete_requested = pyqtSignal(dict)

    def __init__(self, goal: dict) -> None:
        super().__init__()
        self._goal = goal
        self.setObjectName("goalCard")
        self.setStyleSheet(
            "QFrame#goalCard { background: #222640; border-radius: 10px; }"
        )
        self.setMinimumHeight(140)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        g = self._goal
        cat   = g.get("category", "outro")
        icon  = _GOAL_ICONS.get(cat, "🎯")
        name  = g.get("name", "")
        amt   = g.get("amount", 0)
        saved = g.get("savings", 0)
        mths  = g.get("months", 1)
        viable = g.get("viable", True)

        pct = min(100, int(saved / amt * 100)) if amt > 0 else 0
        remaining_months = g.get("months_to_goal")

        # Header
        hdr = QHBoxLayout()
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 22px; background: transparent;")
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("color: #E8EAED; font-weight: 700; font-size: 14px; background: transparent;")
        status_color = _GREEN if viable else _ORANGE
        status_text  = "No prazo ✓" if viable else "Revisar !"
        status_lbl   = QLabel(status_text)
        status_lbl.setStyleSheet(f"color: {status_color}; font-size: 11px; font-weight: 600; background: transparent;")
        hdr.addWidget(icon_lbl)
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        hdr.addWidget(status_lbl)
        lay.addLayout(hdr)

        # Progresso
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(True)
        bar.setFormat(f"{pct}%")
        bar.setFixedHeight(16)
        bar.setStyleSheet(f"""
            QProgressBar {{ background: #2A2D3E; border-radius: 8px; }}
            QProgressBar::chunk {{ background: {_GREEN if pct >= 80 else (_ORANGE if pct >= 40 else _BLUE)};
                                   border-radius: 8px; }}
        """)
        lay.addWidget(bar)

        # Valores
        vals = QHBoxLayout()
        vals.addWidget(QLabel(f"<span style='color:{_TEXT};font-size:11px;'>Guardado: </span>"
                               f"<span style='color:{_GREEN};font-weight:600;font-size:12px;'>{_fmt_brl(saved)}</span>"))
        vals.addStretch()
        vals.addWidget(QLabel(f"<span style='color:{_TEXT};font-size:11px;'>Meta: </span>"
                               f"<span style='color:#E8EAED;font-weight:600;font-size:12px;'>{_fmt_brl(amt)}</span>"))
        for lbl in [w for w in vals.children() if isinstance(w, QLabel)]:
            lbl.setTextFormat(Qt.TextFormat.RichText)
        lay.addLayout(vals)

        # Estimativa
        if remaining_months:
            est = QLabel(f"⏱ Estimativa: {remaining_months} meses restantes | Prazo: {mths} meses")
        else:
            est = QLabel(f"⏱ Prazo: {mths} meses | Aporte necessário maior que o configurado")
        est.setStyleSheet(f"color: {_TEXT}; font-size: 10px; background: transparent;")
        lay.addWidget(est)

        # Botões de ação
        btns = QHBoxLayout()
        btns.addStretch()
        edit_btn = QPushButton()
        edit_btn.setIcon(_svg_icon("edit", "#C8CAD8", 12))
        edit_btn.setFixedSize(28, 28)
        edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._goal))
        del_btn = QPushButton()
        del_btn.setIcon(_svg_icon("delete", "#FF6B6B", 12))
        del_btn.setFixedSize(28, 28)
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._goal))
        btns.addWidget(edit_btn)
        btns.addWidget(del_btn)
        lay.addLayout(btns)


class GoalsTab(QWidget):
    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client
        self._goals: list[dict] = []
        self._build_ui()
        self._load_goals()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(_section_label("Meus Objetivos Financeiros"))
        toolbar.addStretch()
        new_btn = QPushButton("+ Novo Objetivo")
        new_btn.setProperty("class", "primary")
        new_btn.style().unpolish(new_btn)
        new_btn.style().polish(new_btn)
        new_btn.clicked.connect(self._open_new_dialog)
        toolbar.addWidget(new_btn)
        outer.addLayout(toolbar)

        # Área rolável dos cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_container = QWidget()
        self._cards_layout    = QVBoxLayout(self._cards_container)
        self._cards_layout.setSpacing(12)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_container)
        outer.addWidget(scroll)

    def _load_goals(self) -> None:
        if not _GOALS_FILE.exists():
            self._goals = []
        else:
            try:
                self._goals = json.loads(_GOALS_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._goals = []
        self._render_goals()

    def _save_goals(self) -> None:
        _GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GOALS_FILE.write_text(json.dumps(self._goals, ensure_ascii=False, indent=2), encoding="utf-8")

    def _simulate_goal(self, goal: dict) -> dict:
        """Calcula viabilidade e meses para atingir (side-effect-free)."""
        amount  = goal.get("amount", 0)
        savings = goal.get("savings", 0)
        monthly = goal.get("monthly", 0)
        months  = goal.get("months", 1)
        rate    = goal.get("return_rate", 0)

        if amount <= 0:
            return {**goal, "viable": True, "months_to_goal": 0}

        from backend.services.simulation_service import simulate_goal as _sim
        from decimal import Decimal
        result = _sim(
            goal_name=goal.get("name", ""),
            goal_amount=Decimal(str(amount)),
            monthly_contribution=Decimal(str(monthly)),
            current_savings=Decimal(str(savings)),
            annual_return_rate=Decimal(str(rate)),
            deadline_months=months,
        )
        return {**goal, "viable": result["viable"], "months_to_goal": result.get("months_to_goal")}

    def _render_goals(self) -> None:
        # Remove widgets antigos (exceto o stretch final)
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._goals:
            empty = QLabel("Nenhum objetivo cadastrado.\nClique em '+ Novo Objetivo' para começar.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {_TEXT}; font-size: 13px; padding: 40px;")
            self._cards_layout.insertWidget(0, empty)
            return

        for i, goal in enumerate(self._goals):
            enriched = self._simulate_goal(goal)
            card = GoalCard(enriched)
            card.delete_requested.connect(self._delete_goal)
            self._cards_layout.insertWidget(i, card)

    def _open_new_dialog(self) -> None:
        dialog = GoalDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.get_data()
        self._goals.append(data)
        self._save_goals()
        self._render_goals()

    def _delete_goal(self, goal: dict) -> None:
        reply = QMessageBox.question(
            self, "Confirmar exclusão",
            f"Excluir o objetivo \"{goal.get('name', '')}\"?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._goals = [g for g in self._goals if g.get("name") != goal.get("name")]
        self._save_goals()
        self._render_goals()


# -----------------------------------------------------------------
# Aba 3 — Quitação de Dívidas
# -----------------------------------------------------------------

class DebtPayoffCanvas(FigureCanvas):
    """Barras comparando juros totais: sem extra vs com extra."""

    def __init__(self) -> None:
        self._fig = Figure(figsize=(6, 2.5), facecolor=_BG_RGB)
        super().__init__(self._fig)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(200)
        self._ax = self._fig.add_subplot(111)

    def update_data(self, std_interest: float, acc_interest: float,
                    std_months: int, acc_months: int) -> None:
        ax = self._ax
        ax.clear()
        ax.set_facecolor(_BG_RGB)
        ax.tick_params(colors=_TEXT_RGB, labelsize=9)
        ax.spines[:].set_color(_GRID_RGB)
        ax.grid(axis="y", color=_GRID_RGB, linewidth=0.5, zorder=0)

        cats   = ["Sem extra", "Com extra"]
        juros  = [std_interest, acc_interest]
        colors = [_RED + "CC", _GREEN + "CC"]
        ax.bar(cats, juros, color=colors, width=0.45, zorder=3)
        ax.set_ylabel("Juros totais (R$)", color=_TEXT_RGB, fontsize=9)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"R${v:,.0f}".replace(",", "."))
        )
        for i, (v, m) in enumerate(zip(juros, [std_months, acc_months])):
            ax.text(i, v + max(juros) * 0.02, f"{m}m", ha="center",
                    va="bottom", color=_TEXT_RGB, fontsize=9, fontweight="bold")
        self._fig.tight_layout(pad=0.5)
        self.draw()


class DebtPayoffTab(QWidget):
    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client
        self._worker: DebtPayoffWorker | None = None
        self._debts: list[dict] = []
        self._build_ui()
        self._load_debts()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(24)

        # ── Coluna esquerda — parâmetros ──────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(10)

        left.addWidget(_section_label("Dívida a simular"))

        # Dropdown de dívidas cadastradas
        row_debt = QHBoxLayout()
        lbl_debt = QLabel("Dívida cadastrada")
        lbl_debt.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
        lbl_debt.setFixedWidth(140)
        self._debt_combo = QComboBox()
        self._debt_combo.addItem("— inserir manualmente —", None)
        self._debt_combo.currentIndexChanged.connect(self._on_debt_selected)
        row_debt.addWidget(lbl_debt)
        row_debt.addWidget(self._debt_combo)
        left.addLayout(row_debt)

        # Campos manuais
        form = QFormLayout()
        form.setSpacing(8)

        self._debt_amount = QDoubleSpinBox()
        self._debt_amount.setRange(1, 10_000_000)
        self._debt_amount.setDecimals(2)
        self._debt_amount.setGroupSeparatorShown(True)
        self._debt_amount.setValue(10000)
        form.addRow("Saldo devedor (R$):", self._debt_amount)

        self._monthly_rate = QDoubleSpinBox()
        self._monthly_rate.setRange(0.01, 30)
        self._monthly_rate.setDecimals(2)
        self._monthly_rate.setSuffix("% a.m.")
        self._monthly_rate.setValue(2.0)
        form.addRow("Taxa mensal (%):", self._monthly_rate)

        self._monthly_payment = QDoubleSpinBox()
        self._monthly_payment.setRange(1, 100_000)
        self._monthly_payment.setDecimals(2)
        self._monthly_payment.setGroupSeparatorShown(True)
        self._monthly_payment.setValue(500)
        form.addRow("Parcela atual (R$):", self._monthly_payment)

        left.addLayout(form)

        extra_row, self._extra_slider, self._extra_lbl = _slider_row(
            "Aporte extra mensal", 0, 2000, 0, " R$"
        )
        self._extra_slider.setTickInterval(100)
        left.addWidget(extra_row)

        compare_btn = QPushButton("  Comparar cenários")
        compare_btn.setIcon(_svg_icon("chart", "#FFFFFF", 16))
        compare_btn.setProperty("class", "primary")
        compare_btn.style().unpolish(compare_btn)
        compare_btn.style().polish(compare_btn)
        compare_btn.setFixedHeight(40)
        compare_btn.clicked.connect(self._run_simulation)
        left.addWidget(compare_btn)

        self._debt_status = QLabel("")
        self._debt_status.setStyleSheet(f"color: {_TEXT}; font-size: 11px;")
        self._debt_status.setWordWrap(True)
        left.addWidget(self._debt_status)
        left.addStretch()

        # ── Coluna direita — resultado ─────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(12)

        right.addWidget(_section_label("Comparação de cenários"))

        self._result_row = QHBoxLayout()
        self._card_std = _card("Sem aporte extra", "—", _RED)
        self._card_acc = _card("Com aporte extra", "—", _GREEN)
        self._result_row.addWidget(self._card_std)
        self._result_row.addWidget(self._card_acc)
        right.addLayout(self._result_row)

        self._economy_card = _card("Economia total", "—", _ORANGE)
        right.addWidget(self._economy_card)

        self._chart = DebtPayoffCanvas()
        right.addWidget(self._chart)
        right.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(380)
        root.addWidget(left_widget)
        right_widget = QWidget()
        right_widget.setLayout(right)
        root.addWidget(right_widget, stretch=1)

    def _load_debts(self) -> None:
        try:
            self._debts = self._client.get_debts()
            for debt in self._debts:
                self._debt_combo.addItem(debt.get("name", "Dívida"), debt)
        except Exception:
            pass

    def _on_debt_selected(self, index: int) -> None:
        debt = self._debt_combo.currentData()
        if not debt:
            return
        remaining = float(debt.get("remaining_amount", debt.get("total_amount", 0)))
        rate      = float(debt.get("monthly_rate", 2.0))
        payment   = float(debt.get("installment_amount", 500))
        self._debt_amount.setValue(remaining)
        self._monthly_rate.setValue(rate)
        self._monthly_payment.setValue(payment)

    def _run_simulation(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        payload = {
            "debt_amount":     self._debt_amount.value(),
            "monthly_rate":    self._monthly_rate.value(),
            "monthly_payment": self._monthly_payment.value(),
            "extra_payment":   self._extra_slider.value(),
        }
        self._debt_status.setText("Calculando…")
        self._worker = DebtPayoffWorker(self._client, payload)
        self._worker.done.connect(self._on_result)
        self._worker.error_occurred.connect(
            lambda msg: self._debt_status.setText(f"Erro: {msg}")
        )
        self._worker.start()

    def _on_result(self, data: dict) -> None:
        std = data.get("standard", {})
        acc = data.get("accelerated", {})
        saved_m  = data.get("months_saved", 0)
        saved_r  = data.get("interest_saved", 0.0)

        std_lbl = self._card_std.findChildren(QLabel)
        acc_lbl = self._card_acc.findChildren(QLabel)
        eco_lbl = self._economy_card.findChildren(QLabel)

        std_lbl[1].setText(f"{std.get('months','—')} meses\nJuros: {_fmt_brl(std.get('total_interest',0))}")
        acc_lbl[1].setText(f"{acc.get('months','—')} meses\nJuros: {_fmt_brl(acc.get('total_interest',0))}")
        eco_lbl[1].setText(f"{_fmt_brl(saved_r)} ({saved_m} meses a menos)")

        self._chart.update_data(
            std.get("total_interest", 0),
            acc.get("total_interest", 0),
            std.get("months", 0),
            acc.get("months", 0),
        )
        self._debt_status.setText("Simulação concluída.")


# -----------------------------------------------------------------
# Aba 4 — Calculadoras Rápidas
# -----------------------------------------------------------------

class QuickCalcFrame(QFrame):
    """Calculadora com campos e resultado atualizado em tempo real."""

    def __init__(self, title: str, fields: list[tuple[str, float, float, float, str]],
                 calc_fn, result_prefix: str = "=") -> None:
        super().__init__()
        self.setObjectName("calcFrame")
        self.setStyleSheet("QFrame#calcFrame { background: #222640; border-radius: 10px; }")
        self._calc_fn = calc_fn
        self._timer   = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._recalc)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #E8EAED; font-size: 13px; font-weight: 700;")
        lay.addWidget(title_lbl)

        self._spins: list[QDoubleSpinBox] = []
        form = QFormLayout()
        form.setSpacing(6)
        for label, lo, hi, default, suffix in fields:
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(2)
            spin.setSuffix(suffix)
            spin.setValue(default)
            spin.valueChanged.connect(lambda _: self._timer.start())
            self._spins.append(spin)
            form.addRow(label + ":", spin)
        lay.addLayout(form)

        self._result_lbl = QLabel(f"{result_prefix} —")
        self._result_lbl.setStyleSheet(
            f"color: {_GREEN}; font-size: 15px; font-weight: 700; padding-top: 4px;"
        )
        self._result_lbl.setWordWrap(True)
        lay.addWidget(self._result_lbl)
        self._result_prefix = result_prefix
        self._recalc()

    def _recalc(self) -> None:
        try:
            values = [s.value() for s in self._spins]
            result = self._calc_fn(*values)
            self._result_lbl.setText(f"{self._result_prefix}  {result}")
        except Exception:
            self._result_lbl.setText(f"{self._result_prefix}  —")


def _calc_compound(capital: float, annual_rate: float, years: float) -> str:
    r = annual_rate / 100
    fv = capital * (1 + r) ** years
    return f"{_fmt_brl(fv)}  (+{_fmt_brl(fv - capital)})"


def _calc_cdi(capital: float, pct_cdi: float, months: float) -> str:
    cdi_annual = 10.65  # referência atual
    effective  = cdi_annual * pct_cdi / 100
    r_monthly  = (1 + effective / 100) ** (1 / 12) - 1
    fv = capital * (1 + r_monthly) ** months
    return f"{_fmt_brl(fv)}  (+{_fmt_brl(fv - capital)})"


def _calc_inflation(value: float, ipca: float, years: float) -> str:
    fv = value * (1 + ipca / 100) ** years
    return f"{_fmt_brl(fv)}  (poder de compra: {_fmt_brl(value)})"


def _calc_pmt(goal: float, months: float, annual_rate: float) -> str:
    if months <= 0:
        return "—"
    r = (1 + annual_rate / 100) ** (1 / 12) - 1
    if r == 0:
        pmt = goal / months
    else:
        pmt = goal * r / ((1 + r) ** months - 1)
    return _fmt_brl(pmt) + "/mês"


class CalculatorsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._build_ui()

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(24, 20, 24, 20)
        grid.setSpacing(16)

        calcs = [
            QuickCalcFrame(
                "💰 Juros Compostos",
                [("Capital inicial (R$)", 0, 10_000_000, 10000, " R$"),
                 ("Rentabilidade anual", 0, 50, 10, "%"),
                 ("Período (anos)", 0.1, 50, 5, " anos")],
                _calc_compound,
                "Montante final:",
            ),
            QuickCalcFrame(
                "📈 Rendimento CDI",
                [("Capital (R$)", 0, 10_000_000, 10000, " R$"),
                 ("% do CDI", 50, 200, 100, "%"),
                 ("Período (meses)", 1, 360, 12, " meses")],
                _calc_cdi,
                "Montante final:",
            ),
            QuickCalcFrame(
                "🔥 Inflação",
                [("Valor hoje (R$)", 0, 10_000_000, 1000, " R$"),
                 ("IPCA anual", 0, 30, 4.5, "%"),
                 ("Anos", 1, 50, 10, " anos")],
                _calc_inflation,
                "Valor futuro:",
            ),
            QuickCalcFrame(
                "🎯 Quanto preciso poupar?",
                [("Objetivo (R$)", 0, 10_000_000, 50000, " R$"),
                 ("Prazo (meses)", 1, 600, 60, " meses"),
                 ("Rentabilidade anual", 0, 30, 8, "%")],
                _calc_pmt,
                "Aporte mensal:",
            ),
        ]

        for i, calc in enumerate(calcs):
            grid.addWidget(calc, i // 2, i % 2)
        grid.setRowStretch(2, 1)

        scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)


# -----------------------------------------------------------------
# Página principal
# -----------------------------------------------------------------

class SimulationPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        tabs = QTabWidget()
        tabs.addTab(RetirementTab(self._client), "🏖  Aposentadoria / FIRE")
        tabs.addTab(GoalsTab(self._client),      "🎯  Objetivos")
        tabs.addTab(DebtPayoffTab(self._client), "💳  Quitação de Dívidas")
        tabs.addTab(CalculatorsTab(),            "🧮  Calculadoras")
        lay.addWidget(tabs)

    def load_data(self) -> None:
        """Chamado pelo botão ↻ Atualizar da sidebar — recria a página ou faz reload."""
        pass
