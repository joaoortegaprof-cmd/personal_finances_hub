"""
Página de Relatórios — geração de relatórios financeiros em PDF.

Layout:
  ┌─────────────────────────────────────────────────────────────────┐
  │  Tipo:   ○ Lançamentos  ○ Resumo Mensal  ○ Investimentos        │
  │  Período: [data início]  até  [data fim]                        │
  │  [Gerar Relatório]                                              │
  ├─────────────────────────────────────────────────────────────────┤
  │  Pré-visualização do relatório (QTextEdit somente-leitura)      │
  └─────────────────────────────────────────────────────────────────┘

Geração de PDF:
  Usa QPrinter + QTextDocument — sem dependências externas.
  O relatório é formatado em HTML e impresso via QPrinter.PrintToPdf.

Threading:
  ReportWorker busca os dados necessários em background antes de
  montar o relatório, mantendo a UI responsiva.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

import base64
import io
from collections import defaultdict

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QTextDocument
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog, QPageSetupDialog
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.icons import icon as _svg_icon


# ======================================================================
# Worker de busca de dados
# ======================================================================


class ReportWorker(QThread):
    """Busca dados necessários para o relatório em background."""

    data_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        client: ApiClient,
        report_type: str,
        start_date: date,
        end_date: date,
    ) -> None:
        super().__init__()
        self._client = client
        self._report_type = report_type
        self._start = start_date
        self._end = end_date

    def run(self) -> None:
        try:
            data: dict[str, Any] = {
                "report_type": self._report_type,
                "start_date": self._start.isoformat(),
                "end_date": self._end.isoformat(),
            }

            if self._report_type in ("lancamentos", "resumo"):
                data["transactions"] = self._client.get_transactions(
                    start_date=self._start, end_date=self._end
                )
                data["accounts"] = self._client.get_accounts()

            if self._report_type == "investimentos":
                data["assets"] = self._client.get_assets()
                data["portfolio"] = self._client.get_portfolio_summary()

            if self._report_type == "padrão_gastos":
                # Usa o último dia do período como referência para o mês
                data["patterns"] = self._client.get_expense_patterns(
                    reference_date=self._end.isoformat()
                )

            if self._report_type == "custo_oportunidade":
                data["opportunity"] = self._client.get_opportunity_cost()
                data["assets"] = self._client.get_assets()

            self.data_ready.emit(data)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Página de Relatórios
# ======================================================================

_REPORT_TYPES = [
    ("lancamentos",       "Lançamentos do período"),
    ("resumo",            "Resumo mensal"),
    ("investimentos",     "Carteira de investimentos"),
    ("padrão_gastos",     "Padrão de gastos"),
    ("custo_oportunidade","Custo de oportunidade"),
]


class ReportsPage(QWidget):
    """
    Página de geração de relatórios financeiros.

    Ciclo de vida:
      __init__ → _build_ui
      [Gerar] → ReportWorker → _on_data_ready → _render_preview
      [Salvar PDF] → _export_pdf
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: ReportWorker | None = None
        self._current_html = ""
        self._first_load = True

        # Debounce: aguarda 800 ms após a última mudança antes de gerar
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(800)
        self._debounce_timer.timeout.connect(self._generate)

        self._build_ui()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._first_load:
            self._first_load = False
            # Dispara a geração inicial com 200 ms de atraso para a UI terminar de montar
            QTimer.singleShot(200, self._generate)

    def load_data(self) -> None:
        """Compatibilidade com o botão 'Atualizar' da sidebar."""
        self._generate()

    def _schedule_generate(self) -> None:
        """Reinicia o timer de debounce a cada mudança de tipo ou período."""
        self._debounce_timer.start()

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
        main.setSpacing(20)

        # --- Título ---
        title = QLabel("Relatórios Financeiros")
        title.setObjectName("sectionTitle")
        main.addWidget(title)

        # --- Configurações do relatório ---
        config_box = QGroupBox("Configurações")
        config_box.setStyleSheet(
            "QGroupBox { color: #E8EAED; border: 1px solid #2E3250; border-radius: 8px;"
            " margin-top: 8px; padding: 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #8B90A7; }"
        )
        config_layout = QVBoxLayout(config_box)
        config_layout.setSpacing(16)

        # Tipo de relatório
        type_row = QHBoxLayout()
        type_row.setSpacing(24)
        type_label = QLabel("Tipo:")
        type_label.setFixedWidth(60)
        type_label.setStyleSheet("color: #8B90A7;")
        type_row.addWidget(type_label)

        self._type_group = QButtonGroup(self)
        for i, (key, label) in enumerate(_REPORT_TYPES):
            rb = QRadioButton(label)
            rb.setProperty("report_key", key)
            if i == 0:
                rb.setChecked(True)
            rb.toggled.connect(lambda checked: self._schedule_generate() if checked else None)
            self._type_group.addButton(rb, i)
            type_row.addWidget(rb)
        type_row.addStretch()
        config_layout.addLayout(type_row)

        # Período
        period_row = QHBoxLayout()
        period_row.setSpacing(12)
        period_label = QLabel("Período:")
        period_label.setFixedWidth(60)
        period_label.setStyleSheet("color: #8B90A7;")
        period_row.addWidget(period_label)

        today = date.today()
        first_of_month = date(today.year, today.month, 1)

        from PyQt6.QtCore import QDate
        self._start_date = QDateEdit()
        self._start_date.setCalendarPopup(True)
        self._start_date.setDisplayFormat("dd/MM/yyyy")
        self._start_date.setDate(QDate(first_of_month.year, first_of_month.month, 1))
        self._start_date.dateChanged.connect(self._schedule_generate)
        period_row.addWidget(self._start_date)

        period_row.addWidget(QLabel("até"))

        self._end_date = QDateEdit()
        self._end_date.setCalendarPopup(True)
        self._end_date.setDisplayFormat("dd/MM/yyyy")
        last_day = calendar.monthrange(today.year, today.month)[1]
        self._end_date.setDate(QDate(today.year, today.month, last_day))
        self._end_date.dateChanged.connect(self._schedule_generate)
        period_row.addWidget(self._end_date)

        period_row.addStretch()
        config_layout.addLayout(period_row)

        # Botões
        btn_row = QHBoxLayout()
        gen_btn = QPushButton(" Gerar Relatório")
        gen_btn.setIcon(_svg_icon("chart", "#FFFFFF", 14))
        gen_btn.setProperty("class", "primary")
        gen_btn.style().unpolish(gen_btn)
        gen_btn.style().polish(gen_btn)
        gen_btn.clicked.connect(self._generate)
        btn_row.addWidget(gen_btn)

        self._export_btn = QPushButton(" Salvar em PDF")
        self._export_btn.setIcon(_svg_icon("export", "#C8CAD8", 14))
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_pdf)
        btn_row.addWidget(self._export_btn)

        btn_row.addStretch()
        config_layout.addLayout(btn_row)
        main.addWidget(config_box)

        # --- Status ---
        self._status_label = QLabel("")
        self._status_label.setObjectName("loadingLabel")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setVisible(False)
        main.addWidget(self._status_label)

        # --- Preview ---
        preview_label = QLabel("Pré-visualização")
        preview_label.setObjectName("sectionTitle")
        main.addWidget(preview_label)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMinimumHeight(400)
        self._preview.setStyleSheet(
            "QTextEdit { background: #1A1D27; color: #E8EAED; border: 1px solid #2E3250;"
            " border-radius: 8px; padding: 16px; font-family: 'Courier New', monospace; }"
        )
        self._preview.setPlaceholderText("O relatório aparecerá aqui após a geração…")
        main.addWidget(self._preview)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # Geração
    # ------------------------------------------------------------------

    def _generate(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        # Identifica tipo selecionado
        checked_btn = self._type_group.checkedButton()
        report_key = checked_btn.property("report_key") if checked_btn else "lancamentos"

        sd = self._start_date.date()
        ed = self._end_date.date()
        start = date(sd.year(), sd.month(), sd.day())
        end = date(ed.year(), ed.month(), ed.day())

        if end < start:
            QMessageBox.warning(self, "Período inválido", "A data fim deve ser maior que a data início.")
            return

        self._status_label.setText("Buscando dados…")
        self._status_label.setVisible(True)
        self._export_btn.setEnabled(False)

        self._worker = ReportWorker(self._client, report_key, start, end)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_data_ready(self, data: dict) -> None:
        self._status_label.setVisible(False)
        html = self._build_html(data)
        self._current_html = html
        self._preview.setHtml(html)
        self._export_btn.setEnabled(True)

    def _on_error(self, message: str) -> None:
        self._status_label.setText(f"Erro: {message}")
        self._export_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Construção do HTML do relatório
    # ------------------------------------------------------------------

    def _build_html(self, data: dict) -> str:
        report_type = data.get("report_type", "lancamentos")
        start_date = data.get("start_date", "")
        end_date = data.get("end_date", "")

        period_label = f"{_fmt_date(start_date)} a {_fmt_date(end_date)}"
        generated_at = date.today().strftime("%d/%m/%Y")

        css = """
        body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px;
               color: #1a1a2e; margin: 32px; }
        h1 { color: #1a3a6e; font-size: 22px; border-bottom: 2px solid #1a3a6e;
             padding-bottom: 8px; }
        h2 { color: #1a3a6e; font-size: 16px; margin-top: 24px; }
        .meta { color: #555; font-size: 12px; margin-bottom: 24px; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; }
        th { background: #1a3a6e; color: white; padding: 8px 12px;
             text-align: left; font-size: 12px; }
        td { padding: 7px 12px; border-bottom: 1px solid #e0e0e0; font-size: 12px; }
        tr:nth-child(even) { background: #f5f7fa; }
        .amount { text-align: right; font-family: monospace; }
        .receita { color: #0a7a4a; font-weight: bold; }
        .despesa { color: #b71c1c; font-weight: bold; }
        .summary-box { background: #f0f4ff; border: 1px solid #c5cae9;
                       border-radius: 6px; padding: 16px; margin-top: 24px; }
        .summary-item { display: inline-block; margin-right: 40px; }
        .summary-label { font-size: 11px; color: #555; }
        .summary-value { font-size: 18px; font-weight: bold; color: #1a3a6e; }
        .footer { color: #888; font-size: 11px; margin-top: 32px;
                  border-top: 1px solid #ddd; padding-top: 8px; }
        """

        if report_type == "lancamentos":
            body = self._build_transactions_body(data)
            title = "Relatório de Lançamentos"
        elif report_type == "resumo":
            body = self._build_summary_body(data)
            title = "Resumo Financeiro do Período"
        elif report_type == "investimentos":
            body = self._build_investments_body(data)
            title = "Carteira de Investimentos"
        elif report_type == "padrão_gastos":
            body = self._build_expense_pattern_body(data)
            title = "Padrão de Gastos — Anomalias e Tendências"
        else:
            body = self._build_opportunity_cost_body(data)
            title = "Custo de Oportunidade"

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{css}</style></head>
<body>
<h1>FinanceHub — {title}</h1>
<div class="meta">Período: {period_label} &nbsp;|&nbsp; Gerado em: {generated_at}</div>
{body}
<div class="footer">FinanceHub — Relatório gerado automaticamente</div>
</body>
</html>"""

    def _build_transactions_body(self, data: dict) -> str:
        transactions = data.get("transactions", [])
        accounts = {a["id"]: a["name"] for a in data.get("accounts", [])}

        if not transactions:
            return "<p>Nenhum lançamento encontrado no período.</p>"

        income = sum(float(t["amount"]) for t in transactions if t.get("transaction_type") == "income")
        expense = sum(float(t["amount"]) for t in transactions if t.get("transaction_type") in ("debit", "credit", "investment"))
        balance = income - expense

        rows = ""
        for t in sorted(transactions, key=lambda x: x.get("transaction_date", "")):
            tx_type = t.get("transaction_type", "")
            amount = float(t.get("amount", 0))
            cls = "receita" if tx_type == "income" else "despesa" if tx_type in ("debit", "credit", "investment") else ""
            sign = "+" if tx_type == "income" else "-" if tx_type in ("debit", "credit", "investment") else ""
            acc_name = accounts.get(t.get("account_id"), "—")
            rows += f"""<tr>
              <td>{_fmt_date(t.get('transaction_date',''))}</td>
              <td>{t.get('description','')}</td>
              <td>{acc_name}</td>
              <td class="amount {cls}">{sign}R$ {amount:,.2f}</td>
            </tr>""".replace(",", "X").replace(".", ",").replace("X", ".")

        summary = f"""<div class="summary-box">
          <div class="summary-item">
            <div class="summary-label">Receitas</div>
            <div class="summary-value receita">R$ {income:,.2f}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Despesas</div>
            <div class="summary-value despesa">R$ {expense:,.2f}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Saldo</div>
            <div class="summary-value">R$ {balance:,.2f}</div>
          </div>
        </div>""".replace(",", "X").replace(".", ",").replace("X", ".")

        return f"""
        {summary}
        <h2>Detalhamento ({len(transactions)} lançamentos)</h2>
        <table>
          <thead><tr><th>Data</th><th>Descrição</th><th>Conta</th><th>Valor</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    def _make_summary_chart_html(self, transactions: list[dict]) -> str:
        """
        Gera um gráfico de barras mensais (receitas × despesas) como PNG base64
        e retorna a tag <img> para embutir no HTML do relatório.

        Usa FigureCanvasAgg diretamente para não colidir com o backend qtagg
        já inicializado pelo módulo de investimentos.
        """
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure

            income_map: dict[str, float] = defaultdict(float)
            expense_map: dict[str, float] = defaultdict(float)

            for t in transactions:
                tx_date = t.get("transaction_date", "")
                if len(tx_date) >= 7:
                    key = tx_date[:7]   # "2024-01"
                    amount = float(t.get("amount", 0))
                    tx_type = t.get("transaction_type", "")
                    if tx_type == "income":
                        income_map[key] += amount
                    elif tx_type in ("debit", "credit"):
                        expense_map[key] += amount

            all_months = sorted(set(income_map) | set(expense_map))
            if not all_months:
                return ""

            # Labels curtos: "Jan", "Fev", …
            _PT = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                   "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
            labels = []
            for m in all_months:
                try:
                    labels.append(_PT[int(m[5:7])])
                except (ValueError, IndexError):
                    labels.append(m[5:])

            income_vals  = [income_map[m]  for m in all_months]
            expense_vals = [expense_map[m] for m in all_months]
            balance_vals = [i - e for i, e in zip(income_vals, expense_vals)]

            fig = Figure(figsize=(10, 3.2), facecolor="#f8faff")
            FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            ax.set_facecolor("#f8faff")

            x = range(len(labels))
            w = 0.32
            ax.bar([i - w for i in x], income_vals,  w * 2, color="#0a7a4a", label="Receitas",  alpha=0.85)
            ax.bar([i + w for i in x], expense_vals, w * 2, color="#b71c1c", label="Despesas",  alpha=0.85)
            ax.plot(list(x), balance_vals, color="#1a3a6e", marker="o", linewidth=1.5,
                    markersize=4, label="Saldo", zorder=5)

            ax.set_xticks(list(x))
            ax.set_xticklabels(labels, fontsize=9)
            ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--")
            ax.legend(fontsize=8, loc="upper right")
            ax.set_title("Receitas × Despesas por mês", fontsize=11, color="#1a3a6e", pad=6)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.tick_params(axis="y", labelsize=8)
            ax.yaxis.set_major_formatter(
                __import__("matplotlib.ticker", fromlist=["FuncFormatter"]).FuncFormatter(
                    lambda v, _: f"R${v/1000:.0f}k" if abs(v) >= 1000 else f"R${v:.0f}"
                )
            )

            fig.tight_layout(pad=1.0)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=96, bbox_inches="tight")
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode()
            return (
                f'<img src="data:image/png;base64,{b64}" '
                f'style="max-width:100%; height:auto; margin:16px 0; '
                f'border-radius:6px; border:1px solid #e0e0e0;">'
            )
        except Exception:
            return ""   # gráfico opcional — falhar silenciosamente

    def _build_summary_body(self, data: dict) -> str:
        transactions = data.get("transactions", [])
        if not transactions:
            return "<p>Nenhum lançamento encontrado no período.</p>"

        income = sum(float(t["amount"]) for t in transactions if t.get("transaction_type") == "income")
        expense = sum(float(t["amount"]) for t in transactions if t.get("transaction_type") in ("debit", "credit", "investment"))
        balance = income - expense
        rate = (balance / income * 100) if income > 0 else 0

        cats: dict[str, float] = {}
        for t in transactions:
            if t.get("transaction_type") in ("debit", "credit"):
                cat = t.get("category", "outros")
                cats[cat] = cats.get(cat, 0) + float(t.get("amount", 0))

        cat_rows = "".join(
            f"<tr><td>{cat}</td><td class='amount'>R$ {val:,.2f}</td></tr>".replace(",", "X").replace(".", ",").replace("X", ".")
            for cat, val in sorted(cats.items(), key=lambda x: -x[1])
        )

        def fmt(v: float) -> str:
            return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        chart_html = self._make_summary_chart_html(transactions)

        return f"""
        <div class="summary-box">
          <div class="summary-item">
            <div class="summary-label">Receitas</div>
            <div class="summary-value receita">{fmt(income)}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Despesas</div>
            <div class="summary-value despesa">{fmt(expense)}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Saldo</div>
            <div class="summary-value">{fmt(balance)}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Taxa de poupança</div>
            <div class="summary-value">{rate:.1f}%</div>
          </div>
        </div>
        {chart_html}
        <h2>Despesas por categoria</h2>
        <table>
          <thead><tr><th>Categoria</th><th>Total</th></tr></thead>
          <tbody>{cat_rows}</tbody>
        </table>"""

    def _build_investments_body(self, data: dict) -> str:
        assets = data.get("assets", [])
        portfolio = data.get("portfolio", {})
        total = float(portfolio.get("total_invested", 0))

        if not assets:
            return "<p>Nenhum ativo cadastrado.</p>"

        rows = ""
        for a in assets:
            rows += f"""<tr>
              <td>{a.get('ticker','')}</td>
              <td>{a.get('name','')}</td>
              <td>{a.get('asset_type','')}</td>
            </tr>"""

        def fmt(v: float) -> str:
            return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        return f"""
        <div class="summary-box">
          <div class="summary-item">
            <div class="summary-label">Total investido</div>
            <div class="summary-value">{fmt(total)}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Ativos cadastrados</div>
            <div class="summary-value">{len(assets)}</div>
          </div>
        </div>
        <h2>Ativos na carteira</h2>
        <table>
          <thead><tr><th>Ticker</th><th>Nome</th><th>Tipo</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    def _build_expense_pattern_body(self, data: dict) -> str:
        """
        Exibe padrão de gastos por categoria: mês atual vs média 3 meses.

        Anomalias (variação > 50%) aparecem em vermelho com ícone ⚠.
        Tendências usam setas: ↑ crescente, → estável, ↓ decrescente.
        """
        patterns: list[dict] = data.get("patterns", [])

        if not patterns:
            return "<p>Sem dados de gastos para o período. Registre lançamentos primeiro.</p>"

        def fmt(v: float) -> str:
            return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        _TREND_ARROW = {"crescente": "↑", "estável": "→", "decrescente": "↓"}
        _TREND_COLOR = {"crescente": "#b71c1c", "estável": "#555", "decrescente": "#0a7a4a"}

        rows = ""
        anomaly_count = 0
        for p in sorted(patterns, key=lambda x: -abs(x.get("change_pct", 0))):
            cat         = p.get("category", "outros")
            current     = float(p.get("current_month", 0))
            avg3m       = float(p.get("avg_3m", 0))
            change_pct  = float(p.get("change_pct", 0))
            trend       = p.get("trend", "estável")
            is_anomaly  = bool(p.get("is_anomaly", False))

            arrow  = _TREND_ARROW.get(trend, "→")
            color  = _TREND_COLOR.get(trend, "#555")
            sign   = "+" if change_pct > 0 else ""
            flag   = " ⚠" if is_anomaly else ""
            row_bg = ' style="background:#fff5f5;"' if is_anomaly else ""

            if is_anomaly:
                anomaly_count += 1

            rows += f"""<tr{row_bg}>
              <td>{cat}{flag}</td>
              <td class="amount">{fmt(current)}</td>
              <td class="amount">{fmt(avg3m)}</td>
              <td class="amount" style="color:{color};">{sign}{change_pct:.1f}%</td>
              <td style="color:{color}; text-align:center;">{arrow} {trend}</td>
            </tr>"""

        summary_html = ""
        if anomaly_count:
            summary_html = f"""<div class="summary-box" style="border-color:#ffcdd2; background:#fff5f5;">
              <div class="summary-item">
                <div class="summary-label">⚠ Anomalias detectadas</div>
                <div class="summary-value" style="color:#b71c1c;">{anomaly_count}</div>
              </div>
              <div class="summary-item">
                <div class="summary-label">Critério</div>
                <div class="summary-value" style="font-size:14px; color:#555;">variação &gt; 50% vs média 3m</div>
              </div>
            </div>"""

        return f"""
        {summary_html}
        <h2>Gastos por categoria — mês atual vs últimos 3 meses</h2>
        <table>
          <thead>
            <tr>
              <th>Categoria</th>
              <th>Mês atual</th>
              <th>Média 3 meses</th>
              <th>Variação</th>
              <th>Tendência</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <p style="font-size:11px; color:#888; margin-top:12px;">
          ⚠ Anomalia = gasto do mês atual mais de 50% acima da média dos 3 meses anteriores.<br>
          Tendência calculada pela inclinação linear dos últimos 3 meses.
        </p>"""

    def _build_opportunity_cost_body(self, data: dict) -> str:
        """
        Compara o custo investido na carteira de renda variável com o CDI no período.

        Usa as chaves reais retornadas por GET /portfolio/opportunity-cost:
          total_invested_equity, cdi_return_pct, portfolio_return_pct, alpha_pct,
          period_months, message.
        """
        opp: dict = data.get("opportunity", {})
        assets: list[dict] = data.get("assets", [])

        if not opp:
            return "<p>Não foi possível calcular o custo de oportunidade. Verifique a conexão com a API.</p>"

        def fmt(v: float | None, suffix: str = "") -> str:
            if v is None:
                return "—"
            return f"{v:,.2f}{suffix}".replace(",", "X").replace(".", ",").replace("X", ".")

        def fmtbrl(v: float | None) -> str:
            if v is None:
                return "—"
            return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        total_equity    = opp.get("total_invested_equity")
        cdi_pct         = opp.get("cdi_return_pct")
        port_pct        = opp.get("portfolio_return_pct")
        alpha           = opp.get("alpha_pct")
        period_months   = opp.get("period_months", 12)
        message         = opp.get("message", "")

        # Alpha: positivo = carteira superou CDI (verde), negativo = ficou atrás (vermelho)
        alpha_color = "#0a7a4a" if (alpha or 0) >= 0 else "#b71c1c"
        alpha_sign  = "+" if (alpha or 0) >= 0 else ""

        assets_rows = "".join(
            f"<tr><td>{a.get('ticker','—')}</td><td>{a.get('name','')}</td>"
            f"<td>{a.get('asset_type','')}</td></tr>"
            for a in assets
        )

        return f"""
        <div class="summary-box">
          <div class="summary-item">
            <div class="summary-label">Capital em renda variável</div>
            <div class="summary-value">{fmtbrl(total_equity)}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">CDI no período ({period_months}m)</div>
            <div class="summary-value">{fmt(cdi_pct, '%')}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Retorno da carteira</div>
            <div class="summary-value">{fmt(port_pct, '%') if port_pct is not None else 'Cotação necessária'}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Alpha (carteira − CDI)</div>
            <div class="summary-value" style="color:{alpha_color};">
              {(alpha_sign + fmt(alpha, '%')) if alpha is not None else 'Cotação necessária'}
            </div>
          </div>
        </div>
        <p style="font-size:12px; color:#555; margin-top:16px;">{message}</p>
        <p style="font-size:12px; color:#555;">
          <strong>Alpha positivo</strong> = carteira superou o CDI no período.<br>
          <strong>Alpha negativo</strong> = CDI teria rendido mais que a carteira.
        </p>
        <h2>Ativos na carteira</h2>
        <table>
          <thead><tr><th>Ticker</th><th>Nome</th><th>Tipo</th></tr></thead>
          <tbody>{assets_rows}</tbody>
        </table>"""

    # ------------------------------------------------------------------
    # Exportação PDF
    # ------------------------------------------------------------------

    def _export_pdf(self) -> None:
        if not self._current_html:
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar relatório em PDF",
            f"relatorio_financehub_{date.today().strftime('%Y%m%d')}.pdf",
            "Arquivos PDF (*.pdf)",
        )
        if not path:
            return

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)

        doc = QTextDocument()
        doc.setHtml(self._current_html)
        doc.print(printer)

        QMessageBox.information(
            self,
            "PDF salvo",
            f"Relatório salvo com sucesso em:\n{path}",
        )


# ======================================================================
# Utilitários
# ======================================================================


def _fmt_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return d.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso
