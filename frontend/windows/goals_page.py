"""
Página de Metas Financeiras — acompanhamento de metas pessoais.

Layout:
  ┌─────────────────────────────────────────┐
  │ Minhas Metas Financeiras                │
  ├─────────────────────────────────────────┤
  │ ┌─────────────────────────────────────┐ │
  │ │ Meta: Aporte mensal                 │ │
  │ │ Objetivo: R$ 2.000/mês             │ │
  │ │ Realizado: R$ 1.500 (75%)          │ │
  │ │ ████████████░░░░ 75%               │ │
  │ └─────────────────────────────────────┘ │
  │ [+ Nova Meta]                           │
  └─────────────────────────────────────────┘

Persistência: data/goals.json
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.icons import icon as _svg_icon


# ─── Constantes ────────────────────────────────────────────────────────────

_GOALS_PATH = Path(__file__).parent.parent.parent / "data" / "goals.json"

_GOAL_TYPES = [
    ("MONTHLY_INVESTMENT", "Aporte mensal"),
    ("TOTAL_PORTFOLIO",    "Carteira total"),
    ("EMERGENCY_FUND",     "Fundo de emergência"),
    ("SAVINGS_RATE",       "Taxa de poupança (%)"),
    ("DEBT_FREE",          "Quitar dívidas"),
    ("CUSTOM",             "Meta personalizada"),
]

_GOAL_ICONS = [
    ("chart",       "Gráfico"),
    ("dollar",      "Dinheiro"),
    ("wallet",      "Carteira"),
    ("home",        "Casa"),
    ("trending_up", "Crescimento"),
    ("check",       "Concluída"),
]

_DEFAULT_COLORS = [
    "#4A9EFF", "#00C896", "#FFB347", "#FF6B6B",
    "#9B59B6", "#1ABC9C", "#E67E22",
]


# ─── Helpers ───────────────────────────────────────────────────────────────

def _fmt_brl(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'−' if v < 0 else ''}R$ {s}"


def load_goals() -> list[dict]:
    try:
        if _GOALS_PATH.exists():
            return json.loads(_GOALS_PATH.read_text(encoding="utf-8")).get("goals", [])
    except (json.JSONDecodeError, OSError):
        pass
    return []


def save_goals(goals: list[dict]) -> None:
    _GOALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GOALS_PATH.write_text(
        json.dumps({"goals": goals}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ─── Worker ────────────────────────────────────────────────────────────────

class GoalValuesWorker(QThread):
    """
    Busca os valores atuais de cada tipo de meta consultando a API.
    Emite um dict {goal_type: current_value}.
    """

    data_ready     = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, goal_types: set[str]) -> None:
        super().__init__()
        self._client = client
        self._types  = goal_types

    def run(self) -> None:
        try:
            values: dict[str, float] = {}

            if "TOTAL_PORTFOLIO" in self._types or "MONTHLY_INVESTMENT" in self._types or "SAVINGS_RATE" in self._types:
                try:
                    dash = self._client.get_dashboard()
                    nw   = dash.get("net_worth", {})
                    mon  = dash.get("monthly_summary", {})
                    values["TOTAL_PORTFOLIO"]    = float(nw.get("net_worth", 0))
                    values["MONTHLY_INVESTMENT"] = float(mon.get("investment", mon.get("investments", 0)))
                    values["SAVINGS_RATE"]       = float(mon.get("savings_rate", 0))
                except ApiError:
                    pass

            if "EMERGENCY_FUND" in self._types:
                try:
                    ef = self._client.get_emergency_fund()
                    values["EMERGENCY_FUND"] = float(ef.get("saldo_total", 0))
                except ApiError:
                    pass

            if "DEBT_FREE" in self._types:
                try:
                    debts = self._client.get_debts()
                    total_debt = sum(float(d.get("remaining_balance", 0)) for d in debts)
                    values["DEBT_FREE"] = total_debt
                except ApiError:
                    pass

            self.data_ready.emit(values)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


# ─── GoalCard ──────────────────────────────────────────────────────────────

class GoalCard(QFrame):
    """Card visual de uma meta com barra de progresso e ações."""

    edit_requested   = pyqtSignal(str)  # goal id
    delete_requested = pyqtSignal(str)  # goal id

    def __init__(self, goal: dict, current_value: float | None = None) -> None:
        super().__init__()
        self._goal_id = goal["id"]
        self.setObjectName("summaryCard")
        self._build_ui(goal, current_value)

    def _build_ui(self, goal: dict, current_value: float | None) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        color      = goal.get("color", "#4A9EFF")
        icon_name  = goal.get("icon", "chart")
        goal_type  = goal.get("type", "CUSTOM")
        goal_name  = goal.get("name", "Meta")
        target     = float(goal.get("target_value", 0))
        target_dt  = goal.get("target_date")
        is_pct     = goal_type == "SAVINGS_RATE"
        is_debt    = goal_type == "DEBT_FREE"

        # ── Cabeçalho ──
        header = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_svg_icon(icon_name, color, 18).pixmap(18, 18))
        icon_lbl.setFixedSize(24, 24)

        name_lbl = QLabel(goal_name)
        name_lbl.setStyleSheet(
            f"color: #E8EAED; font-size: 14px; font-weight: 700;"
        )

        header.addWidget(icon_lbl)
        header.addWidget(name_lbl)
        header.addStretch()

        # Badge de status (calculado depois)
        self._badge = QLabel()
        self._badge.setFixedHeight(22)
        self._badge.setStyleSheet(
            "background: #2A2E4A; border-radius: 10px; padding: 2px 10px;"
            "color: #8B90A7; font-size: 10px; font-weight: 700;"
        )
        header.addWidget(self._badge)
        outer.addLayout(header)

        # ── Valores ──
        vals_row = QHBoxLayout()
        type_label_map = dict(_GOAL_TYPES)
        type_lbl = QLabel(type_label_map.get(goal_type, goal_type))
        type_lbl.setStyleSheet("color: #8B90A7; font-size: 11px;")
        vals_row.addWidget(type_lbl)
        vals_row.addStretch()

        fmt = (lambda v: f"{v:.1f}%") if is_pct else _fmt_brl
        target_lbl = QLabel(f"Meta: {fmt(target)}")
        target_lbl.setStyleSheet("color: #C8CAD8; font-size: 12px;")
        vals_row.addWidget(target_lbl)

        if target_dt:
            dt_lbl = QLabel(f"Prazo: {target_dt}")
            dt_lbl.setStyleSheet("color: #8B90A7; font-size: 11px; margin-left: 12px;")
            vals_row.addWidget(dt_lbl)

        outer.addLayout(vals_row)

        # ── Barra de progresso ──
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(10)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: #2A2E4A;
                border-radius: 5px;
                border: none;
            }}
            QProgressBar::chunk {{
                background: {color};
                border-radius: 5px;
            }}
        """)
        outer.addWidget(self._progress_bar)

        # ── Linha de progresso textual ──
        self._progress_lbl = QLabel("—")
        self._progress_lbl.setStyleSheet("color: #8B90A7; font-size: 11px;")
        outer.addWidget(self._progress_lbl)

        # ── Botões de ação ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        edit_btn = QPushButton(_svg_icon("edit", "#8B90A7", 13), " Editar")
        edit_btn.setStyleSheet(
            "color: #8B90A7; background: transparent; border: none; font-size: 11px;"
        )
        edit_btn.setToolTip("Editar esta meta")
        edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._goal_id))

        del_btn = QPushButton(_svg_icon("delete", "#FF6B6B", 13), " Excluir")
        del_btn.setStyleSheet(
            "color: #FF6B6B; background: transparent; border: none; font-size: 11px;"
        )
        del_btn.setToolTip("Excluir esta meta permanentemente")
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._goal_id))

        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        outer.addLayout(btn_row)

        # Populate progress if current value is known
        self.update_progress(current_value, target, is_pct, is_debt, color, target_dt)

    def update_progress(
        self,
        current: float | None,
        target: float,
        is_pct: bool,
        is_debt: bool,
        color: str,
        target_dt: str | None,
    ) -> None:
        if current is None:
            self._progress_lbl.setText("Buscando valor atual…")
            self._progress_bar.setValue(0)
            self._badge.setText("—")
            return

        fmt = (lambda v: f"{v:.1f}%") if is_pct else _fmt_brl

        if is_debt:
            # Dívida: progresso = quanto JÁ foi quitado (precisa de valor inicial)
            # Simplificação: se current == 0 → 100%; senão usa target como saldo inicial
            if target <= 0:
                pct = 100.0 if current == 0 else 0.0
            else:
                pct = max(0.0, min(100.0, (1 - current / target) * 100))
            self._progress_lbl.setText(
                f"Dívida restante: {fmt(current)} / {fmt(target)} inicial"
            )
        else:
            pct = max(0.0, min(100.0, (current / target * 100) if target > 0 else 0.0))
            self._progress_lbl.setText(
                f"Realizado: {fmt(current)}  /  Meta: {fmt(target)}  ({pct:.1f}%)"
            )

        self._progress_bar.setValue(int(pct))

        # Badge
        if pct >= 100:
            badge_text, badge_color = "Atingida ✓", "#00C896"
        elif target_dt:
            days_left = (date.fromisoformat(target_dt) - date.today()).days
            if days_left < 0:
                badge_text, badge_color = "Atrasada", "#FF6B6B"
            elif days_left <= 30:
                badge_text, badge_color = "Atenção", "#FFB347"
            else:
                badge_text, badge_color = "No prazo", "#4A9EFF"
        else:
            badge_text = f"{pct:.0f}%"
            badge_color = "#00C896" if pct >= 80 else ("#FFB347" if pct >= 50 else "#8B90A7")

        self._badge.setText(badge_text)
        self._badge.setStyleSheet(
            f"background: {badge_color}22; border-radius: 10px; padding: 2px 10px;"
            f"color: {badge_color}; font-size: 10px; font-weight: 700;"
        )


