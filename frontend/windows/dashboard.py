"""
Página do Dashboard — visão geral do patrimônio e alertas ativos.

Layout:
  ┌───────────────────────────────────────────────────────┐
  │  [Patrimônio]  [Receitas]  [Despesas]  [Score Saúde]  │  ← cards de resumo
  ├───────────────────────────────────────────────────────┤
  │              Gráfico de evolução patrimonial           │  ← placeholder
  ├───────────────────────────────────────────────────────┤
  │  Alertas Ativos                                       │
  │  ● [título] — [mensagem]                              │  ← lista de alertas
  └───────────────────────────────────────────────────────┘

Threading:
  DashboardWorker (QThread) busca dashboard + alertas em background e emite
  sinais Qt de volta para a thread principal quando os dados chegam ou um
  erro ocorre. Isso mantém a UI responsiva durante a requisição HTTP.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QPushButton,
)

from frontend.components.api_client import ApiClient, ApiError


# ======================================================================
# Worker — busca de dados em background
# ======================================================================


class DashboardWorker(QThread):
    """
    Executa as chamadas HTTP em uma thread separada para não travar a UI.

    Qt só permite atualizar widgets na thread principal (a que criou o
    QApplication). Por isso, o worker apenas emite sinais com os dados
    brutos; os slots na DashboardPage atualizam os widgets.
    """

    # Emitido com (dashboard_dict, alerts_dict) quando ambas as requisições concluem
    data_ready = pyqtSignal(dict, dict)
    # Emitido com mensagem de erro amigável se qualquer requisição falhar
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        """Executa na thread do worker — nunca toque em widgets aqui."""
        try:
            dashboard = self._client.get_dashboard()
            alerts = self._client.get_alerts()
            self.data_ready.emit(dashboard, alerts)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            # Captura erros inesperados sem derrubar a aplicação
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Componentes de UI reutilizáveis dentro do dashboard
# ======================================================================


class SummaryCard(QFrame):
    """
    Card compacto que exibe um único indicador financeiro.

    Recebe cor explícita para o valor para reforçar o significado visual:
      verde  → positivo / receita / score alto
      vermelho → negativo / despesa / score baixo
      azul   → informacional (patrimônio total)
    """

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

        # Subtítulo opcional (ex: "05/2026")
        self._sub_label = QLabel("")
        self._sub_label.setObjectName("cardSub")
        self._sub_label.setStyleSheet("color: #8B90A7; font-size: 11px;")

        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)
        layout.addWidget(self._sub_label)

    def set_value(self, value: str, color: str | None = None, sub: str = "") -> None:
        """Atualiza valor exibido, cor e subtítulo de forma segura."""
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

        priority = alert.get("priority", "BAIXA")
        dot_color = self._PRIORITY_COLOR.get(priority, "#4A9EFF")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)

        # Marcador de prioridade — círculo colorido
        dot = QLabel("●")
        dot.setFixedWidth(14)
        dot.setStyleSheet(f"color: {dot_color}; font-size: 11px;")
        dot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        # Conteúdo textual
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
    Página completa do dashboard montada com scroll vertical.

    Ciclo de vida:
      __init__ → _build_ui (cria widgets em estado de loading)
               → load_data (dispara o worker em background)
      Worker   → _on_data_ready / _on_error (atualiza a UI na main thread)
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        # Mantém referência ao worker para evitar garbage collection prematura
        self._worker: DashboardWorker | None = None

        self._build_ui()
        self.load_data()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # ScrollArea permite que o conteúdo cresça sem limites verticais
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

        # --- Estado de loading (visível até os dados chegarem) ---
        self._loading_label = QLabel("Carregando dados do dashboard…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_layout.addWidget(self._loading_label)

        # --- Linha de cards de resumo ---
        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)

        # Cada card tem cor padrão associada ao seu significado semântico
        self._card_patrimonio = SummaryCard("Patrimônio Líquido", "#4A9EFF")
        self._card_receitas = SummaryCard("Receitas do Mês", "#00C896")
        self._card_despesas = SummaryCard("Despesas do Mês", "#FF6B6B")
        self._card_score = SummaryCard("Score de Saúde", "#00C896")

        for card in self._cards():
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.setVisible(False)  # ocultos até os dados chegarem
            cards_row.addWidget(card)

        self._content_layout.addLayout(cards_row)

        # --- Placeholder do gráfico de evolução patrimonial ---
        self._chart_frame = QFrame()
        self._chart_frame.setObjectName("chartPlaceholder")
        self._chart_frame.setMinimumHeight(280)
        self._chart_frame.setVisible(False)
        chart_layout = QVBoxLayout(self._chart_frame)
        chart_label = QLabel("Gráfico de Evolução Patrimonial\n(integração Plotly em breve)")
        chart_label.setObjectName("chartPlaceholderLabel")
        chart_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chart_layout.addWidget(chart_label)
        self._content_layout.addWidget(self._chart_frame)

        # --- Seção de alertas ---
        self._alerts_title = QLabel("Alertas Ativos")
        self._alerts_title.setObjectName("sectionTitle")
        self._alerts_title.setVisible(False)
        self._content_layout.addWidget(self._alerts_title)

        # Container de alertas: itens são inseridos/removidos dinamicamente
        self._alerts_area = QVBoxLayout()
        self._alerts_area.setSpacing(8)
        self._content_layout.addLayout(self._alerts_area)

        # Botão de recarregar (oculto no loading, visível após 1ª carga)
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

    # ------------------------------------------------------------------
    # Carregamento de dados
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        """Inicia (ou reinicia) a busca de dados em background."""
        # Evita disparar dois workers simultâneos ao clicar em "Atualizar"
        if self._worker and self._worker.isRunning():
            return

        self._set_content_visible(False)
        self._loading_label.setText("Carregando dados do dashboard…")
        self._loading_label.setVisible(True)
        self._reload_btn.setVisible(False)

        self._worker = DashboardWorker(self._client)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------
    # Slots (chamados na main thread via sinal Qt)
    # ------------------------------------------------------------------

    def _on_data_ready(self, dashboard: dict, alerts: dict) -> None:
        """Atualiza todos os cards e alertas com os dados recebidos da API."""
        self._loading_label.setVisible(False)
        self._set_content_visible(True)

        # Extrai sub-dicionários com fallback seguro
        net_worth = dashboard.get("net_worth", {})
        monthly = dashboard.get("monthly_summary", {})
        health = dashboard.get("health_score", {})

        # Patrimônio líquido: azul se positivo, vermelho se negativo
        nw = float(net_worth.get("net_worth", 0))
        nw_color = "#4A9EFF" if nw >= 0 else "#FF6B6B"
        self._card_patrimonio.set_value(
            _fmt_brl(nw), color=nw_color, sub=f"Ativos: {_fmt_brl(float(net_worth.get('total_assets', 0)))}"
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

        # Score de saúde: verde ≥ 60, laranja ≥ 40, vermelho < 40
        score = int(health.get("total", 0))
        score_color = "#00C896" if score >= 60 else ("#FFB347" if score >= 40 else "#FF6B6B")
        self._card_score.set_value(f"{score} / 100", color=score_color)

        # Atualiza lista de alertas
        self._populate_alerts(alerts.get("alerts", []))

        self._reload_btn.setVisible(True)

    def _on_error(self, message: str) -> None:
        """Exibe mensagem de erro no lugar do indicador de loading."""
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._loading_label.setVisible(True)
        self._set_content_visible(False)
        self._reload_btn.setVisible(True)

    # ------------------------------------------------------------------
    # Helpers de UI
    # ------------------------------------------------------------------

    def _cards(self) -> list[SummaryCard]:
        return [self._card_patrimonio, self._card_receitas, self._card_despesas, self._card_score]

    def _set_content_visible(self, visible: bool) -> None:
        """Alterna visibilidade de todos os elementos de conteúdo de uma vez."""
        for card in self._cards():
            card.setVisible(visible)
        self._chart_frame.setVisible(visible)
        self._alerts_title.setVisible(visible)

    def _populate_alerts(self, alerts: list[dict]) -> None:
        """Remove alertas antigos e insere os novos na área de alertas."""
        # Limpa widgets existentes sem iterar while-count (mais seguro)
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
            row = AlertRow(alert_data)
            self._alerts_area.addWidget(row)


# ======================================================================
# Utilitário de formatação
# ======================================================================


def _fmt_brl(value: float) -> str:
    """
    Formata um número como moeda brasileira: R$ 1.234,56

    Não usa locale do sistema para evitar dependência de configuração do OS.
    Funciona em qualquer ambiente onde Python esteja instalado.
    """
    try:
        formatted = f"{abs(value):,.2f}"          # "1,234.56"
        formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")  # "1.234,56"
        prefix = "-R$ " if value < 0 else "R$ "
        return f"{prefix}{formatted}"
    except (TypeError, ValueError):
        return "—"
