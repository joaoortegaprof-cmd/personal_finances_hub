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

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from frontend.components.api_client import ApiClient, ApiError
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
    QProgressBar,
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


# ======================================================================
# Worker de saúde financeira
# ======================================================================


class HealthScoreWorker(QThread):
    """
    Calcula o score de saúde financeira (0–100) consultando a API:
      - Taxa de poupança real do mês corrente (dashboard)
      - Cobertura do fundo de emergência (emergency-fund)
      - Carga de dívidas (debt payments / income)
    """

    data_ready     = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, settings: dict) -> None:
        super().__init__()
        self._client   = client
        self._settings = settings

    def run(self) -> None:
        try:
            result: dict = {}

            # 1. Dashboard → patrimônio e summary do mês
            try:
                dash = self._client.get_dashboard()
                nw   = float(dash.get("net_worth", {}).get("net_worth", 0))
                result["net_worth"] = nw

                monthly = dash.get("monthly_summary", {})
                income  = float(monthly.get("income",  0))
                expense = float(monthly.get("expense", monthly.get("expenses", 0)))
                result["income"]  = income
                result["expense"] = expense
                # Usa savings_rate já calculado pela API quando disponível
                api_sr = monthly.get("savings_rate")
                result["savings_rate"] = float(api_sr) if api_sr is not None else (
                    (income - expense) / income * 100 if income > 0 else 0.0
                )
            except Exception:
                result.setdefault("savings_rate", 0.0)

            # 2. Fundo de emergência
            try:
                ef = self._client.get_emergency_fund()
                # API retorna: saldo_total, media_gastos_6m, meses_cobertos
                result["ef_months_covered"] = float(
                    ef.get("meses_cobertos") or ef.get("months_covered", 0)
                )
                result["ef_balance"] = float(
                    ef.get("saldo_total") or ef.get("emergency_fund_balance", 0)
                )
                monthly_expenses = float(
                    ef.get("media_gastos_6m") or ef.get("monthly_expenses", 0)
                )
                result["ef_target"] = monthly_expenses * self._settings.get("emergency_fund_months", 6)
            except Exception:
                result.setdefault("ef_months_covered", 0.0)

            # 3. Dívidas
            try:
                debts = self._client.get_debts()
                total_debt = sum(float(d.get("remaining_balance", 0)) for d in debts)
                result["total_debt"] = total_debt
                result["debt_count"] = len(debts)
            except Exception:
                result.setdefault("total_debt", 0.0)

            # Score composto (0–100)
            score = 0

            # Poupança — peso 40 pts
            sr   = result.get("savings_rate", 0)
            goal = self._settings.get("savings_rate_goal_pct", 20)
            score += min(40, int(sr / max(goal, 1) * 40))

            # Fundo de emergência — peso 35 pts
            ef_months  = result.get("ef_months_covered", 0)
            ef_target  = self._settings.get("emergency_fund_months", 6)
            score += min(35, int(ef_months / max(ef_target, 1) * 35))

            # Sem dívidas — peso 25 pts
            nw    = result.get("net_worth", 0)
            debt  = result.get("total_debt", 0)
            debt_ratio = (debt / nw) if nw > 0 else (1.0 if debt > 0 else 0.0)
            score += max(0, 25 - int(debt_ratio * 25))

            result["score"] = min(100, max(0, score))
            self.data_ready.emit(result)
        except Exception as exc:
            self.error_occurred.emit(f"Erro: {exc}")


# ======================================================================
# Painel de score de saúde financeira
# ======================================================================


