"""
Página de Mercado — cotações e histórico de preços de ativos.

Layout:
  ┌────────────────────────────────────────────────────────────┐
  │  [Ticker…]  [Período ▾]  [Intervalo ▾]  [Buscar]          │  ← toolbar
  ├────────────────────────────────────────────────────────────┤
  │  Preço atual  │  Variação  │  Volume  │  Atualizado às     │  ← card cotação
  ├────────────────────────────────────────────────────────────┤
  │  Gráfico de preços históricos (Plotly via QWebEngineView)  │
  └────────────────────────────────────────────────────────────┘

Threading:
  MarketWorker busca GET /market/quote/{ticker} e
  GET /market/history/{ticker} em background.
  O gráfico é gerado em Plotly e carregado como HTML no QWebEngineView.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError


# ======================================================================
# Worker
# ======================================================================


class MarketWorker(QThread):
    """Busca cotação atual e histórico de preços em background."""

    data_ready = pyqtSignal(dict, dict)  # (quote, history)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        client: ApiClient,
        ticker: str,
        period: str,
        interval: str,
    ) -> None:
        super().__init__()
        self._client = client
        self._ticker = ticker
        self._period = period
        self._interval = interval

    def run(self) -> None:
        try:
            quote = self._client.get_market_quote(self._ticker)
            history = self._client.get_market_history(
                self._ticker, self._period, self._interval
            )
            self.data_ready.emit(quote, history)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Página de Mercado
# ======================================================================


_PERIOD_OPTIONS = [
    ("1 mês",   "1m",  "1d"),
    ("3 meses", "3m",  "1d"),
    ("6 meses", "6m",  "1d"),
    ("1 ano",   "1y",  "1wk"),
    ("5 anos",  "5y",  "1mo"),
]


class MarketPage(QWidget):
    """
    Página de busca de cotações e histórico de preços.

    O gráfico Plotly é renderizado como HTML em um QWebEngineView.
    Se PyQtWebEngine não estiver disponível, mostra fallback em texto.
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: MarketWorker | None = None
        self._web_view = None  # carregado sob demanda
        self._build_ui()

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

        # --- Toolbar de busca ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        self._ticker_input = QLineEdit()
        self._ticker_input.setPlaceholderText("Ex: PETR4, VALE3, ^BVSP, AAPL…")
        self._ticker_input.setFixedWidth(220)
        self._ticker_input.returnPressed.connect(self._search)
        toolbar.addWidget(self._ticker_input)

        self._period_combo = QComboBox()
        for label, _, _ in _PERIOD_OPTIONS:
            self._period_combo.addItem(label)
        self._period_combo.setCurrentIndex(2)  # 6 meses padrão
        toolbar.addWidget(self._period_combo)

        search_btn = QPushButton("Buscar")
        search_btn.setProperty("class", "primary")
        search_btn.style().unpolish(search_btn)
        search_btn.style().polish(search_btn)
        search_btn.clicked.connect(self._search)
        toolbar.addWidget(search_btn)

        toolbar.addStretch()
        main.addLayout(toolbar)

        # --- Card de cotação ---
        self._quote_frame = QFrame()
        self._quote_frame.setObjectName("summaryCard")
        self._quote_frame.setVisible(False)
        quote_layout = QHBoxLayout(self._quote_frame)
        quote_layout.setContentsMargins(24, 16, 24, 16)
        quote_layout.setSpacing(32)

        self._lbl_ticker   = _info_label("—", big=True)
        self._lbl_price    = _info_label("—", big=True, color="#E8EAED")
        self._lbl_change   = _info_label("—")
        self._lbl_volume   = _info_label("—")
        self._lbl_updated  = _info_label("—")

        for widget, label_text in [
            (self._lbl_ticker,  "Ativo"),
            (self._lbl_price,   "Preço"),
            (self._lbl_change,  "Variação"),
            (self._lbl_volume,  "Volume méd. 3m"),
            (self._lbl_updated, "Atualizado"),
        ]:
            col = QVBoxLayout()
            col.setSpacing(4)
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #8B90A7; font-size: 11px; text-transform: uppercase;")
            col.addWidget(lbl)
            col.addWidget(widget)
            quote_layout.addLayout(col)

        quote_layout.addStretch()
        main.addWidget(self._quote_frame)

        # --- Status / loading ---
        self._status_label = QLabel("Informe um ticker e clique em Buscar.")
        self._status_label.setObjectName("loadingLabel")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._status_label)

        # --- Área do gráfico ---
        self._chart_container = QWidget()
        self._chart_container.setObjectName("summaryCard")
        self._chart_container.setMinimumHeight(400)
        self._chart_container.setVisible(False)
        chart_layout = QVBoxLayout(self._chart_container)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        main.addWidget(self._chart_container)

        # Tenta inicializar o WebEngineView para o gráfico Plotly
        self._chart_label = QLabel()
        self._chart_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chart_label.setWordWrap(True)
        chart_layout.addWidget(self._chart_label)
        self._chart_inner_layout = chart_layout

        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            self._web_view = QWebEngineView()
            self._web_view.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            chart_layout.addWidget(self._web_view)
            self._chart_label.setVisible(False)
        except ImportError:
            self._web_view = None
            self._chart_label.setText(
                "PyQtWebEngine não está instalado.\n"
                "Instale com: pip install PyQt6-WebEngine"
            )

        main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # Busca
    # ------------------------------------------------------------------

    def _search(self) -> None:
        ticker = self._ticker_input.text().strip().upper()
        if not ticker:
            QMessageBox.warning(self, "Ticker vazio", "Informe o código do ativo.")
            return

        period_idx = self._period_combo.currentIndex()
        _, period, interval = _PERIOD_OPTIONS[period_idx]

        if self._worker and self._worker.isRunning():
            return

        self._quote_frame.setVisible(False)
        self._chart_container.setVisible(False)
        self._status_label.setText(f"Buscando dados de {ticker}…")
        self._status_label.setVisible(True)

        self._worker = MarketWorker(self._client, ticker, period, interval)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_data_ready(self, quote: dict, history: dict) -> None:
        self._status_label.setVisible(False)
        self._update_quote_card(quote)
        self._update_chart(quote["ticker"], history)

    def _on_error(self, message: str) -> None:
        self._status_label.setText(f"Erro: {message}")
        self._quote_frame.setVisible(False)
        self._chart_container.setVisible(False)

    # ------------------------------------------------------------------
    # Card de cotação
    # ------------------------------------------------------------------

    def _update_quote_card(self, quote: dict) -> None:
        ticker = quote.get("ticker", "—")
        price = float(quote.get("price", 0))
        currency = quote.get("currency", "R$")
        change = quote.get("change_pct")
        volume = quote.get("volume")
        fetched_at = quote.get("fetched_at", "")

        currency_symbol = "R$" if currency == "BRL" else "$"
        price_str = f"{currency_symbol} {price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        self._lbl_ticker.setText(ticker)

        self._lbl_price.setText(price_str)

        if change is not None:
            change_val = float(change)
            color = "#00C896" if change_val >= 0 else "#FF6B6B"
            sign = "+" if change_val >= 0 else ""
            self._lbl_change.setText(f"{sign}{change_val:.2f}%")
            self._lbl_change.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: 600;")
        else:
            self._lbl_change.setText("—")

        if volume is not None:
            vol_str = f"{int(volume):,}".replace(",", ".")
            self._lbl_volume.setText(vol_str)
        else:
            self._lbl_volume.setText("—")

        if fetched_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                self._lbl_updated.setText(dt.strftime("%H:%M:%S"))
            except Exception:
                self._lbl_updated.setText(fetched_at[:19])
        else:
            self._lbl_updated.setText("—")

        self._quote_frame.setVisible(True)

    # ------------------------------------------------------------------
    # Gráfico
    # ------------------------------------------------------------------

    def _update_chart(self, ticker: str, history: dict) -> None:
        points = history.get("points", [])
        if not points:
            self._chart_label.setText("Sem dados históricos disponíveis.")
            self._chart_label.setVisible(True)
            if self._web_view:
                self._web_view.setVisible(False)
            self._chart_container.setVisible(True)
            return

        dates = [p["date"] for p in points]
        prices = [float(p["price"]) for p in points]

        if self._web_view is not None:
            html = _build_plotly_html(ticker, dates, prices)
            self._web_view.setVisible(True)
            self._chart_label.setVisible(False)
            self._web_view.setHtml(html)
        else:
            # Fallback: tabela de texto simples
            lines = [f"{d}: {p:,.2f}" for d, p in zip(dates[-10:], prices[-10:])]
            self._chart_label.setText(
                "Últimos 10 fechamentos:\n" + "\n".join(lines)
            )
            self._chart_label.setVisible(True)

        self._chart_container.setVisible(True)


