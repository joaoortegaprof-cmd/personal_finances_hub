"""
Página de Mercado — carteira agrupada por categoria com cotações em tempo real.

Layout:
  ┌─────────────────────────────────────────────────────────────────┐
  │  Minha Carteira                              [↻ Atualizar]      │
  ├─────────────────────────────────────────────────────────────────┤
  │  Valor Total  │  Maior Posição  │  Variação do Dia              │  ← resumo
  ├─────────────────────────────────────────────────────────────────┤
  │  Ações — R$ 12.450,00 — 42,3%                                   │
  │  [ tabela de ações ]                                            │
  ├─────────────────────────────────────────────────────────────────┤
  │  FIIs — R$ 8.200,00 — 27,9%                                     │
  │  [ tabela de FIIs ]                                             │
  └─────────────────────────────────────────────────────────────────┘

Ao clicar em uma linha → FundamentalsDialog com posição + indicadores fundamentalistas.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
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


# ======================================================================
# Constantes de apresentação
# ======================================================================

_CATEGORY_ORDER: list[tuple[str, str]] = [
    ("acao",               "Ações"),
    ("fii",                "FIIs"),
    ("etf",                "ETFs"),
    ("tesouro_direto",     "Tesouro Direto"),
    ("renda_fixa",         "Renda Fixa"),
    ("criptomoeda",        "Criptomoedas"),
    ("acao_internacional", "Internacional"),
    ("previdencia",        "Previdência"),
    ("outros",             "Outros"),
]

_TABLE_HEADERS = [
    "Ticker", "Nome", "Qtd", "Preço Médio",
    "Cotação Atual", "Valor Investido", "Valor Atual",
    "Rentab. %", "Var. Dia %",
]

_COL_TICKER   = 0
_COL_NAME     = 1
_COL_QTD      = 2
_COL_AVG      = 3
_COL_CURRENT  = 4
_COL_INVESTED = 5
_COL_ACTUAL   = 6
_COL_RETURN   = 7
_COL_DAY      = 8

_C_POS    = "#00C896"
_C_NEG    = "#FF6B6B"
_C_MUTED  = "#8B90A7"
_C_WHITE  = "#E8EAED"
_C_ACCENT = "#4A9EFF"

_FIXED_TYPES = {"tesouro_direto", "renda_fixa", "previdencia"}

_FUND_ITEMS = [
    ("pe_ratio",       "P/L",           "Preço / Lucro — quantas vezes o mercado paga pelo lucro anual"),
    ("pb_ratio",       "P/VP",          "Preço / Valor Patrimonial — prêmio sobre o patrimônio"),
    ("dividend_yield", "DY %",          "Dividend Yield — rendimento de proventos nos últimos 12 meses"),
    ("roe",            "ROE %",         "Return on Equity — retorno sobre o patrimônio líquido"),
    ("net_margin",     "Margem Liq. %", "Margem Líquida — proporção do lucro sobre a receita"),
    ("ev_ebitda",      "EV/EBITDA",     "Enterprise Value / EBITDA — múltiplo de valuation operacional"),
]


# ======================================================================
# Helpers de formatação
# ======================================================================

def _fmt_brl(value: float | None, currency: str = "BRL") -> str:
    if value is None:
        return "—"
    sym = "R$" if currency == "BRL" else "$"
    s = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if value < 0 else ''}{sym} {s}"


def _fmt_pct(value: float | None) -> tuple[str, str]:
    """Retorna (string formatada, cor hex)."""
    if value is None:
        return "—", _C_MUTED
    sign = "+" if value >= 0 else ""
    color = _C_POS if value >= 0 else _C_NEG
    return f"{sign}{value:.2f}%", color


def _fmt_qty(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}".replace(",", ".")
    return f"{value:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ======================================================================
# Workers
# ======================================================================

class PortfolioQuotesWorker(QThread):
    """
    Carrega todos os ativos com posições ativas e busca cotações em background.

    Para cada ativo:
      1. GET /assets — lista todos os ativos cadastrados
      2. GET /assets/{id}/position — quantidade líquida e preço médio
      3. GET /market/quote/{ticker} — cotação atual (apenas para ativos com ticker)

    Emite lista de dicts com todos os dados calculados.
    """
    data_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            assets = self._client.get_assets()
            result: list[dict] = []

            for asset in assets:
                asset_id   = asset["id"]
                asset_type = asset.get("asset_type", "outros")
                ticker     = asset.get("ticker")
                currency   = asset.get("currency", "BRL")

                try:
                    pos       = self._client.get_asset_position(asset_id)
                    net_qty   = float(pos.get("net_quantity") or 0)
                    avg_price = float(pos.get("avg_price") or 0)
                except ApiError:
                    net_qty = avg_price = 0.0

                if net_qty <= 0:
                    continue

                valor_investido = net_qty * avg_price
                current_price   = None
                change_pct      = None

                if ticker:
                    try:
                        q = self._client.get_market_quote(ticker)
                        p = q.get("price")
                        current_price = float(p) if p is not None else None
                        cp = q.get("change_pct")
                        change_pct = float(cp) if cp is not None else None
                        currency = q.get("currency", currency)
                    except ApiError:
                        pass

                valor_atual  = (net_qty * current_price) if current_price is not None else None
                rentabilidade = None
                if current_price is not None and avg_price > 0:
                    rentabilidade = (current_price - avg_price) / avg_price * 100

                result.append({
                    "id":             asset_id,
                    "ticker":         ticker,
                    "name":           asset.get("name", ""),
                    "asset_type":     asset_type,
                    "currency":       currency,
                    "net_quantity":   net_qty,
                    "avg_price":      avg_price,
                    "current_price":  current_price,
                    "change_pct":     change_pct,
                    "valor_investido":valor_investido,
                    "valor_atual":    valor_atual,
                    "rentabilidade":  rentabilidade,
                    "indexer":        asset.get("indexer"),
                    "maturity_date":  asset.get("maturity_date"),
                    "notes":          asset.get("notes"),
                })

            self.data_ready.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class FundamentalsWorker(QThread):
    """Busca indicadores fundamentalistas de um ticker em background."""
    data_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, ticker: str) -> None:
        super().__init__()
        self._client = client
        self._ticker = ticker

    def run(self) -> None:
        try:
            data = self._client.get_market_fundamentals(self._ticker)
            self.data_ready.emit(data)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Dialog de detalhes / fundamentalistas
# ======================================================================

class FundamentalsDialog(QDialog):
    """
    Dialog com posição consolidada e indicadores fundamentalistas de um ativo.

    Para ativos com ticker: carrega P/L, P/VP, DY, ROE, Margem Líquida, EV/EBITDA.
    Para renda fixa / Tesouro: exibe indexador, vencimento e observações.
    """

    def __init__(self, entry: dict, client: ApiClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self._client = client
        self._worker: FundamentalsWorker | None = None
        self._fund_layout: QGridLayout | None = None

        display = entry.get("ticker") or entry.get("name", "Ativo")
        self.setWindowTitle(f"Detalhes — {display}")
        self.setMinimumWidth(460)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # --- Cabeçalho ---
        name_lbl = QLabel(entry.get("name", ""))
        name_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {_C_WHITE};")
        name_lbl.setWordWrap(True)
        root.addWidget(name_lbl)

        if entry.get("ticker"):
            tk = QLabel(entry["ticker"])
            tk.setStyleSheet(f"font-size: 11px; color: {_C_ACCENT};")
            root.addWidget(tk)

        # --- Posição ---
        root.addWidget(self._build_position_frame(entry))

        # --- Fundamentalistas (apenas para renda variável com ticker) ---
        is_fixed = entry.get("asset_type") in _FIXED_TYPES
        if not is_fixed and entry.get("ticker"):
            sep = QLabel("Indicadores Fundamentalistas")
            sep.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {_C_MUTED}; margin-top: 6px;"
            )
            root.addWidget(sep)

            fund_frame = QFrame()
            fund_frame.setObjectName("summaryCard")
            self._fund_layout = QGridLayout(fund_frame)
            self._fund_layout.setContentsMargins(16, 12, 16, 12)
            self._fund_layout.setSpacing(6)
            self._fund_layout.setColumnStretch(1, 1)

            loading = QLabel("Carregando indicadores…")
            loading.setStyleSheet(f"color: {_C_MUTED}; font-size: 12px;")
            loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._fund_layout.addWidget(loading, 0, 0, 1, 2)
            root.addWidget(fund_frame)

            self._start_fundamentals_worker()

        # --- Botão fechar ---
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.reject)
        root.addWidget(btn)

    def _build_position_frame(self, entry: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("summaryCard")
        grid = QGridLayout(frame)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setSpacing(6)
        grid.setColumnStretch(1, 1)

        is_fixed = entry.get("asset_type") in _FIXED_TYPES
        ccy = entry.get("currency", "BRL")
        row = 0

        if is_fixed:
            _grid_row(grid, row, "Indexador", entry.get("indexer") or "—"); row += 1
            _grid_row(grid, row, "Vencimento", entry.get("maturity_date") or "Sem vencimento"); row += 1
            notes = entry.get("notes")
            if notes:
                _grid_row(grid, row, "Observações", notes); row += 1

        qty = entry.get("net_quantity", 0.0)
        avg = entry.get("avg_price", 0.0)
        inv = entry.get("valor_investido", 0.0)
        cur_p = entry.get("current_price")
        cur_v = entry.get("valor_atual")
        ret   = entry.get("rentabilidade")

        _grid_row(grid, row, "Quantidade", _fmt_qty(qty)); row += 1
        _grid_row(grid, row, "Preço Médio", _fmt_brl(avg, ccy)); row += 1
        _grid_row(grid, row, "Valor Investido", _fmt_brl(inv, ccy)); row += 1
        if cur_p is not None:
            _grid_row(grid, row, "Cotação Atual", _fmt_brl(cur_p, ccy)); row += 1
        if cur_v is not None:
            _grid_row(grid, row, "Valor Atual", _fmt_brl(cur_v, ccy)); row += 1
        if ret is not None:
            ret_str, ret_color = _fmt_pct(ret)
            _grid_row(grid, row, "Rentabilidade", ret_str, ret_color); row += 1

        return frame

    def _start_fundamentals_worker(self) -> None:
        self._worker = FundamentalsWorker(self._client, self._entry["ticker"])
        self._worker.data_ready.connect(self._on_fundamentals_ready)
        self._worker.error_occurred.connect(self._on_fundamentals_error)
        self._worker.start()

    def _on_fundamentals_ready(self, data: dict) -> None:
        assert self._fund_layout is not None
        _clear_layout(self._fund_layout)

        row = 0
        has_data = False
        for key, label, tooltip in _FUND_ITEMS:
            val = data.get(key)
            if val is None:
                continue
            has_data = True

            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
            lbl.setToolTip(tooltip)
            self._fund_layout.addWidget(lbl, row, 0)

            val_lbl = QLabel(f"{float(val):.2f}")
            val_lbl.setStyleSheet(f"color: {_C_WHITE}; font-size: 13px; font-weight: 600;")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._fund_layout.addWidget(val_lbl, row, 1)

            tip_lbl = QLabel(tooltip)
            tip_lbl.setStyleSheet("color: #5A5F7A; font-size: 10px;")
            tip_lbl.setWordWrap(True)
            self._fund_layout.addWidget(tip_lbl, row + 1, 0, 1, 2)
            row += 2

        if not has_data:
            nd = QLabel("Indicadores não disponíveis para este ativo.")
            nd.setStyleSheet(f"color: {_C_MUTED}; font-size: 12px;")
            nd.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._fund_layout.addWidget(nd, 0, 0, 1, 2)

    def _on_fundamentals_error(self, msg: str) -> None:
        assert self._fund_layout is not None
        _clear_layout(self._fund_layout)
        err = QLabel(f"Erro: {msg}")
        err.setStyleSheet(f"color: {_C_NEG}; font-size: 12px;")
        err.setWordWrap(True)
        self._fund_layout.addWidget(err, 0, 0, 1, 2)

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)
        super().closeEvent(event)


# ======================================================================
# Página principal
# ======================================================================

class MarketPage(QWidget):
    """
    Página de mercado com resumo do patrimônio e tabelas por categoria.
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: PortfolioQuotesWorker | None = None
        self._sections: dict[str, dict] = {}
        self._build_ui()
        self._load_portfolio()

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
        main.setContentsMargins(32, 24, 32, 24)
        main.setSpacing(20)

        # Cabeçalho da página + botão atualizar
        hdr = QHBoxLayout()
        title = QLabel("Minha Carteira")
        title.setObjectName("sectionTitle")
        hdr.addWidget(title)
        hdr.addStretch()
        refresh_btn = QPushButton("↻ Atualizar")
        refresh_btn.setProperty("class", "primary")
        refresh_btn.clicked.connect(self._load_portfolio)
        hdr.addWidget(refresh_btn)
        main.addLayout(hdr)

        # Card de resumo
        self._summary_frame = QFrame()
        self._summary_frame.setObjectName("summaryCard")
        self._summary_frame.setVisible(False)
        summary_row = QHBoxLayout(self._summary_frame)
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(0)

        self._lbl_total   = _summary_value("—")
        self._lbl_biggest = _summary_value("—", size="15px")
        self._lbl_day_var = _summary_value("—")

        for i, (val_lbl, caption) in enumerate([
            (self._lbl_total,   "Valor Total da Carteira"),
            (self._lbl_biggest, "Maior Posição"),
            (self._lbl_day_var, "Variação do Dia"),
        ]):
            if i > 0:
                div = QFrame()
                div.setFrameShape(QFrame.Shape.VLine)
                div.setFixedWidth(1)
                div.setStyleSheet("background: #2E3250;")
                summary_row.addWidget(div)
            col = QVBoxLayout()
            col.setSpacing(4)
            col.setContentsMargins(28, 16, 28, 16)
            cap = QLabel(caption)
            cap.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px; text-transform: uppercase;")
            col.addWidget(cap)
            col.addWidget(val_lbl)
            summary_row.addLayout(col)

        summary_row.addStretch()
        main.addWidget(self._summary_frame)

        # Label de carregamento / erro
        self._loading_label = QLabel("Carregando carteira…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._loading_label)

        # Seções por categoria (criadas uma vez, exibidas conforme dados)
        for type_key, type_label in _CATEGORY_ORDER:
            sec = self._build_section(type_key, type_label)
            sec["container"].setVisible(False)
            main.addWidget(sec["container"])
            self._sections[type_key] = sec

        main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_section(self, type_key: str, type_label: str) -> dict:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        header = QLabel(type_label)
        header.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {_C_MUTED};"
            "padding: 4px 0;"
        )
        v.addWidget(header)

        table = self._build_table()
        v.addWidget(table)

        return {"container": container, "header": header, "table": table}

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(_TABLE_HEADERS))
        table.setHorizontalHeaderLabels(_TABLE_HEADERS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)

        table.itemClicked.connect(self._on_row_clicked)
        return table

    # ------------------------------------------------------------------
    # Carregamento
    # ------------------------------------------------------------------

    def _load_portfolio(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        for sec in self._sections.values():
            sec["container"].setVisible(False)
        self._summary_frame.setVisible(False)
        self._loading_label.setText("Carregando carteira…")
        self._loading_label.setVisible(True)

        self._worker = PortfolioQuotesWorker(self._client)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_data_ready(self, entries: list[dict]) -> None:
        self._loading_label.setVisible(False)

        if not entries:
            self._loading_label.setText("Nenhum ativo com posição ativa cadastrado.")
            self._loading_label.setVisible(True)
            return

        # Agrupar por tipo
        grouped: dict[str, list[dict]] = {}
        for e in entries:
            grouped.setdefault(e["asset_type"], []).append(e)

        # Calcular resumo do portfólio
        total_val = sum(
            (e["valor_atual"] if e["valor_atual"] is not None else e["valor_investido"])
            for e in entries
        )
        biggest_e = max(
            entries,
            key=lambda e: (e["valor_atual"] if e["valor_atual"] is not None else e["valor_investido"]),
        )
        biggest_v = biggest_e["valor_atual"] if biggest_e["valor_atual"] is not None else biggest_e["valor_investido"]
        biggest_pct = (biggest_v / total_val * 100) if total_val > 0 else 0.0
        biggest_label = (biggest_e.get("ticker") or biggest_e["name"][:14]) + f" ({biggest_pct:.1f}%)"

        # Variação ponderada do dia
        num = sum(
            e["valor_atual"] * e["change_pct"]
            for e in entries
            if e["change_pct"] is not None and e["valor_atual"] is not None
        )
        den = sum(
            e["valor_atual"]
            for e in entries
            if e["change_pct"] is not None and e["valor_atual"] is not None
        )
        day_var = (num / den) if den > 0 else None

        self._lbl_total.setText(_fmt_brl(total_val))
        self._lbl_biggest.setText(biggest_label)
        day_str, day_color = _fmt_pct(day_var)
        self._lbl_day_var.setText(day_str)
        self._lbl_day_var.setStyleSheet(
            f"color: {day_color}; font-size: 18px; font-weight: 700;"
        )
        self._summary_frame.setVisible(True)

        # Preencher seções
        for type_key, _ in _CATEGORY_ORDER:
            sec = self._sections[type_key]
            cat_entries = grouped.get(type_key, [])
            if not cat_entries:
                sec["container"].setVisible(False)
                continue

            cat_val  = sum(
                (e["valor_atual"] if e["valor_atual"] is not None else e["valor_investido"])
                for e in cat_entries
            )
            cat_pct = (cat_val / total_val * 100) if total_val > 0 else 0.0
            type_label = dict(_CATEGORY_ORDER)[type_key]
            sec["header"].setText(
                f"{type_label}  —  {_fmt_brl(cat_val)}  —  {cat_pct:.1f}%"
            )
            sec["header"].setStyleSheet(
                f"font-size: 13px; font-weight: 600; color: {_C_WHITE}; padding: 4px 0;"
            )

            self._fill_table(sec["table"], cat_entries)
            sec["container"].setVisible(True)

    def _fill_table(self, table: QTableWidget, entries: list[dict]) -> None:
        table.setRowCount(len(entries))

        for row, e in enumerate(entries):
            ticker = e.get("ticker") or "—"
            name   = e.get("name", "")
            qty    = e.get("net_quantity", 0.0)
            avg    = e.get("avg_price", 0.0)
            cur_p  = e.get("current_price")
            inv    = e.get("valor_investido", 0.0)
            cur_v  = e.get("valor_atual")
            ret    = e.get("rentabilidade")
            chg    = e.get("change_pct")
            ccy    = e.get("currency", "BRL")

            ret_str, ret_col = _fmt_pct(ret)
            chg_str, chg_col = _fmt_pct(chg)

            cells: list[tuple[str, str, Qt.AlignmentFlag]] = [
                (ticker,           _C_ACCENT, Qt.AlignmentFlag.AlignLeft  | Qt.AlignmentFlag.AlignVCenter),
                (name,             _C_WHITE,  Qt.AlignmentFlag.AlignLeft  | Qt.AlignmentFlag.AlignVCenter),
                (_fmt_qty(qty),    _C_WHITE,  Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (_fmt_brl(avg, ccy), _C_WHITE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (_fmt_brl(cur_p, ccy), _C_WHITE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (_fmt_brl(inv, ccy), _C_WHITE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (_fmt_brl(cur_v, ccy), _C_WHITE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (ret_str,          ret_col,   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                (chg_str,          chg_col,   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            ]

            for col, (text, color, align) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                item.setTextAlignment(align)
                if col == _COL_TICKER:
                    # Armazena o entry completo na coluna do ticker para recuperar no clique
                    item.setData(Qt.ItemDataRole.UserRole, e)
                table.setItem(row, col, item)

        # Ajusta altura da tabela ao conteúdo (sem scrollbar interno)
        header_h = table.horizontalHeader().height() or 26
        table.setFixedHeight(header_h + len(entries) * 30 + 4)

    def _on_row_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        tbl = item.tableWidget()
        if tbl is None:
            return
        first = tbl.item(row, _COL_TICKER)
        if first is None:
            return
        entry = first.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            return
        dlg = FundamentalsDialog(entry, self._client, parent=self)
        dlg.exec()

    def _on_error(self, msg: str) -> None:
        self._loading_label.setText(f"Erro ao carregar carteira: {msg}")
        self._loading_label.setVisible(True)


# ======================================================================
# Helpers de UI
# ======================================================================

def _summary_value(text: str, size: str = "18px") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {_C_WHITE}; font-size: {size}; font-weight: 700;")
    return lbl


def _grid_row(
    grid: QGridLayout,
    row: int,
    label: str,
    value: str,
    value_color: str = _C_WHITE,
) -> None:
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
    grid.addWidget(lbl, row, 0)

    val = QLabel(value)
    val.setStyleSheet(f"color: {value_color}; font-size: 13px; font-weight: 500;")
    val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    val.setWordWrap(True)
    grid.addWidget(val, row, 1)


def _clear_layout(layout: QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()