class HealthScorePanel(QFrame):
    """
    Card visual no topo de Configurações mostrando o score de saúde
    financeira (0–100) e três barras de progresso contextuais.
    """

    _SCORE_COLORS = [
        (80, "#00C896"),   # ≥ 80 → verde
        (50, "#FFB347"),   # ≥ 50 → laranja
        (0,  "#FF6B6B"),   # < 50 → vermelho
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("summaryCard")
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(32)

        # ── Coluna esquerda: score circular (simulado com label grande) ──
        score_col = QVBoxLayout()
        score_col.setAlignment(Qt.AlignmentFlag.AlignCenter)

        score_title = QLabel("SAÚDE FINANCEIRA")
        score_title.setStyleSheet(
            "color: #8B90A7; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        score_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_col.addWidget(score_title)

        self._score_lbl = QLabel("—")
        self._score_lbl.setStyleSheet(
            "color: #4A9EFF; font-size: 52px; font-weight: 800;"
        )
        self._score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_col.addWidget(self._score_lbl)

        self._score_sub = QLabel("calculando…")
        self._score_sub.setStyleSheet("color: #8B90A7; font-size: 11px;")
        self._score_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_col.addWidget(self._score_sub)

        outer.addLayout(score_col)

        # Separador vertical
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #2E3250;")
        outer.addWidget(sep)

        # ── Coluna direita: barras de progresso ──
        bars_col = QVBoxLayout()
        bars_col.setSpacing(14)

        # Taxa de poupança
        sr_lay, self._sr_bar, self._sr_lbl = self._make_bar_row(
            "Taxa de poupança", "#00C896"
        )
        bars_col.addLayout(sr_lay)

        # Fundo de emergência
        ef_lay, self._ef_bar, self._ef_lbl = self._make_bar_row(
            "Fundo de emergência", "#4A9EFF"
        )
        bars_col.addLayout(ef_lay)

        # Dívidas (inversa: menos dívida = melhor)
        dbt_lay, self._dbt_bar, self._dbt_lbl = self._make_bar_row(
            "Sem dívidas", "#FFB347"
        )
        bars_col.addLayout(dbt_lay)

        outer.addLayout(bars_col, stretch=1)

    def _make_bar_row(self, label: str, color: str
                      ) -> tuple[QVBoxLayout, QProgressBar, QLabel]:
        lay = QVBoxLayout()
        lay.setSpacing(3)

        header = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #C8CAD8; font-size: 11px;")
        val_lbl = QLabel("—")
        val_lbl.setStyleSheet("color: #8B90A7; font-size: 11px;")
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(lbl)
        header.addStretch()
        header.addWidget(val_lbl)
        lay.addLayout(header)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background: #2A2E4A;
                border-radius: 4px;
                border: none;
            }}
            QProgressBar::chunk {{
                background: {color};
                border-radius: 4px;
            }}
        """)
        lay.addWidget(bar)
        return lay, bar, val_lbl

    def update_data(self, data: dict, settings: dict) -> None:
        """Atualiza o score e as barras de progresso com os dados da API."""
        score = data.get("score", 0)

        # Cor do score por faixa
        color = "#FF6B6B"
        label = "Crítico"
        for threshold, clr in self._SCORE_COLORS:
            if score >= threshold:
                color = clr
                label = ("Excelente" if score >= 80 else
                         "Bom" if score >= 65 else
                         "Regular" if score >= 50 else "Atenção")
                break

        self._score_lbl.setText(str(score))
        self._score_lbl.setStyleSheet(
            f"color: {color}; font-size: 52px; font-weight: 800;"
        )
        self._score_sub.setText(label)
        self._score_sub.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")

        # Barra 1 — poupança
        sr   = max(0.0, data.get("savings_rate", 0.0))
        goal = max(1.0, settings.get("savings_rate_goal_pct", 20))
        sr_pct = min(100, int(sr / goal * 100))
        self._sr_bar.setValue(sr_pct)
        self._sr_lbl.setText(f"{sr:.1f}% / meta {goal:.0f}%")

        # Barra 2 — fundo de emergência
        ef_months  = data.get("ef_months_covered", 0.0)
        ef_target  = max(1.0, settings.get("emergency_fund_months", 6))
        ef_pct = min(100, int(ef_months / ef_target * 100))
        self._ef_bar.setValue(ef_pct)
        self._ef_lbl.setText(f"{ef_months:.1f} / {ef_target:.0f} meses")

        # Barra 3 — saúde de dívidas (inversamente proporcional à carga)
        nw   = max(1.0, data.get("net_worth", 1.0))
        debt = data.get("total_debt", 0.0)
        debt_health = max(0, 100 - int(debt / nw * 100))
        self._dbt_bar.setValue(debt_health)
        debt_txt = (f"R$ {debt:,.0f} em aberto".replace(",", ".") if debt > 0 else "Sem dívidas ✓")
        self._dbt_lbl.setText(debt_txt)


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
        self._client = ApiClient()
        self._health_worker: HealthScoreWorker | None = None
        self._build_ui()
        self._load_values()
        # Score: carrega 400ms após a UI estar pronta
        QTimer.singleShot(400, self._refresh_health)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_health()

    def _refresh_health(self) -> None:
        if self._health_worker and self._health_worker.isRunning():
            return
        cfg = load_settings()
        self._health_worker = HealthScoreWorker(self._client, cfg)
        self._health_worker.data_ready.connect(
            lambda d: self._health_panel.update_data(d, load_settings())
        )
        self._health_worker.error_occurred.connect(lambda _: None)
        self._health_worker.start()

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

        # --- Score de saúde financeira ---
        self._health_panel = HealthScorePanel()
        self._health_panel.setMinimumHeight(140)
        main.addWidget(self._health_panel)

        # --- Metas Financeiras ---
        goals_box = self._build_group("Metas Financeiras")
        goals_form = QFormLayout()
        goals_form.setSpacing(14)
        goals_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._savings_goal = _pct_spin()
        self._savings_goal.setValue(20.0)
        self._savings_goal.setToolTip("Percentual da renda que você deseja poupar mensalmente")
        goals_form.addRow("Taxa de poupança desejada:", self._savings_goal)

        self._income_estimate = _money_spin()
        self._income_estimate.setToolTip("Renda mensal bruta estimada usada como referência nos cálculos")
        goals_form.addRow("Renda mensal estimada:", self._income_estimate)

        self._emergency_months = _int_spin(1, 36, 6)
        self._emergency_months.setToolTip("Quantidade de meses de despesas que você deseja ter de reserva (recomendado: 6)")
        goals_form.addRow("Fundo de emergência (meses de despesas):", self._emergency_months)

        goals_box.layout().addLayout(goals_form)
        main.addWidget(goals_box)

        # --- Alertas — Limites ---
        alerts_box = self._build_group("Limites de Alerta")
        alerts_form = QFormLayout()
        alerts_form.setSpacing(14)
        alerts_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._invoice_days = _int_spin(1, 30, 3)
        self._invoice_days.setToolTip("Quantos dias antes do vencimento da fatura você quer ser avisado")
        alerts_form.addRow("Avisar fatura do cartão com antecedência de:", _with_unit(self._invoice_days, "dias"))

        self._maturity_days = _int_spin(1, 365, 30)
        self._maturity_days.setToolTip("Janela futura (em dias) para alertas de vencimento de renda fixa e Tesouro")
        alerts_form.addRow("Alertar vencimentos nos próximos:", _with_unit(self._maturity_days, "dias"))

        self._savings_alert = _pct_spin()
        self._savings_alert.setValue(15.0)
        self._savings_alert.setToolTip("Alerta é disparado quando a taxa de poupança real cair abaixo deste valor")
        alerts_form.addRow("Alertar se taxa de poupança ficar abaixo de:", self._savings_alert)

        self._debt_days = _int_spin(1, 30, 3)
        self._debt_days.setToolTip("Quantos dias antes do vencimento de uma parcela de dívida você quer ser alertado")
        alerts_form.addRow("Alertar parcelas de dívidas nos próximos:", _with_unit(self._debt_days, "dias"))

        self._recurring_days = _int_spin(1, 30, 7)
        self._recurring_days.setToolTip("Janela para alertas de gastos recorrentes (assinaturas, planos fixos)")
        alerts_form.addRow("Alertar recorrentes nos próximos:", _with_unit(self._recurring_days, "dias"))

        self._min_liquidity = _money_spin()
        self._min_liquidity.setToolTip("Valor mínimo que você quer manter disponível em liquidez imediata (D+0)")
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

        # --- Botões de ação ---
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        save_btn = QPushButton(" Salvar Configurações")
        save_btn.setIcon(_svg_icon("save", "#FFFFFF", 14))
        save_btn.setProperty("class", "primary")
        save_btn.style().unpolish(save_btn)
        save_btn.style().polish(save_btn)
        save_btn.setMinimumHeight(36)
        save_btn.setMinimumWidth(200)
        save_btn.setToolTip("Salvar todas as configurações no arquivo data/settings.json")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        reset_btn = QPushButton(" Redefinir Padrões")
        reset_btn.setIcon(_svg_icon("refresh", "#C8CAD8", 14))
        reset_btn.setMinimumHeight(36)
        reset_btn.setMinimumWidth(180)
        reset_btn.setToolTip("Restaurar todos os valores para as configurações padrão")
        reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(reset_btn)

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

    def _reset_defaults(self) -> None:
        reply = QMessageBox.question(
            self,
            "Redefinir padrões",
            "Restaurar todas as configurações para os valores padrão?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for k, v in _DEFAULTS.items():
            pass  # _load_values já lê do JSON; aqui forçamos os defaults
        # Aplica defaults diretamente nos widgets
        self._savings_goal.setValue(_DEFAULTS["savings_rate_goal_pct"])
        self._income_estimate.setValue(_DEFAULTS["monthly_income_estimate"])
        self._emergency_months.setValue(_DEFAULTS["emergency_fund_months"])
        self._invoice_days.setValue(_DEFAULTS["invoice_alert_days"])
        self._maturity_days.setValue(_DEFAULTS["maturity_alert_days"])
        self._savings_alert.setValue(_DEFAULTS["savings_alert_pct"])
        self._debt_days.setValue(_DEFAULTS["debt_alert_days"])
        self._recurring_days.setValue(_DEFAULTS["recurring_alert_days"])
        self._min_liquidity.setValue(_DEFAULTS["min_liquidity"])
        for cb in self._alert_checks.values():
            cb.setChecked(True)
        self._saved_label.setText("↺ Padrões restaurados — clique em Salvar para persistir.")

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
            QTimer.singleShot(100, self._refresh_health)
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