# ======================================================================
# Helpers
# ======================================================================


def _info_label(text: str, big: bool = False, color: str = "#4A9EFF") -> QLabel:
    lbl = QLabel(text)
    size = "20px" if big else "14px"
    weight = "700" if big else "600"
    lbl.setStyleSheet(f"color: {color}; font-size: {size}; font-weight: {weight};")
    return lbl


def _build_plotly_html(ticker: str, dates: list[str], prices: list[float]) -> str:
    """Gera HTML auto-contido com um gráfico de linha Plotly."""
    data = json.dumps({
        "dates": dates,
        "prices": prices,
        "ticker": ticker,
    })
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ margin: 0; background: #1A1D27; }}
  #chart {{ width: 100%; height: 100vh; }}
</style>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
</head>
<body>
<div id="chart"></div>
<script>
var raw = {data};
var trace = {{
  x: raw.dates,
  y: raw.prices,
  type: 'scatter',
  mode: 'lines',
  name: raw.ticker,
  line: {{ color: '#4A9EFF', width: 2 }},
  fill: 'tozeroy',
  fillcolor: 'rgba(74,158,255,0.08)'
}};
var layout = {{
  paper_bgcolor: '#1A1D27',
  plot_bgcolor:  '#1A1D27',
  font:  {{ color: '#E8EAED', family: 'Inter, Segoe UI, sans-serif', size: 12 }},
  xaxis: {{ gridcolor: '#2E3250', showgrid: true }},
  yaxis: {{ gridcolor: '#2E3250', showgrid: true }},
  margin: {{ l: 60, r: 20, t: 30, b: 40 }},
  showlegend: false
}};
Plotly.newPlot('chart', [trace], layout, {{responsive: true, displayModeBar: false}});
</script>
</body>
</html>"""