# ─── NewGoalDialog ─────────────────────────────────────────────────────────

class GoalDialog(QDialog):
    """Dialog para criar ou editar uma meta financeira."""

    def __init__(self, parent=None, goal: dict | None = None) -> None:
        super().__init__(parent)
        self._editing = goal
        self._color   = (goal or {}).get("color", _DEFAULT_COLORS[0])
        self.setWindowTitle("Editar Meta" if goal else "Nova Meta")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._build_ui(goal)

    def _build_ui(self, goal: dict | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        title = QLabel("Editar Meta" if goal else "Nova Meta Financeira")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #E8EAED;")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Nome
        self._name_edit = QLineEdit(goal.get("name", "") if goal else "")
        self._name_edit.setPlaceholderText("Ex: Aporte mensal R$ 2.000")
        self._name_edit.setToolTip("Nome descritivo da meta")
        form.addRow("Nome:", self._name_edit)

        # Tipo
        self._type_combo = QComboBox()
        for value, label in _GOAL_TYPES:
            self._type_combo.addItem(label, value)
        if goal:
            idx = next((i for i, (v, _) in enumerate(_GOAL_TYPES) if v == goal.get("type")), 0)
            self._type_combo.setCurrentIndex(idx)
        self._type_combo.setToolTip("Tipo da meta — define como o valor atual é calculado automaticamente")
        form.addRow("Tipo:", self._type_combo)

        # Valor alvo
        self._target_spin = QDoubleSpinBox()
        self._target_spin.setRange(0.0, 99_999_999.0)
        self._target_spin.setDecimals(2)
        self._target_spin.setSingleStep(500.0)
        self._target_spin.setPrefix("R$ ")
        self._target_spin.setFixedWidth(180)
        self._target_spin.setToolTip("Valor objetivo a atingir")
        if goal:
            self._target_spin.setValue(float(goal.get("target_value", 0)))
        form.addRow("Valor alvo:", self._target_spin)

        # Data limite (opcional)
        date_row = QHBoxLayout()
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("dd/MM/yyyy")
        self._date_edit.setDate(
            date.fromisoformat(goal["target_date"])
            if (goal and goal.get("target_date"))
            else date.today().replace(year=date.today().year + 1)
        )
        self._date_edit.setToolTip("Prazo para atingir a meta (opcional)")
        self._no_date_btn = QPushButton("Sem prazo")
        self._no_date_btn.setCheckable(True)
        self._no_date_btn.setChecked(not bool(goal and goal.get("target_date")))
        self._no_date_btn.setToolTip("Marcar para não definir prazo")
        self._no_date_btn.toggled.connect(lambda checked: self._date_edit.setDisabled(checked))
        self._date_edit.setDisabled(self._no_date_btn.isChecked())
        date_row.addWidget(self._date_edit)
        date_row.addWidget(self._no_date_btn)
        date_widget = QWidget()
        date_widget.setLayout(date_row)
        form.addRow("Prazo:", date_widget)

        # Ícone
        self._icon_combo = QComboBox()
        for icon_name, icon_label in _GOAL_ICONS:
            self._icon_combo.addItem(icon_label, icon_name)
        if goal:
            idx = next((i for i, (n, _) in enumerate(_GOAL_ICONS) if n == goal.get("icon")), 0)
            self._icon_combo.setCurrentIndex(idx)
        self._icon_combo.setToolTip("Ícone representativo da meta")
        form.addRow("Ícone:", self._icon_combo)

        root.addLayout(form)

        # Cor
        color_row = QHBoxLayout()
        color_label = QLabel("Cor:")
        color_label.setStyleSheet("color: #C8CAD8; font-size: 11px;")
        self._color_preview = QLabel()
        self._color_preview.setFixedSize(28, 28)
        self._color_preview.setStyleSheet(
            f"background: {self._color}; border-radius: 14px; border: 2px solid #3D4166;"
        )
        color_btn = QPushButton("Escolher cor")
        color_btn.setToolTip("Abrir seletor de cor para personalizar o card")
        color_btn.clicked.connect(self._pick_color)
        color_row.addWidget(color_label)
        color_row.addWidget(self._color_preview)
        color_row.addWidget(color_btn)
        color_row.addStretch()
        root.addLayout(color_row)

        # Botões
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(" Salvar")
        save_btn.setIcon(_svg_icon("save", "#0A0D14", 14))
        save_btn.setProperty("class", "primary")
        save_btn.clicked.connect(self._validate_and_accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _pick_color(self) -> None:
        from PyQt6.QtGui import QColor
        chosen = QColorDialog.getColor(QColor(self._color), self, "Escolher cor da meta")
        if chosen.isValid():
            self._color = chosen.name()
            self._color_preview.setStyleSheet(
                f"background: {self._color}; border-radius: 14px; border: 2px solid #3D4166;"
            )

    def _validate_and_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Digite um nome para a meta.")
            return
        self.accept()

    def get_goal_data(self) -> dict:
        target_date = None
        if not self._no_date_btn.isChecked():
            target_date = self._date_edit.date().toPyDate().isoformat()

        base = {
            "id":           self._editing["id"] if self._editing else str(uuid.uuid4()),
            "type":         self._type_combo.currentData(),
            "name":         self._name_edit.text().strip(),
            "target_value": self._target_spin.value(),
            "target_date":  target_date,
            "icon":         self._icon_combo.currentData(),
            "color":        self._color,
            "created_at":   self._editing.get("created_at", date.today().isoformat())
                            if self._editing else date.today().isoformat(),
        }
        return base


# ─── Página principal ──────────────────────────────────────────────────────

class GoalsPage(QWidget):
    """
    Página de metas financeiras com GoalCards, progresso automático e CRUD.
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: GoalValuesWorker | None = None
        self._goals: list[dict] = []
        self._current_values: dict[str, float] = {}
        self._cards: list[GoalCard] = []
        self._build_ui()
        self.load_data()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.load_data()

    # ── Build UI ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("dashboardContent")
        self._main = QVBoxLayout(content)
        self._main.setContentsMargins(32, 24, 32, 24)
        self._main.setSpacing(16)

        # Cabeçalho
        hdr = QHBoxLayout()
        title = QLabel("Minhas Metas Financeiras")
        title.setObjectName("sectionTitle")
        hdr.addWidget(title)
        hdr.addStretch()

        refresh_btn = QPushButton(" Atualizar")
        refresh_btn.setIcon(_svg_icon("refresh", "#FFFFFF", 14))
        refresh_btn.setProperty("class", "primary")
        refresh_btn.setToolTip("Buscar valores atuais da API")
        refresh_btn.clicked.connect(self.load_data)
        hdr.addWidget(refresh_btn)
        self._main.addLayout(hdr)

        # Loading label
        self._loading_lbl = QLabel("Carregando metas…")
        self._loading_lbl.setObjectName("loadingLabel")
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setVisible(False)
        self._main.addWidget(self._loading_lbl)

        # Container de cards (preenchido dinamicamente)
        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setSpacing(12)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._main.addWidget(self._cards_container)

        # Botão nova meta
        add_row = QHBoxLayout()
        add_btn = QPushButton(_svg_icon("add_circle", "#0A0D14", 16), " Nova Meta")
        add_btn.setProperty("class", "primary")
        add_btn.setMinimumWidth(160)
        add_btn.setToolTip("Criar uma nova meta financeira")
        add_btn.clicked.connect(self._on_add_goal)
        add_row.addWidget(add_btn)
        add_row.addStretch()
        self._main.addLayout(add_row)

        self._main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Carregamento ───────────────────────────────────────────────────────

    def load_data(self) -> None:
        self._goals = load_goals()
        self._render_cards()

        if not self._goals:
            return

        goal_types = {g["type"] for g in self._goals}
        if self._worker and self._worker.isRunning():
            return
        self._loading_lbl.setVisible(True)
        self._worker = GoalValuesWorker(self._client, goal_types)
        self._worker.data_ready.connect(self._on_values_ready)
        self._worker.error_occurred.connect(lambda _: self._loading_lbl.setVisible(False))
        self._worker.start()

    def _on_values_ready(self, values: dict[str, float]) -> None:
        self._current_values = values
        self._loading_lbl.setVisible(False)
        self._render_cards()

    def _render_cards(self) -> None:
        # Limpar cards anteriores
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        self._cards.clear()

        if not self._goals:
            empty = QLabel("Nenhuma meta cadastrada. Clique em '+ Nova Meta' para começar.")
            empty.setStyleSheet("color: #8B90A7; font-size: 13px; padding: 24px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._cards_layout.addWidget(empty)
            return

        for goal in self._goals:
            goal_type    = goal.get("type", "CUSTOM")
            current_val  = self._current_values.get(goal_type)
            # CUSTOM type: use stored current_value if present
            if goal_type == "CUSTOM" and "current_value" in goal:
                current_val = float(goal["current_value"])

            card = GoalCard(goal, current_val)
            card.edit_requested.connect(self._on_edit_goal)
            card.delete_requested.connect(self._on_delete_goal)
            self._cards_layout.addWidget(card)
            self._cards.append(card)

    # ── CRUD ───────────────────────────────────────────────────────────────

    def _on_add_goal(self) -> None:
        dlg = GoalDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            goal = dlg.get_goal_data()
            self._goals.append(goal)
            save_goals(self._goals)
            self.load_data()

    def _on_edit_goal(self, goal_id: str) -> None:
        goal = next((g for g in self._goals if g["id"] == goal_id), None)
        if not goal:
            return
        dlg = GoalDialog(parent=self, goal=goal)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_goal_data()
            idx = next(i for i, g in enumerate(self._goals) if g["id"] == goal_id)
            self._goals[idx] = updated
            save_goals(self._goals)
            self.load_data()

    def _on_delete_goal(self, goal_id: str) -> None:
        goal = next((g for g in self._goals if g["id"] == goal_id), None)
        if not goal:
            return
        reply = QMessageBox.question(
            self,
            "Excluir meta",
            f"Excluir a meta '{goal['name']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._goals = [g for g in self._goals if g["id"] != goal_id]
            save_goals(self._goals)
            self._render_cards()
