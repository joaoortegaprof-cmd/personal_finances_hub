"""
Página de Configurações e Metas — parâmetros do aplicativo e metas financeiras.

Layout:
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Metas Financeiras                                                   │
  │  Taxa de poupança:        [____]%                                    │
  │  Renda mensal estimada:   R$ [_______]                               │
  │  Meta de fundo emergência: [____] meses de despesas                 │
  ├──────────────────────────────────────────────────────────────────────┤
  │  Alertas                                                             │
  │  Avisar sobre fatura com:  [___] dias de antecedência               │
  │  Avisar sobre vencimentos: [___] dias à frente                      │
  │  Meta de taxa de poupança: [____]%                                   │
  ├──────────────────────────────────────────────────────────────────────┤
  │  [Salvar Configurações]                                              │
  └──────────────────────────────────────────────────────────────────────┘

Persistência: data/settings.json (criado automaticamente se não existir).
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from frontend.components.icons import icon as _svg_icon
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Caminho do arquivo de configurações — relativo à raiz do projeto
_SETTINGS_PATH = Path(__file__).parent.parent.parent / "data" / "settings.json"

# Valores padrão usados na primeira execução ou se o arquivo estiver corrompido
_DEFAULTS: dict = {
    "savings_rate_goal_pct":   20.0,
    "monthly_income_estimate": 0.0,
    "emergency_fund_months":   6,
    "invoice_alert_days":      3,
    "maturity_alert_days":     30,
    "savings_alert_pct":       15.0,
    "debt_alert_days":         3,
    "recurring_alert_days":    7,
    "min_liquidity":           0.0,
    # Alertas habilitados — lista de alert_type.value; None = todos
    "enabled_alerts": None,
}

# Todos os tipos de alerta disponíveis com rótulo amigável
_ALL_ALERT_TYPES = [
    ("darf",             "DARF de renda variável"),
    ("fatura_vencendo",  "Fatura de cartão vencendo"),
    ("renda_fixa",       "Vencimento de renda fixa/Tesouro"),
    ("taxa_poupanca",    "Taxa de poupança abaixo da meta"),
    ("come_cotas",       "Come-cotas de fundos (lembrete)"),
    ("parcela_divida",   "Parcela de dívida vencendo"),
    ("recorrente",       "Gasto recorrente vencendo"),
    ("liquidez_baixa",   "Liquidez imediata abaixo do mínimo"),
    ("rebalanceamento",  "Ativo fora da alocação-alvo"),
    ("juros_altos",      "Juros de dívida acima do benchmark"),
]


def load_settings() -> dict:
    """Lê as configurações do arquivo JSON. Retorna defaults se o arquivo não existir."""
    try:
        if _SETTINGS_PATH.exists():
            data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            # Mescla com defaults para garantir que campos novos existam
            return {**_DEFAULTS, **data}
    except (json.JSONDecodeError, OSError):
        pass
    return dict(_DEFAULTS)


def save_settings(settings: dict) -> None:
    """Persiste as configurações no arquivo JSON, criando o diretório se necessário."""
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ======================================================================
# Página de Configurações
# ======================================================================


class SettingsPage(QWidget):
    """
    Página de metas financeiras e parâmetros de alertas.

    Lê as configurações atuais ao ser exibida e salva no JSON ao clicar
    em "Salvar". Não depende da API — é puramente local.
    """

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # UI
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
        main.setContentsMargins(32, 24, 32, 24)
        main.setSpacing(24)

        # --- Título ---
        title = QLabel("Configurações e Metas")
        title.setObjectName("sectionTitle")
        main.addWidget(title)

        # --- Metas Financeiras ---
        goals_box = self._build_group("Metas Financeiras")
        goals_form = QFormLayout()
        goals_form.setSpacing(14)
        goals_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._savings_goal = _pct_spin()
        self._savings_goal.setValue(20.0)
        goals_form.addRow("Taxa de poupança desejada:", self._savings_goal)

        self._income_estimate = _money_spin()
        goals_form.addRow("Renda mensal estimada:", self._income_estimate)

        self._emergency_months = _int_spin(1, 36, 6)
        goals_form.addRow("Fundo de emergência (meses de despesas):", self._emergency_months)

        goals_box.layout().addLayout(goals_form)
        main.addWidget(goals_box)

        # --- Alertas — Limites ---
        alerts_box = self._build_group("Limites de Alerta")
        alerts_form = QFormLayout()
        alerts_form.setSpacing(14)
        alerts_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._invoice_days = _int_spin(1, 30, 3)
        alerts_form.addRow("Avisar fatura do cartão com antecedência de:", _with_unit(self._invoice_days, "dias"))

        self._maturity_days = _int_spin(1, 365, 30)
        alerts_form.addRow("Alertar vencimentos nos próximos:", _with_unit(self._maturity_days, "dias"))

        self._savings_alert = _pct_spin()
        self._savings_alert.setValue(15.0)
        alerts_form.addRow("Alertar se taxa de poupança ficar abaixo de:", self._savings_alert)

        self._debt_days = _int_spin(1, 30, 3)
        alerts_form.addRow("Alertar parcelas de dívidas nos próximos:", _with_unit(self._debt_days, "dias"))

        self._recurring_days = _int_spin(1, 30, 7)
        alerts_form.addRow("Alertar recorrentes nos próximos:", _with_unit(self._recurring_days, "dias"))

        self._min_liquidity = _money_spin()
        alerts_form.addRow("Mínimo de liquidez imediata (D+0):", self._min_liquidity)

        alerts_box.layout().addLayout(alerts_form)
        main.addWidget(alerts_box)

        # --- Alertas — Tipos ativos ---
        enabled_box = self._build_group("Tipos de Alerta Ativos")
        enabled_lay = QVBoxLayout()
        enabled_lay.setSpacing(8)
        self._alert_checks: dict[str, QCheckBox] = {}
        for value, label in _ALL_ALERT_TYPES:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("color: #C8CAD8; font-size: 13px;")
            self._alert_checks[value] = cb
            enabled_lay.addWidget(cb)
        enabled_box.layout().addLayout(enabled_lay)
        main.addWidget(enabled_box)

        # --- Rodapé informativo ---
        info = QLabel(
            "As configurações são salvas localmente e usadas nos alertas exibidos no Dashboard."
        )
        info.setStyleSheet("color: #8B90A7; font-size: 12px;")
        info.setWordWrap(True)
        main.addWidget(info)

        # --- Botão salvar ---
        btn_row = QHBoxLayout()
        save_btn = QPushButton(" Salvar Configurações")
        save_btn.setIcon(_svg_icon("save", "#FFFFFF", 14))
        save_btn.setProperty("class", "primary")
        save_btn.style().unpolish(save_btn)
        save_btn.style().polish(save_btn)
        save_btn.setFixedWidth(220)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        self._saved_label = QLabel("")
        self._saved_label.setStyleSheet("color: #00C896; font-size: 13px;")
        btn_row.addWidget(self._saved_label)
        btn_row.addStretch()
        main.addLayout(btn_row)

        main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_group(self, title: str) -> QGroupBox:
        box = QGroupBox(title)
        box.setStyleSheet(
            "QGroupBox { color: #E8EAED; border: 1px solid #2E3250; border-radius: 8px;"
            " margin-top: 8px; padding: 16px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #8B90A7;"
            " font-size: 13px; font-weight: 600; }"
        )
        layout = QVBoxLayout(box)
        layout.setSpacing(0)
        return box

    # ------------------------------------------------------------------
    # Leitura e escrita
    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        cfg = load_settings()
        self._savings_goal.setValue(cfg.get("savings_rate_goal_pct", 20.0))
        self._income_estimate.setValue(cfg.get("monthly_income_estimate", 0.0))
        self._emergency_months.setValue(cfg.get("emergency_fund_months", 6))
        self._invoice_days.setValue(cfg.get("invoice_alert_days", 3))
        self._maturity_days.setValue(cfg.get("maturity_alert_days", 30))
        self._savings_alert.setValue(cfg.get("savings_alert_pct", 15.0))
        self._debt_days.setValue(cfg.get("debt_alert_days", 3))
        self._recurring_days.setValue(cfg.get("recurring_alert_days", 7))
        self._min_liquidity.setValue(cfg.get("min_liquidity", 0.0))
        enabled = cfg.get("enabled_alerts")  # None = todos
        for value, cb in self._alert_checks.items():
            cb.setChecked(enabled is None or value in enabled)

    def _save(self) -> None:
        enabled = [v for v, cb in self._alert_checks.items() if cb.isChecked()]
        # None means all enabled; using explicit list only if some are unchecked
        enabled_val = None if len(enabled) == len(_ALL_ALERT_TYPES) else ",".join(enabled)

        cfg = {
            "savings_rate_goal_pct":   self._savings_goal.value(),
            "monthly_income_estimate": self._income_estimate.value(),
            "emergency_fund_months":   self._emergency_months.value(),
            "invoice_alert_days":      self._invoice_days.value(),
            "maturity_alert_days":     self._maturity_days.value(),
            "savings_alert_pct":       self._savings_alert.value(),
            "debt_alert_days":         self._debt_days.value(),
            "recurring_alert_days":    self._recurring_days.value(),
            "min_liquidity":           self._min_liquidity.value(),
            "enabled_alerts":          enabled_val,
        }
        try:
            save_settings(cfg)
            self._saved_label.setText("✓ Configurações salvas!")
        except OSError as exc:
            QMessageBox.critical(self, "Erro ao salvar", str(exc))


# ======================================================================
# Helpers de widgets
# ======================================================================


def _pct_spin() -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 100.0)
    spin.setDecimals(1)
    spin.setSuffix(" %")
    spin.setSingleStep(1.0)
    spin.setFixedWidth(130)
    return spin


def _money_spin() -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 9_999_999.99)
    spin.setDecimals(2)
    spin.setPrefix("R$ ")
    spin.setSingleStep(500.0)
    spin.setFixedWidth(180)
    return spin


def _int_spin(min_val: int, max_val: int, default: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(min_val, max_val)
    spin.setValue(default)
    spin.setFixedWidth(100)
    return spin


def _with_unit(widget: QWidget, unit: str) -> QWidget:
    """Envolve um widget em um layout horizontal com um label de unidade."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(widget)
    lbl = QLabel(unit)
    lbl.setStyleSheet("color: #8B90A7;")
    layout.addWidget(lbl)
    layout.addStretch()
    return container
