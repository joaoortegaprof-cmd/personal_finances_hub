"""
Página de Investimentos — carteira de ativos, posições e liquidez.

Layout:
  ┌──────────────────────────────────────────────────────────────┐
  │  [Total Investido]  [Nº de Ativos]  [Distribuição por Tipo]  │  ← cards
  ├──────────────────────────────────────────────────────────────┤
  │  [+ Adicionar Ativo]  [↕ Registrar Operação]                 │  ← toolbar
  ├──────────────────────────────────────────────────────────────┤
  │  Ticker │ Nome │ Tipo │ Quantidade │ Preço Médio │ Investido  │  ← tabela
  ├──────────────────────────────────────────────────────────────┤
  │  Liquidez: D+0  │  D+1  │  D+2  │  Vencimento               │  ← breakdown
  └──────────────────────────────────────────────────────────────┘

Threading:
  InvestmentsWorker busca /assets, posições individuais, /portfolio/summary
  e /portfolio/liquidity em background antes de emitir data_ready.

  SaveAssetWorker e SaveOperationWorker executam os POSTs em background.

Nota sobre N+1 de posições:
  A API não expõe um endpoint que retorne todas as posições consolidadas
  em uma única chamada. O worker chama GET /assets/{id}/position para cada
  ativo sequencialmente. Aceitável para carteiras pessoais (< 100 ativos).
"""

from __future__ import annotations

import matplotlib
matplotlib.use("qtagg")  # noqa: E402

from datetime import date
from typing import Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.ticker_logo import TickerLogoWidget
from frontend.components.colors import (
    COLOR_ASSET      as _GREEN,
    COLOR_EXPENSE    as _RED,
    COLOR_INVESTMENT as _BLUE,
    COLOR_WARNING    as _ORANGE,
    COLOR_NEUTRAL    as _WHITE,
    COLOR_BALANCE    as _LIGHT,
    COLOR_MUTED      as _MUTED,
    COLOR_BG         as _BG_COLOR,
    COLOR_GRID       as _GRID_COLOR,
    COLOR_ASSET_RGB, COLOR_EXPENSE_RGB, COLOR_INVESTMENT_RGB,
    CATEGORY_COLOR,
)
from frontend.components.icons import icon as _svg_icon


# ======================================================================
# Workers
# ======================================================================


class InvestmentsWorker(QThread):
    """
    Busca todos os dados da página em um único round-trip de worker:
      1. GET /assets              → lista de ativos cadastrados
      2. GET /assets/{id}/position para cada ativo → posições consolidadas
      3. GET /portfolio/summary   → totais por tipo (cards de distribuição)
      4. GET /portfolio/liquidity → breakdown de liquidez
    """

    # (assets_list, positions_list, portfolio_summary, liquidity_breakdown)
    data_ready = pyqtSignal(list, list, dict, dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            assets    = self._client.get_assets()
            portfolio = self._client.get_portfolio_summary()
            liquidity = self._client.get_liquidity()

            # Etapa 6: posição + cotação de mercado para cada ativo
            positions: list[dict] = []
            for asset in assets:
                try:
                    pos = self._client.get_asset_position(asset["id"])
                except ApiError:
                    pos = {
                        "asset_id":       asset["id"],
                        "ticker":         asset.get("ticker"),
                        "name":           asset["name"],
                        "asset_type":     asset["asset_type"],
                        "net_quantity":   "0",
                        "avg_price":      "0",
                        "estimated_cost": "0",
                    }

                # Enriquece com cotação se o ativo tiver ticker
                ticker = pos.get("ticker") or asset.get("ticker")
                if ticker:
                    try:
                        quote = self._client.get_market_quote(ticker)
                        cur_price = float(quote.get("price", 0) or 0)
                        day_chg   = float(quote.get("change_pct", 0) or 0)
                        net_qty   = float(pos.get("net_quantity", 0))
                        cur_value = (
                            cur_price * net_qty if cur_price > 0
                            else float(pos.get("estimated_cost", 0))
                        )
                        pos["current_price"]  = cur_price
                        pos["current_value"]  = cur_value
                        pos["day_change_pct"] = day_chg
                    except Exception:
                        pass   # cotação é opcional — falha silenciosa

                positions.append(pos)

            self.data_ready.emit(assets, positions, portfolio, liquidity)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class UpdateAssetWorker(QThread):
    """Executa PUT /assets/{id} em background."""

    updated = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, asset_id: int, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._asset_id = asset_id
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.update_asset(self._asset_id, self._payload)
            self.updated.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class SaveAssetWorker(QThread):
    """Executa POST /assets em background."""

    saved = pyqtSignal(dict)   # retorna o ativo criado para atualizar combos
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.create_asset(self._payload)
            self.saved.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class SaveOperationWorker(QThread):
    """Executa POST /assets/{id}/operations em background."""

    saved = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, asset_id: int, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._asset_id = asset_id
        self._payload = payload

    def run(self) -> None:
        try:
            self._client.create_asset_operation(self._asset_id, self._payload)
            self.saved.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Lista estática de tickers B3 (top 100 ações + top 50 FIIs + ETFs)
# ======================================================================

_B3_STOCKS = [
    "ABEV3","ALOS3","ALPA4","ASAI3","AURE3","BBAS3","BBDC4","BEEF3","BHIA3","BPAC11",
    "BRAP4","BRKM5","BRFS3","CBAV3","CCRO3","CEAB3","CMIG4","CMIN3","COGN3","CPLE6",
    "CSAN3","CSNA3","CVCB3","CYRE3","DXCO3","ECOO11","EGIA3","EGIE3","ELET3","ELET6",
    "EMBR3","EQTL3","EZTC3","FLRY3","GFSA3","GGBR4","GRND3","HAPV3","HYPE3","IFCM3",
    "IGTI11","ITSA4","ITUB4","JBSS3","JHSF3","KLBN11","LAVV3","LEVE3","LREN3","LWSA3",
    "MATD3","MDNE3","MDIA3","MGLU3","MILS3","MLAS3","MOVI3","MRFG3","MRVE3","MULT3",
    "NATU3","NTCO3","PCAR3","PDGR3","PETZ3","POSI3","PRIO3","QUAL3","RADL3","RAIL3",
    "RAIZ4","RANI3","RDOR3","RENT3","RLOG3","SAPR11","SANB11","SBSP3","SLCE3","SMLS3",
    "SMTO3","SUZB3","TAEE11","TGMA3","TIMS3","TOTS3","UGPA3","VALE3","VBBR3","VIVT3",
    "VVAR3","WEGE3","YDUQ3","PETR3","PETR4","PRNR3","BRSR6","CGAS3","CGAS5","ENBR3",
]

_B3_FIIS = [
    "AFHI11","ALZR11","BCFF11","BPML11","BRCO11","BRCR11","BRIP11","BTCI11","BTLG11",
    "CPFF11","CPTS11","DEVA11","FCFL11","FIIB11","GGRC11","HFOF11","HGBS11","HGCR11",
    "HGLG11","HGPO11","HGRE11","HSML11","IFIE11","IRDM11","JSRE11","KNCR11","KNRI11",
    "KNSC11","MANA11","MCCI11","MXRF11","PATC11","PVBI11","QAGR11","RBRF11","RBVA11",
    "RBBV11","RBRR11","RECR11","RNGO11","SNFF11","TGAR11","TSNC11","TRXF11","VGHF11",
    "VGIR11","VINO11","VISC11","VSLH11","XPLG11","XPML11",
]

_B3_ETFS = [
    "BBSD11","BOVA11","BOVB11","DIVO11","ECOO11","FIXA11","FIND11","GOVE11","HASH11",
    "IFRA11","IRFM11","ISUS11","IVVB11","LFTS11","MATB11","NTNB11","PIBB11","SMAL11",
    "SPXI11","XFIX11",
]

# Tipos que usam dropdown com lista B3
_TICKER_DROPDOWN_TYPES = {"acao", "fii", "etf"}


class TickerNameWorker(QThread):
    """Busca cotação de um ticker para obter o nome da empresa."""

    name_found = pyqtSignal(str)
    not_found = pyqtSignal()

    def __init__(self, client: ApiClient, ticker: str) -> None:
        super().__init__()
        self._client = client
        self._ticker = ticker

    def run(self) -> None:
        try:
            quote = self._client.get_market_quote(self._ticker)
            name = quote.get("name") or quote.get("long_name") or ""
            if name:
                self.name_found.emit(name)
            else:
                self.not_found.emit()
        except ApiError:
            self.not_found.emit()
        except Exception:
            self.not_found.emit()


# ======================================================================
# Dados de domínio (enums mapeados para exibição)
# ======================================================================

_ASSET_TYPE_LABELS = {
    "Ação (B3)": "acao",
    "FII": "fii",
    "ETF": "etf",
    "Tesouro Direto": "tesouro_direto",
    "Renda Fixa (CDB/LCI/LCA)": "renda_fixa",
    "Criptomoeda": "criptomoeda",
    "Ação Internacional": "acao_internacional",
    "Previdência": "previdencia",
    "Outros": "outros",
}
_ASSET_TYPE_DISPLAY = {v: k for k, v in _ASSET_TYPE_LABELS.items()}

_LIQUIDITY_LABELS = {
    "D+0 (imediato)": "D+0",
    "D+1 (dia útil)": "D+1",
    "D+2 (liquidação B3)": "D+2",
    "Vencimento": "vencimento",
}
_LIQUIDITY_DISPLAY = {v: k for k, v in _LIQUIDITY_LABELS.items()}

_INDEXER_LABELS = {
    "Nenhum": None,
    "IPCA+": "IPCA+",
    "CDI": "CDI",
    "Selic": "Selic",
    "Prefixado": "Prefixado",
    "IGPM+": "IGPM+",
}

_OPERATION_TYPE_LABELS = {
    "Compra": "compra",
    "Venda": "venda",
    "Bonificação": "bonificacao",
    "Desdobramento": "desdobramento",
    "Grupamento": "grupamento",
    "Amortização": "amortizacao",
}


# ======================================================================
# Diálogos
# ======================================================================


class NewAssetDialog(QDialog):
    """
    Formulário para cadastrar um novo ativo na carteira.

    Para STOCK/FII/ETF: ticker via QComboBox com QCompleter (lista B3 estática).
    Para outros tipos: ticker via campo de texto livre.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Adicionar Ativo")
        self.setMinimumWidth(480)
        self._client = ApiClient()
        self._name_worker: TickerNameWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("Novo Ativo")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._populate_asset_form(form)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setProperty("class", "success")
            save_btn.style().unpolish(save_btn)
            save_btn.style().polish(save_btn)
        layout.addWidget(buttons)

        self._on_type_changed(self._type_combo.currentText())

    def _populate_asset_form(self, form: QFormLayout) -> None:
        """Adiciona todos os campos do ativo a um QFormLayout existente."""
        self._type_combo = QComboBox()
        for label in _ASSET_TYPE_LABELS:
            self._type_combo.addItem(label)
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        form.addRow("Tipo *", self._type_combo)

        ticker_row = QHBoxLayout()
        ticker_row.setSpacing(6)
        self._ticker_combo = QComboBox()
        self._ticker_combo.setEditable(True)
        self._ticker_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._ticker_combo.setMinimumWidth(180)
        self._ticker_combo.activated.connect(self._on_ticker_activated)
        ticker_row.addWidget(self._ticker_combo, 1)
        lookup_btn = QPushButton()
        lookup_btn.setIcon(_svg_icon("refresh", _LIGHT, 14))
        lookup_btn.setToolTip("Buscar nome do ticker")
        lookup_btn.setFixedWidth(36)
        lookup_btn.clicked.connect(self._lookup_name)
        ticker_row.addWidget(lookup_btn)
        ticker_widget = QWidget()
        ticker_widget.setLayout(ticker_row)
        form.addRow("Ticker", ticker_widget)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Ex: Petrobras PN, CDB Nubank 110% CDI")
        form.addRow("Nome *", self._name)

        self._liquidity_combo = QComboBox()
        for label in _LIQUIDITY_LABELS:
            self._liquidity_combo.addItem(label)
        self._liquidity_combo.setCurrentIndex(2)
        form.addRow("Liquidez", self._liquidity_combo)

        self._emergency_fund_cb = QCheckBox("Compõe reserva de emergência")
        self._emergency_fund_cb.setToolTip(
            "Apenas ativos com liquidez D+0 podem compor a reserva de emergência."
        )
        form.addRow("", self._emergency_fund_cb)

        self._indexer_combo = QComboBox()
        for label in _INDEXER_LABELS:
            self._indexer_combo.addItem(label)
        form.addRow("Indexador", self._indexer_combo)

        self._maturity = QDateEdit()
        self._maturity.setCalendarPopup(True)
        self._maturity.setSpecialValueText("Sem vencimento")
        self._maturity.setDisplayFormat("dd/MM/yyyy")
        self._maturity.setDate(QDate(2099, 12, 31))
        self._has_maturity = QCheckBox("Definir data de vencimento")
        self._has_maturity.toggled.connect(self._maturity.setEnabled)
        self._maturity.setEnabled(False)
        form.addRow(self._has_maturity, self._maturity)

        self._sector = QLineEdit()
        self._sector.setPlaceholderText("Ex: Energia, Financeiro, Imóveis (opcional)")
        form.addRow("Setor", self._sector)

        # Alocação-alvo: % da carteira que este ativo deve representar.
        # Quando o desvio real superar 5 p.p., o alerta de rebalanceamento é disparado.
        self._target_alloc = QDoubleSpinBox()
        self._target_alloc.setRange(0.0, 100.0)
        self._target_alloc.setDecimals(1)
        self._target_alloc.setSuffix(" %")
        self._target_alloc.setSpecialValueText("Sem meta")   # 0.0 → sem meta
        self._target_alloc.setToolTip(
            "Percentual-alvo na carteira. "
            "Alerta de rebalanceamento dispara quando o desvio superar 5 p.p."
        )
        form.addRow("Alocação-alvo", self._target_alloc)

        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("Observações opcionais…")
        self._notes.setMaximumHeight(72)
        form.addRow("Observações", self._notes)

    def _on_type_changed(self, type_label: str) -> None:
        api_type = _ASSET_TYPE_LABELS.get(type_label, "")
        self._ticker_combo.blockSignals(True)
        self._ticker_combo.clear()

        if api_type in _TICKER_DROPDOWN_TYPES:
            if api_type == "acao":
                items = _B3_STOCKS
            elif api_type == "fii":
                items = _B3_FIIS
            else:
                items = _B3_ETFS
            self._ticker_combo.addItems(items)
            self._ticker_combo.setCurrentIndex(-1)
            self._ticker_combo.lineEdit().setPlaceholderText("Selecione ou digite o ticker…")

            completer = QCompleter(items, self._ticker_combo)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self._ticker_combo.setCompleter(completer)
        else:
            self._ticker_combo.setCompleter(None)
            self._ticker_combo.lineEdit().setPlaceholderText("Ex: BTC, TESOURO-IPCA-2035 (opcional)")

        self._ticker_combo.blockSignals(False)

    def _on_ticker_activated(self, index: int) -> None:
        self._lookup_name()

    def _lookup_name(self) -> None:
        ticker = self._ticker_combo.currentText().strip().upper()
        if not ticker:
            return
        if self._name_worker and self._name_worker.isRunning():
            return
        self._name.setPlaceholderText("Buscando nome…")
        self._name_worker = TickerNameWorker(self._client, ticker)
        self._name_worker.name_found.connect(self._on_name_found)
        self._name_worker.not_found.connect(
            lambda: self._name.setPlaceholderText("Nome não encontrado — informe manualmente")
        )
        self._name_worker.start()

    def _on_name_found(self, name: str) -> None:
        if not self._name.text().strip():
            self._name.setText(name)
        self._name.setPlaceholderText("Ex: Petrobras PN, CDB Nubank 110% CDI")

    def _on_accept(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe o nome do ativo.")
            self._name.setFocus()
            return

        if self._emergency_fund_cb.isChecked():
            liq_value = _LIQUIDITY_LABELS[self._liquidity_combo.currentText()]
            if liq_value != "D+0":
                QMessageBox.warning(
                    self,
                    "Liquidez incompatível",
                    "Apenas ativos com liquidez D+0 podem compor a reserva de emergência.\n"
                    "Altere a liquidez ou desmarque esta opção.",
                )
                return

        self.accept()

    def get_payload(self) -> dict[str, Any]:
        """Monta o payload para POST /assets."""
        qdate = self._maturity.date()
        maturity = (
            date(qdate.year(), qdate.month(), qdate.day()).isoformat()
            if self._has_maturity.isChecked()
            else None
        )

        indexer_key = self._indexer_combo.currentText()
        indexer_value = _INDEXER_LABELS[indexer_key]

        liq_key = self._liquidity_combo.currentText()
        liq_value = _LIQUIDITY_LABELS[liq_key]

        payload: dict[str, Any] = {
            "name": self._name.text().strip(),
            "asset_type": _ASSET_TYPE_LABELS[self._type_combo.currentText()],
            "liquidity": liq_value,
            "is_emergency_fund": self._emergency_fund_cb.isChecked(),
        }

        ticker = self._ticker_combo.currentText().strip().upper()
        if ticker:
            payload["ticker"] = ticker
        if indexer_value:
            payload["indexer"] = indexer_value
        if maturity:
            payload["maturity_date"] = maturity
        sector = self._sector.text().strip()
        if sector:
            payload["sector"] = sector
        notes = self._notes.toPlainText().strip()
        if notes:
            payload["notes"] = notes
        # 0.0 = "Sem meta" (specialValueText) → envia null para a API
        target = self._target_alloc.value()
        payload["target_allocation_pct"] = target if target > 0 else None

        return payload


class NewOperationDialog(QDialog):
    """
    Formulário para registrar uma compra, venda ou evento corporativo.

    Recebe a lista de ativos já cadastrados para popular o combo de seleção.
    O campo "Quantidade" aceita decimais para FIIs e ETFs (frações de cota).
    """

    def __init__(self, assets: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Registrar Operação")
        self.setMinimumWidth(460)
        self._assets = assets
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("Registrar Operação")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Seleção do ativo
        self._asset_combo = QComboBox()
        self._asset_combo.setMinimumWidth(200)
        for asset in self._assets:
            ticker = asset.get("ticker") or ""
            label = f"{ticker} — {asset['name']}" if ticker else asset["name"]
            self._asset_combo.addItem(label, userData=asset["id"])
        form.addRow("Ativo *", self._asset_combo)

        # Tipo de operação
        self._op_type = QComboBox()
        for label in _OPERATION_TYPE_LABELS:
            self._op_type.addItem(label)
        form.addRow("Operação *", self._op_type)

        # Data da operação
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Data *", self._date_edit)

        # Quantidade (aceita decimais para ativos fracionários)
        self._quantity = QDoubleSpinBox()
        self._quantity.setRange(0.000001, 9_999_999.0)
        self._quantity.setDecimals(6)
        self._quantity.setValue(1.0)
        form.addRow("Quantidade *", self._quantity)

        # Preço unitário
        self._unit_price = QDoubleSpinBox()
        self._unit_price.setRange(0.0, 999_999.99)
        self._unit_price.setDecimals(2)
        self._unit_price.setPrefix("R$ ")
        form.addRow("Preço unitário *", self._unit_price)

        # Taxas e corretagem
        self._fees = QDoubleSpinBox()
        self._fees.setRange(0.0, 99_999.99)
        self._fees.setDecimals(2)
        self._fees.setPrefix("R$ ")
        form.addRow("Taxas/Corretagem", self._fees)

        # Observações
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("Observações opcionais…")
        self._notes.setMaximumHeight(72)
        form.addRow("Observações", self._notes)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setProperty("class", "success")
            save_btn.style().unpolish(save_btn)
            save_btn.style().polish(save_btn)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if self._asset_combo.count() == 0:
            QMessageBox.warning(self, "Sem ativos", "Cadastre um ativo antes de registrar uma operação.")
            return
        if self._quantity.value() <= 0:
            QMessageBox.warning(self, "Campo obrigatório", "A quantidade deve ser maior que zero.")
            return
        self.accept()

    def get_asset_id(self) -> int:
        return self._asset_combo.currentData()

    def get_payload(self) -> dict[str, Any]:
        """Monta o payload para POST /assets/{id}/operations."""
        qdate = self._date_edit.date()
        op_date = date(qdate.year(), qdate.month(), qdate.day())

        return {
            "operation_date": op_date.isoformat(),
            "quantity": f"{self._quantity.value():.6f}",
            "unit_price": f"{self._unit_price.value():.2f}",
            "fees": f"{self._fees.value():.2f}",
            "operation_type": _OPERATION_TYPE_LABELS[self._op_type.currentText()],
            "notes": self._notes.toPlainText().strip() or None,
        }


class AdjustPositionWorker(QThread):
    """
    Executa POST /assets/{id}/adjust-position em background.

    O backend zera a posição atual (SELL com qty negativa) e cria
    uma nova BUY — tudo em uma única transação atômica.
    """

    done           = pyqtSignal(dict)   # retorna o PositionAdjustOut
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, asset_id: int, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client   = client
        self._asset_id = asset_id
        self._payload  = payload

    def run(self) -> None:
        try:
            result = self._client.adjust_position(self._asset_id, self._payload)
            self.done.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


_ADJUST_REASON_OPTIONS = [
    "Correção manual",
    "Dividendos em cotas / Bonificação",
    "Migração de corretora",
    "Reconciliação de extrato",
    "Erro de lançamento anterior",
    "Outro",
]


class EditAssetDialog(NewAssetDialog):
    """
    Formulário modal para editar um ativo — duas abas:
      1. Dados do Ativo  — mesmos campos do cadastro
      2. Ajuste de Posição — carrega posição atual automaticamente,
         permite corrigir qty/preço médio e exibe preview do resultado.
    """

    def __init__(
        self,
        asset: dict[str, Any],
        client: ApiClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        self._edit_client = client
        # Guarda posição atual para comparação (carregada em _prefill)
        self._current_qty: float = 0.0
        self._current_avg: float = 0.0
        super().__init__(parent)
        self.setWindowTitle("Editar Ativo")
        self._asset_id = asset["id"]
        self._prefill(asset)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)

        tabs = QTabWidget()

        # ── Aba 1: Dados do Ativo ─────────────────────────────────────
        tab1 = QWidget()
        t1_layout = QVBoxLayout(tab1)
        t1_layout.setContentsMargins(0, 12, 0, 0)
        t1_layout.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._populate_asset_form(form)
        t1_layout.addLayout(form)
        t1_layout.addStretch()
        tabs.addTab(tab1, "Dados do Ativo")

        # ── Aba 2: Ajuste de Posição ──────────────────────────────────
        tab2 = QWidget()
        t2_layout = QVBoxLayout(tab2)
        t2_layout.setContentsMargins(0, 12, 0, 0)
        t2_layout.setSpacing(12)

        # Painel: posição atual (carregada automaticamente)
        cur_frame = QFrame()
        cur_frame.setStyleSheet(
            "QFrame { background: #1E2138; border-radius: 6px; border: 1px solid #2E3250; }"
        )
        cur_lay = QVBoxLayout(cur_frame)
        cur_lay.setContentsMargins(16, 12, 16, 12)
        cur_lay.setSpacing(4)

        cur_title = QLabel("Posição Atual (calculada pelo histórico)")
        cur_title.setStyleSheet("color: #8B90A7; font-size: 11px; font-weight: 600;")
        cur_lay.addWidget(cur_title)

        self._cur_qty_lbl = QLabel("Quantidade:  —    •    Preço Médio: —")
        self._cur_qty_lbl.setStyleSheet("color: #C8CAD8; font-size: 12px;")
        cur_lay.addWidget(self._cur_qty_lbl)

        self._cur_cost_lbl = QLabel("Custo Total: —")
        self._cur_cost_lbl.setStyleSheet("color: #8B90A7; font-size: 11px;")
        cur_lay.addWidget(self._cur_cost_lbl)

        t2_layout.addWidget(cur_frame)

        # Formulário de ajuste
        adj_title = QLabel("Ajustar Para")
        adj_title.setStyleSheet("color: #E8EAED; font-size: 12px; font-weight: 600;")
        t2_layout.addWidget(adj_title)

        pos_form = QFormLayout()
        pos_form.setSpacing(10)
        pos_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._pos_qty = QDoubleSpinBox()
        self._pos_qty.setRange(0.0, 99_999_999.0)
        self._pos_qty.setDecimals(6)
        self._pos_qty.setValue(0.0)
        self._pos_qty.valueChanged.connect(self._update_preview)
        pos_form.addRow("Quantidade *", self._pos_qty)

        self._pos_avg_price = QDoubleSpinBox()
        self._pos_avg_price.setRange(0.0, 9_999_999.99)
        self._pos_avg_price.setDecimals(4)
        self._pos_avg_price.setPrefix("R$ ")
        self._pos_avg_price.setValue(0.0)
        self._pos_avg_price.valueChanged.connect(self._update_preview)
        pos_form.addRow("Preço Médio *", self._pos_avg_price)

        self._pos_date = QDateEdit()
        self._pos_date.setCalendarPopup(True)
        self._pos_date.setDisplayFormat("dd/MM/yyyy")
        self._pos_date.setDate(QDate.currentDate())
        pos_form.addRow("Data do Ajuste", self._pos_date)

        self._pos_reason = QComboBox()
        for opt in _ADJUST_REASON_OPTIONS:
            self._pos_reason.addItem(opt)
        pos_form.addRow("Motivo", self._pos_reason)

        t2_layout.addLayout(pos_form)

        # Painel de preview do resultado
        prev_frame = QFrame()
        prev_frame.setStyleSheet(
            "QFrame { background: #1A2E28; border-radius: 6px; border: 1px solid #1E4D3A; }"
        )
        prev_lay = QVBoxLayout(prev_frame)
        prev_lay.setContentsMargins(16, 12, 16, 12)
        prev_lay.setSpacing(4)

        prev_title = QLabel("Resultado do Ajuste")
        prev_title.setStyleSheet("color: #00C896; font-size: 11px; font-weight: 600;")
        prev_lay.addWidget(prev_title)

        self._prev_main_lbl = QLabel("—")
        self._prev_main_lbl.setStyleSheet("color: #E8EAED; font-size: 12px;")
        prev_lay.addWidget(self._prev_main_lbl)

        self._prev_delta_lbl = QLabel("")
        self._prev_delta_lbl.setStyleSheet("color: #8B90A7; font-size: 11px;")
        prev_lay.addWidget(self._prev_delta_lbl)

        t2_layout.addWidget(prev_frame)

        warn = QLabel(
            "⚠  Operações de ajuste são adicionadas ao histórico e não apagam "
            "lançamentos anteriores. Para registro de compra/venda real, use "
            "\"Registrar Operação\"."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #6B7080; font-size: 10px;")
        t2_layout.addWidget(warn)

        t2_layout.addStretch()
        tabs.addTab(tab2, "Ajuste de Posição")

        outer.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setProperty("class", "success")
            save_btn.style().unpolish(save_btn)
            save_btn.style().polish(save_btn)
        outer.addWidget(buttons)

        self._on_type_changed(self._type_combo.currentText())

    def _load_current_position(self) -> None:
        """Carrega a posição atual via API e atualiza os labels do painel."""
        if not self._edit_client:
            return
        try:
            pos = self._edit_client.get_asset_position(self._asset_id)
            self._current_qty = float(pos.get("net_quantity", 0))
            self._current_avg = float(pos.get("avg_price", 0))
            cur_cost = self._current_qty * self._current_avg

            self._cur_qty_lbl.setText(
                f"Quantidade: {_fmt_qty(self._current_qty)}    •    "
                f"Preço Médio: {_fmt_brl(self._current_avg)}"
            )
            self._cur_cost_lbl.setText(f"Custo Total: {_fmt_brl(cur_cost)}")

            # Pré-preenche o formulário com os valores atuais
            self._pos_qty.setValue(self._current_qty)
            self._pos_avg_price.setValue(self._current_avg)
            self._update_preview()
        except Exception:
            self._cur_qty_lbl.setText("Não foi possível carregar a posição atual.")

    def _update_preview(self) -> None:
        """Recalcula o painel de preview toda vez que qty ou avg mudam."""
        new_qty = self._pos_qty.value()
        new_avg = self._pos_avg_price.value()
        new_cost = new_qty * new_avg

        self._prev_main_lbl.setText(
            f"➜  {_fmt_qty(new_qty)} cotas  @  {_fmt_brl(new_avg)}  =  {_fmt_brl(new_cost)}"
        )

        delta_qty = new_qty - self._current_qty
        sign = "+" if delta_qty >= 0 else ""
        cur_cost = self._current_qty * self._current_avg
        delta_cost = new_cost - cur_cost

        self._prev_delta_lbl.setText(
            f"Δ Qtd: {sign}{_fmt_qty(delta_qty)}  •  Δ Custo: {'+' if delta_cost >= 0 else ''}{_fmt_brl(delta_cost)}"
        )

    def get_position_data(self) -> dict[str, Any] | None:
        """
        Retorna payload para adjust-position se os valores divergem da posição
        atual carregada. Retorna None se não há alteração significativa.
        """
        new_qty = self._pos_qty.value()
        new_avg = self._pos_avg_price.value()

        # Considera mudança significativa se diferir em mais de 0.000001
        qty_changed = abs(new_qty - self._current_qty) > 1e-6
        avg_changed = abs(new_avg - self._current_avg) > 1e-4

        if not qty_changed and not avg_changed:
            return None

        qd = self._pos_date.date()
        reason = self._pos_reason.currentText()
        return {
            "target_quantity": f"{new_qty:.6f}",
            "target_avg_price": f"{new_avg:.4f}",
            "op_date": date(qd.year(), qd.month(), qd.day()).isoformat(),
            "reason": reason,
        }

    def _prefill(self, asset: dict[str, Any]) -> None:
        ticker = asset.get("ticker") or ""
        self._ticker_combo.setCurrentText(ticker)
        self._name.setText(asset.get("name", ""))

        atype = asset.get("asset_type", "acao")
        display = _ASSET_TYPE_DISPLAY.get(atype, "Ação (B3)")
        idx = self._type_combo.findText(display)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)

        liq = asset.get("liquidity", "D+2")
        liq_display = _LIQUIDITY_DISPLAY.get(liq, "D+2 (liquidação B3)")
        idx = self._liquidity_combo.findText(liq_display)
        if idx >= 0:
            self._liquidity_combo.setCurrentIndex(idx)

        indexer = asset.get("indexer")
        if indexer:
            idx = self._indexer_combo.findText(indexer)
            if idx >= 0:
                self._indexer_combo.setCurrentIndex(idx)

        maturity_iso = asset.get("maturity_date")
        if maturity_iso:
            try:
                d = date.fromisoformat(maturity_iso)
                self._maturity.setDate(QDate(d.year, d.month, d.day))
                self._has_maturity.setChecked(True)
            except (ValueError, TypeError):
                pass

        self._sector.setText(asset.get("sector") or "")
        self._notes.setPlainText(asset.get("notes") or "")
        self._emergency_fund_cb.setChecked(bool(asset.get("is_emergency_fund", False)))
        target = asset.get("target_allocation_pct")
        self._target_alloc.setValue(float(target) if target else 0.0)

        # Auto-carrega a posição atual após preencher os dados cadastrais
        self._load_current_position()


# ======================================================================
# Página principal de Investimentos
# ======================================================================

# ======================================================================
# Workers — Proventos
# ======================================================================

_DIVIDEND_TYPE_LABELS = {
    "Dividendo": "dividendo",
    "JCP": "jcp",
    "Rendimento FII": "rendimento_fii",
    "Amortização": "amortizacao",
    "Bonificação": "bonificacao",
}
_DIVIDEND_TYPE_DISPLAY = {v: k for k, v in _DIVIDEND_TYPE_LABELS.items()}


class DividendWorker(QThread):
    """Busca lista de proventos e resumo em background."""

    data_ready = pyqtSignal(list, dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            dividends = self._client.get_dividends()
            summary = self._client.get_dividends_summary()
            self.data_ready.emit(dividends, summary)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class SaveDividendWorker(QThread):
    """Executa POST /dividends em background."""

    saved = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._payload = payload

    def run(self) -> None:
        try:
            self._client.create_dividend(self._payload)
            self.saved.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class NewDividendDialog(QDialog):
    """Formulário para registrar um provento recebido."""

    def __init__(self, assets: list[dict], client: ApiClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Registrar Provento")
        self.setMinimumWidth(460)
        self._assets = assets
        self._client = client
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("Registrar Provento")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._asset_combo = QComboBox()
        self._asset_combo.setMinimumWidth(200)
        for asset in self._assets:
            ticker = asset.get("ticker") or ""
            label = f"{ticker} — {asset['name']}" if ticker else asset["name"]
            self._asset_combo.addItem(label, userData=asset["id"])
        self._asset_combo.currentIndexChanged.connect(self._update_total)
        form.addRow("Ativo *", self._asset_combo)

        self._div_type = QComboBox()
        for label in _DIVIDEND_TYPE_LABELS:
            self._div_type.addItem(label)
        self._div_type.currentTextChanged.connect(self._on_type_changed)
        form.addRow("Tipo *", self._div_type)

        self._payment_date = QDateEdit()
        self._payment_date.setCalendarPopup(True)
        self._payment_date.setDate(QDate.currentDate())
        self._payment_date.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Data de Pagamento *", self._payment_date)

        self._record_date = QDateEdit()
        self._record_date.setCalendarPopup(True)
        self._record_date.setDate(QDate.currentDate())
        self._record_date.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Data Ex (opcional)", self._record_date)

        self._amount_per_unit = QDoubleSpinBox()
        self._amount_per_unit.setRange(0.0, 999_999.999999)
        self._amount_per_unit.setDecimals(6)
        self._amount_per_unit.setPrefix("R$ ")
        self._amount_per_unit.valueChanged.connect(self._update_total)
        form.addRow("Valor por Cota/Ação *", self._amount_per_unit)

        self._total_amount = QDoubleSpinBox()
        self._total_amount.setRange(0.0, 9_999_999.99)
        self._total_amount.setDecimals(2)
        self._total_amount.setPrefix("R$ ")
        form.addRow("Total Recebido *", self._total_amount)

        self._tax_row_label = QLabel("IR Retido na Fonte")
        self._tax_withheld = QDoubleSpinBox()
        self._tax_withheld.setRange(0.0, 999_999.99)
        self._tax_withheld.setDecimals(2)
        self._tax_withheld.setPrefix("R$ ")
        form.addRow(self._tax_row_label, self._tax_withheld)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setProperty("class", "success")
            save_btn.style().unpolish(save_btn)
            save_btn.style().polish(save_btn)
        layout.addWidget(buttons)

        self._on_type_changed(self._div_type.currentText())

    def _on_type_changed(self, label: str) -> None:
        is_jcp = _DIVIDEND_TYPE_LABELS.get(label, "") == "jcp"
        self._tax_withheld.setEnabled(True)
        if is_jcp:
            self._tax_row_label.setText("IR Retido (JCP — 15%)")
            # Sugere 15% do total como IR retido
            self._tax_withheld.setValue(self._total_amount.value() * 0.15)
        else:
            self._tax_row_label.setText("IR Retido na Fonte")

    def _update_total(self) -> None:
        # Tenta buscar a posição atual do ativo para sugerir o total
        # Cálculo aproximado: valor_por_cota × posição_aproximada
        # Como não temos a posição em mãos aqui, apenas deixa o usuário preencher
        pass

    def _on_accept(self) -> None:
        if self._asset_combo.count() == 0:
            QMessageBox.warning(self, "Sem ativos", "Cadastre um ativo antes.")
            return
        if self._total_amount.value() <= 0:
            QMessageBox.warning(self, "Campo obrigatório", "Informe o total recebido.")
            return
        self.accept()

    def get_payload(self) -> dict[str, Any]:
        qd = self._payment_date.date()
        payment_date = date(qd.year(), qd.month(), qd.day()).isoformat()
        qd2 = self._record_date.date()
        record_date = date(qd2.year(), qd2.month(), qd2.day()).isoformat()

        div_type = _DIVIDEND_TYPE_LABELS[self._div_type.currentText()]
        is_taxable = div_type == "jcp"

        return {
            "asset_id": self._asset_combo.currentData(),
            "payment_date": payment_date,
            "record_date": record_date,
            "amount_per_unit": f"{self._amount_per_unit.value():.6f}",
            "total_amount": f"{self._total_amount.value():.2f}",
            "dividend_type": div_type,
            "is_taxable": is_taxable,
            "tax_withheld": f"{self._tax_withheld.value():.2f}",
        }


# ======================================================================
# Canvas de gráfico de proventos mensais
# ======================================================================


class DividendsBarCanvas(FigureCanvasQTAgg):
    """Barras dos proventos mensais dos últimos 12 meses."""

    def __init__(self, parent: QWidget | None = None) -> None:
        fig = Figure(figsize=(10, 3), facecolor=_BG_COLOR)
        self.axes = fig.add_subplot(111)
        super().__init__(fig)
        self._style_axes()

    def _style_axes(self) -> None:
        ax = self.axes
        ax.set_facecolor(_BG_COLOR)
        ax.tick_params(colors=_MUTED, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(_GRID_COLOR)

    def plot(self, by_month: dict[str, float]) -> None:
        self.axes.cla()
        self._style_axes()
        if not by_month:
            self.draw()
            return

        labels = list(by_month.keys())
        values = [float(v) for v in by_month.values()]
        colors = [_GREEN if v > 0 else _MUTED for v in values]

        x_pos = range(len(labels))
        bars = self.axes.bar(x_pos, values, color=colors, width=0.6)
        self.axes.set_xticks(list(x_pos))
        short_labels = [lbl[5:] if len(lbl) >= 7 else lbl for lbl in labels]
        self.axes.set_xticklabels(short_labels, rotation=30, ha="right", fontsize=7)
        self.axes.set_ylabel("R$", color=_MUTED, fontsize=8)
        self.axes.yaxis.set_tick_params(labelcolor=_MUTED)

        # Anotação de valor em cima de cada barra
        for bar, val in zip(bars, values):
            if val > 0:
                self.axes.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(values) * 0.01,
                    f"R${val:.0f}",
                    ha="center", va="bottom",
                    color=_WHITE, fontsize=6,
                )

        self.figure.tight_layout()
        self.draw()


# Colunas da tabela de ativos
_COL_TICKER = 0
_COL_NAME = 1
_COL_TYPE = 2
_COL_QTY = 3
_COL_AVG = 4
_COL_COST = 5
_COL_ACTIONS = 6


class RiskWorker(QThread):
    """Busca GET /portfolio/risk-analysis em background."""
    data_ready     = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            self.data_ready.emit(self._client.get_portfolio_risk())
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class RentabilidadeWorker(QThread):
    """
    Coleta dados para o gráfico de rentabilidade:
      1. GET /portfolio/opportunity-cost → portfolio_return_pct, cdi_return_pct
      2. GET /market/benchmark-comparison?ticker=BOVA11 → retorno do IBOV no período
    """

    data_ready     = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            opp = self._client.get_opportunity_cost()
            ibov: dict = {}
            try:
                ibov = self._client.get_benchmark_comparison("BOVA11", "1y")
            except Exception:
                pass   # IBOV é opcional
            self.data_ready.emit({"opportunity": opp, "ibov": ibov})
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Canvas de rentabilidade — portfolio vs CDI vs IBOV
# ======================================================================


class RentabilidadeCanvas(FigureCanvasQTAgg):
    """
    Gráfico de barras horizontais comparando o retorno da carteira de renda
    variável com CDI e IBOV no mesmo período.

    Barra verde  = retorno acima do CDI (alpha positivo)
    Barra vermelha = retorno abaixo do CDI
    """

    _BG    = _BG_COLOR
    _GRID  = _GRID_COLOR

    def __init__(self, parent=None) -> None:
        fig = Figure(figsize=(8, 2.8), facecolor=self._BG)
        super().__init__(fig)
        self.setParent(parent)
        self._fig = fig
        self._ax  = fig.add_subplot(111)
        self._draw_empty()

    def _draw_empty(self) -> None:
        self._ax.clear()
        self._ax.set_facecolor(self._BG)
        self._ax.text(
            0.5, 0.5, "Nenhum dado de rentabilidade disponível",
            ha="center", va="center", color=_MUTED,
            fontsize=10, transform=self._ax.transAxes,
        )
        self._ax.axis("off")
        self._fig.tight_layout()
        self.draw()

    def plot(self, port_pct: float | None, cdi_pct: float | None, ibov_pct: float | None) -> None:
        """Redesenha com os retornos percentuais de cada benchmark."""
        self._ax.clear()
        self._ax.set_facecolor(self._BG)

        entries: list[tuple[str, float, str]] = []
        if port_pct is not None:
            clr = _GREEN if (cdi_pct is None or port_pct >= cdi_pct) else _RED
            entries.append(("Carteira (equities)", port_pct, clr))
        if cdi_pct is not None:
            entries.append(("CDI", cdi_pct, _BLUE))
        if ibov_pct is not None:
            clr_ibov = _GREEN if ibov_pct >= 0 else _RED
            entries.append(("IBOVESPA (BOVA11)", ibov_pct, clr_ibov))

        if not entries:
            self._draw_empty()
            return

        labels = [e[0] for e in entries]
        values = [e[1] for e in entries]
        colors = [e[2] for e in entries]
        y_pos  = range(len(entries))

        bars = self._ax.barh(list(y_pos), values, color=colors, height=0.5, alpha=0.88)

        # Anotação de valor na ponta de cada barra
        max_abs = max(abs(v) for v in values) or 1
        for bar, val in zip(bars, values):
            x_pos_ann = val + max_abs * 0.02 if val >= 0 else val - max_abs * 0.02
            ha = "left" if val >= 0 else "right"
            self._ax.text(
                x_pos_ann, bar.get_y() + bar.get_height() / 2,
                f"{val:+.2f}%",
                va="center", ha=ha,
                color=_WHITE, fontsize=9, fontweight="bold",
            )

        self._ax.set_yticks(list(y_pos))
        self._ax.set_yticklabels(labels, color=_LIGHT, fontsize=9)
        self._ax.axvline(0, color="#4A4D6A", linewidth=0.8)
        self._ax.set_xlabel("Retorno no período (%)", color=_MUTED, fontsize=8)
        self._ax.tick_params(axis="x", colors=_MUTED, labelsize=8)
        self._ax.tick_params(axis="y", left=False)
        for spine in self._ax.spines.values():
            spine.set_visible(False)
        self._ax.set_title("Rentabilidade da carteira vs benchmarks (12 meses)",
                           color=_LIGHT, fontsize=10, pad=8)
        self._ax.set_xlim(
            min(values + [0]) * 1.4,
            max(values + [0]) * 1.4 + max_abs * 0.2,
        )

        self._fig.tight_layout(pad=1.2)
        self.draw()


class DiversificationCanvas(FigureCanvasQTAgg):
    """Gráfico de barras horizontais com diversificação por classe de ativo."""

    _BG   = _BG_COLOR
    _GRID = _GRID_COLOR
    _COLORS = [_BLUE, _GREEN, _ORANGE, _RED,
               "#9B6DFF", "#FF9ECD", "#50D8D7", "#A0A0A0"]

    _TYPE_LABELS = {
        "acao":                 "Ações",
        "fii":                  "FIIs",
        "etf":                  "ETFs",
        "tesouro_direto":       "Tesouro Direto",
        "renda_fixa":           "Renda Fixa",
        "criptomoeda":          "Cripto",
        "acao_internacional":   "Int'l",
        "previdencia":          "Previdência",
        "outros":               "Outros",
    }

    def __init__(self) -> None:
        self._fig = Figure(figsize=(6, 2.6), facecolor=self._BG)
        super().__init__(self._fig)
        self.setMinimumHeight(180)
        self._ax = self._fig.add_subplot(111)
        self._ax.set_facecolor(self._BG)
        self._fig.tight_layout(pad=1.0)

    def plot(self, diversification: list[dict]) -> None:
        self._ax.cla()
        self._ax.set_facecolor(self._BG)

        if not diversification:
            self._fig.canvas.draw_idle()
            return

        labels = [self._TYPE_LABELS.get(d["type"], d["type"]) for d in diversification]
        values = [d["pct"] for d in diversification]
        colors = [self._COLORS[i % len(self._COLORS)] for i in range(len(labels))]

        y = range(len(labels))
        self._ax.barh(list(y), values, color=colors, height=0.6)

        for i, (v, lbl) in enumerate(zip(values, labels)):
            self._ax.text(v + 0.5, i, f"{v:.1f}%", va="center",
                          color=_LIGHT, fontsize=8)
            self._ax.text(-0.5, i, lbl, va="center", ha="right",
                          color=_MUTED, fontsize=8)

        self._ax.set_yticks([])
        self._ax.set_xlabel("% da carteira", color=_MUTED, fontsize=8)
        self._ax.tick_params(colors=_MUTED, labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color(self._GRID)
        self._ax.set_xlim(-12, max(values) * 1.15 if values else 100)

        self._fig.tight_layout(pad=1.0)
        self._fig.canvas.draw_idle()


# ======================================================================
# Metadados de categorias — ordem, rótulo e cor de cada tipo de ativo
# ======================================================================

_CATEGORY_META: dict[str, dict] = {
    "acao":               {"label": "Ações B3",       "color": "#00C896"},
    "fii":                {"label": "FIIs",            "color": "#4A9EFF"},
    "etf":                {"label": "ETFs",            "color": "#9B59B6"},
    "tesouro_direto":     {"label": "Tesouro Direto",  "color": "#F39C12"},
    "renda_fixa":         {"label": "Renda Fixa",      "color": "#1ABC9C"},
    "criptomoeda":        {"label": "Criptomoedas",    "color": "#E67E22"},
    "acao_internacional": {"label": "Internacional",   "color": "#3498DB"},
    "previdencia":        {"label": "Previdência",     "color": "#9B6DFF"},
    "outros":             {"label": "Outros",          "color": "#95A5A6"},
}

_CATEGORY_ORDER = [
    "acao", "fii", "etf", "tesouro_direto",
    "renda_fixa", "criptomoeda", "acao_internacional",
    "previdencia", "outros",
]

_ROW_H    = 60   # altura de cada linha da tabela
_HEADER_H = 40   # altura do cabeçalho da tabela


def _colored_label(text: str, color: str, size: str = "12px") -> QLabel:
    """Cria um QLabel com cor e tamanho de fonte específicos."""
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {color}; font-size: {size}; background: transparent;")
    return lbl


# ======================================================================
# Etapas 1, 3 e 5 — Seção de ativos por categoria
# ======================================================================

class AssetCategorySection(QWidget):
    """
    Widget de seção para uma categoria de ativos (ex: Ações B3, FIIs).

    Etapa 1: tabela de 9 colunas com ativos da categoria.
    Etapa 3: cabeçalho visual com border-left colorida e 3 linhas de info.
    Etapa 5: sem scrollbars internos — altura fixa proporcional às linhas.

    Sinais:
      edit_requested(asset_id)          → abre diálogo de edição
      delete_requested(asset_id, ticker) → confirma exclusão
    """

    edit_requested   = pyqtSignal(int)
    delete_requested = pyqtSignal(int, str)

    def __init__(
        self,
        asset_type: str,
        positions: list[dict],
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        meta = _CATEGORY_META.get(asset_type, {"label": asset_type, "color": "#95A5A6"})
        self._color     = meta["color"]
        self._label     = meta["label"]
        self._positions = sorted(
            positions,
            key=lambda p: float(p.get("estimated_cost", 0)),
            reverse=True,
        )
        self._build_ui()

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(4)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_table())

    # ------------------------------------------------------------------
    # Etapa 3 — cabeçalho destacado com border-left
    # ------------------------------------------------------------------

    def _build_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("categoryHeader")
        frame.setStyleSheet(f"""
            QFrame#categoryHeader {{
                background: #222640;
                border-left: 4px solid {self._color};
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }}
        """)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)

        # Linha 1: ● Categoria (bold 13px) + nº ativos (cinza)
        row1 = QHBoxLayout()
        name_lbl = QLabel(f"● {self._label}")
        name_lbl.setStyleSheet(
            f"color: {self._color}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        n = len(self._positions)
        n_lbl = QLabel(f"{n} ativo{'s' if n != 1 else ''}")
        n_lbl.setStyleSheet("color: #8B90A7; font-size: 11px; background: transparent;")
        row1.addWidget(name_lbl)
        row1.addStretch()
        row1.addWidget(n_lbl)
        lay.addLayout(row1)

        # Linha 2: valor investido
        total_cost = sum(float(p.get("estimated_cost", 0)) for p in self._positions)
        lay.addWidget(_colored_label(f"{_fmt_brl(total_cost)} investidos", "#C8CAD8", "12px"))

        # Linha 3: rentabilidade total + variação do dia
        total_cur = sum(
            float(p.get("current_value", p.get("estimated_cost", 0)))
            for p in self._positions
        )
        rent_pct  = (total_cur - total_cost) / total_cost * 100 if total_cost > 0 else 0.0
        day_pcts  = [float(p["day_change_pct"]) for p in self._positions
                     if p.get("day_change_pct") is not None]
        avg_day   = sum(day_pcts) / len(day_pcts) if day_pcts else 0.0

        r_col = "#00C896" if rent_pct >= 0 else "#FF6B6B"
        d_col = "#00C896" if avg_day  >= 0 else "#FF6B6B"

        row3 = QHBoxLayout()
        row3.addWidget(_colored_label(f"Rentab.: {rent_pct:+.1f}%", r_col, "11px"))
        row3.addWidget(_colored_label("  •  ", "#8B90A7", "11px"))
        row3.addWidget(_colored_label(f"Hoje: {avg_day:+.2f}%", d_col, "11px"))
        row3.addStretch()
        lay.addLayout(row3)

        return frame

    # ------------------------------------------------------------------
    # Etapas 1 e 5 — tabela sem scroll interno
    # ------------------------------------------------------------------

    def _build_table(self) -> QTableWidget:
        headers = [
            "Ticker / Nome", "Quantidade", "Preço Médio",
            "Cotação Atual", "Valor Investido", "Valor Atual",
            "Rentab. %", "Variação Dia %", "Ações",
        ]
        n = len(self._positions)
        table = QTableWidget(n, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_ROW_H)

        # Etapa 5: sem scrollbars internos
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, w in [(1, 100), (2, 120), (3, 120), (4, 130), (5, 130), (6, 110), (7, 110), (8, 90)]:
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(col, w)

        # Altura fixa: sem scroll vertical
        table.setFixedHeight(_HEADER_H + _ROW_H * max(n, 1))

        self._populate(table)
        return table

    def _populate(self, table: QTableWidget) -> None:
        for row, pos in enumerate(self._positions):
            ticker     = pos.get("ticker") or "—"
            atype      = pos.get("asset_type", "")
            net_qty    = float(pos.get("net_quantity", 0))
            avg_price  = float(pos.get("avg_price", 0))
            cost       = float(pos.get("estimated_cost", 0))
            cur_price  = float(pos.get("current_price",  0))
            cur_value  = float(pos.get("current_value",  cost))
            asset_id   = pos.get("asset_id", pos.get("id"))

            rent_pct   = (cur_value - cost) / cost * 100 if cost > 0 else 0.0
            r_col      = "#00C896" if rent_pct >= 0 else "#FF6B6B"

            day_raw    = pos.get("day_change_pct")
            day_pct    = float(day_raw) if day_raw is not None else None
            day_text   = f"{day_pct:+.2f}%" if day_pct is not None else "—"
            d_col      = "#00C896" if (day_pct or 0) >= 0 else "#FF6B6B"

            # Col 0: logo circular + ticker + nome
            logo_cell = QWidget(table)
            ll = QHBoxLayout(logo_cell)
            ll.setContentsMargins(8, 6, 4, 6)
            ll.setSpacing(8)
            ll.addWidget(TickerLogoWidget(ticker, atype, size=40),
                         alignment=Qt.AlignmentFlag.AlignVCenter)
            tc = QWidget()
            tc_lay = QVBoxLayout(tc)
            tc_lay.setContentsMargins(0, 0, 0, 0)
            tc_lay.setSpacing(2)
            t_lbl = QLabel(ticker)
            t_lbl.setStyleSheet(f"color: {self._color}; font-size: 12px; font-weight: 700;")
            n_lbl = QLabel((pos.get("name", "") or "")[:24])
            n_lbl.setStyleSheet("color: #8B90A7; font-size: 10px;")
            tc_lay.addWidget(t_lbl)
            tc_lay.addWidget(n_lbl)
            ll.addWidget(tc, stretch=1)
            table.setCellWidget(row, 0, logo_cell)

            def _it(text: str, color: str = "#C8CAD8", right: bool = False) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setForeground(QColor(color))
                if right:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                return it

            v_col = "#00C896" if cur_value > cost else ("#FF6B6B" if cur_value < cost else "#C8CAD8")

            table.setItem(row, 1, _it(_fmt_qty(net_qty), right=True))
            table.setItem(row, 2, _it(_fmt_brl(avg_price), right=True))
            table.setItem(row, 3, _it(_fmt_brl(cur_price) if cur_price > 0 else "—", right=True))
            table.setItem(row, 4, _it(_fmt_brl(cost), "#00C896" if cost > 0 else "#8B90A7", right=True))
            table.setItem(row, 5, _it(_fmt_brl(cur_value), v_col, right=True))
            table.setItem(row, 6, _it(f"{rent_pct:+.2f}%", r_col, right=True))
            table.setItem(row, 7, _it(day_text, d_col, right=True))

            # Col 8: editar + excluir
            cw = QWidget(table)
            cl = QHBoxLayout(cw)
            cl.setContentsMargins(4, 4, 4, 4)
            cl.setSpacing(4)
            cl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            eb = QPushButton()
            eb.setIcon(_svg_icon("edit", _LIGHT, 14))
            eb.setFixedSize(36, 36)
            eb.setToolTip("Editar ativo")
            eb.clicked.connect(lambda _, aid=asset_id: self.edit_requested.emit(aid))
            cl.addWidget(eb)

            db = QPushButton()
            db.setIcon(_svg_icon("delete", _RED, 14))
            db.setFixedSize(36, 36)
            db.setToolTip("Excluir ativo")
            db.clicked.connect(lambda _, aid=asset_id, t=ticker: self.delete_requested.emit(aid, t))
            cl.addWidget(db)

            table.setCellWidget(row, 8, cw)


# ======================================================================
# Etapa 4 — Rodapé consolidado
# ======================================================================

class _ConsolidatedFooter(QFrame):
    """
    Rodapé com totais consolidados de toda a carteira.

    ┌─────────────────────────────────────────┐
    │ TOTAL DA CARTEIRA                       │
    │ Investido: R$ X.XXX | Atual: R$ X.XXX  │
    │ Rentab. Total: +X,X% | Hoje: +X,X%     │
    └─────────────────────────────────────────┘
    """

    def __init__(self, positions: list[dict], parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("consolidatedFooter")
        self.setStyleSheet("""
            QFrame#consolidatedFooter {
                background: #222640;
                border-top: 2px solid #2E3250;
                border-radius: 8px;
            }
        """)

        total_cost = sum(float(p.get("estimated_cost", 0)) for p in positions)
        total_cur  = sum(float(p.get("current_value", p.get("estimated_cost", 0))) for p in positions)
        rent_pct   = (total_cur - total_cost) / total_cost * 100 if total_cost > 0 else 0.0
        day_pcts   = [float(p["day_change_pct"]) for p in positions
                      if p.get("day_change_pct") is not None]
        avg_day    = sum(day_pcts) / len(day_pcts) if day_pcts else 0.0

        r_col = "#00C896" if rent_pct >= 0 else "#FF6B6B"
        d_col = "#00C896" if avg_day  >= 0 else "#FF6B6B"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(6)

        title = QLabel("TOTAL DA CARTEIRA")
        title.setStyleSheet(
            "color: #8B90A7; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1px; background: transparent;"
        )
        lay.addWidget(title)

        row2 = QHBoxLayout()
        row2.addWidget(_colored_label(f"Investido: {_fmt_brl(total_cost)}", "#C8CAD8"))
        row2.addWidget(_colored_label("  |  ", "#3A3D50"))
        row2.addWidget(_colored_label(f"Atual: {_fmt_brl(total_cur)}", "#E8EAED", "14px"))
        row2.addStretch()
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(_colored_label(f"Rentab. Total: {rent_pct:+.2f}%", r_col))
        row3.addWidget(_colored_label("  |  ", "#3A3D50"))
        row3.addWidget(_colored_label(f"Hoje: {avg_day:+.2f}%", d_col))
        row3.addStretch()
        lay.addLayout(row3)


class InvestmentsPage(QWidget):
    """
    Página completa de investimentos: resumo, tabela de posições e liquidez.

    Ciclo de vida:
      __init__ → _build_ui → load_data
      InvestmentsWorker emite data_ready → _on_data_ready atualiza tudo
      Diálogos de cadastro e operação disparam workers → load_data ao salvar
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: InvestmentsWorker | None = None
        self._save_worker: SaveAssetWorker | SaveOperationWorker | None = None
        self._update_worker: UpdateAssetWorker | None = None
        self._dividend_worker: DividendWorker | None = None
        self._save_dividend_worker: SaveDividendWorker | None = None
        self._risk_worker: RiskWorker | None = None
        self._risk_loaded: bool = False
        self._rent_worker: RentabilidadeWorker | None = None
        self._rent_loaded: bool = False

        self._raw_assets: list[dict] = []
        self._assets: list[dict] = []

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

        # --- Cards de resumo (sempre visíveis) ---
        main.addLayout(self._build_summary_cards())

        # --- QTabWidget: Posições | Proventos ---
        self._tab_widget = QTabWidget()
        self._tab_widget.currentChanged.connect(self._on_tab_changed)

        # Aba 1: Posições na Carteira
        portfolio_tab = self._build_portfolio_tab()
        self._tab_widget.addTab(portfolio_tab, "Posições na Carteira")

        # Aba 2: Proventos Recebidos
        dividends_tab = self._build_dividends_tab()
        self._tab_widget.addTab(dividends_tab, "Proventos Recebidos")

        # Aba 3: Análise de Risco
        risk_tab = self._build_risk_tab()
        self._tab_widget.addTab(risk_tab, "Análise de Risco")

        # Aba 4: Rentabilidade vs benchmarks
        rent_tab = self._build_rentabilidade_tab()
        self._tab_widget.addTab(rent_tab, "Rentabilidade")

        main.addWidget(self._tab_widget)
        main.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_portfolio_tab(self) -> QWidget:
        """
        Aba Carteira — toolbar + seções por categoria (Etapa 2).

        O scroll é feito pelo QScrollArea externo que envolve a página inteira.
        As tabelas de categoria têm altura fixa (sem scroll interno — Etapa 5).
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(16)

        # Toolbar
        layout.addLayout(self._build_toolbar())

        # Loading label
        self._loading_label = QLabel("Carregando carteira…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._loading_label)

        # Container principal: seções de categoria + footer (Etapas 1-4)
        self._portfolio_content = QWidget()
        self._portfolio_content_layout = QVBoxLayout(self._portfolio_content)
        self._portfolio_content_layout.setContentsMargins(0, 0, 0, 0)
        self._portfolio_content_layout.setSpacing(16)
        self._portfolio_content.setVisible(False)
        layout.addWidget(self._portfolio_content)

        # Seção de liquidez (abaixo das categorias)
        self._liquidity_section = self._build_liquidity_section()
        self._liquidity_section.setVisible(False)
        layout.addWidget(self._liquidity_section)

        layout.addStretch()
        return tab

    def _build_dividends_tab(self) -> QWidget:
        """Aba de histórico de proventos recebidos."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(16)

        # --- Cards de resumo de proventos ---
        div_cards_row = QHBoxLayout()
        div_cards_row.setSpacing(16)
        self._div_card_total = _SummaryCard("Total Recebido no Ano", _GREEN)
        self._div_card_avg = _SummaryCard("Média Mensal", _BLUE)
        self._div_card_top = _SummaryCard("Maior Pagador", _ORANGE)
        for card in [self._div_card_total, self._div_card_avg, self._div_card_top]:
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            div_cards_row.addWidget(card)
        layout.addLayout(div_cards_row)

        # --- Toolbar ---
        div_toolbar = QHBoxLayout()
        div_toolbar.addStretch()
        add_div_btn = QPushButton("+ Registrar Provento")
        add_div_btn.setProperty("class", "primary")
        add_div_btn.style().unpolish(add_div_btn)
        add_div_btn.style().polish(add_div_btn)
        add_div_btn.clicked.connect(self._open_dividend_dialog)
        div_toolbar.addWidget(add_div_btn)
        layout.addLayout(div_toolbar)

        # --- Loading de proventos ---
        self._div_loading = QLabel("Carregando proventos…")
        self._div_loading.setObjectName("loadingLabel")
        self._div_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._div_loading)

        # --- Tabela de proventos ---
        div_headers = ["Data Pgto", "Ativo", "Tipo", "R$/Cota", "Total", "IR Retido"]
        self._div_table = QTableWidget(0, len(div_headers))
        self._div_table.setHorizontalHeaderLabels(div_headers)
        self._div_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._div_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._div_table.setAlternatingRowColors(True)
        self._div_table.verticalHeader().setVisible(False)
        div_hdr = self._div_table.horizontalHeader()
        div_hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        div_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._div_table.setVisible(False)
        layout.addWidget(self._div_table)

        # --- Gráfico de barras mensais ---
        chart_label = QLabel("Proventos Mensais (últimos 12 meses)")
        chart_label.setObjectName("sectionTitle")
        chart_label.setVisible(False)
        self._div_chart_label = chart_label
        layout.addWidget(chart_label)

        self._div_chart = DividendsBarCanvas()
        self._div_chart.setVisible(False)
        self._div_chart.setMinimumHeight(200)
        layout.addWidget(self._div_chart)

        layout.addStretch()
        return tab

    def _build_summary_cards(self) -> QHBoxLayout:
        """
        Linha de 3 cards: total investido, número de ativos e distribuição por tipo.

        Os cards são ocultos durante o loading e exibidos em _on_data_ready.
        """
        row = QHBoxLayout()
        row.setSpacing(16)

        self._card_total = _SummaryCard("Total Investido", _BLUE)
        self._card_assets = _SummaryCard("Ativos na Carteira", _WHITE)
        self._card_distribution = _SummaryCard("Maior Posição", _ORANGE)

        for card in [self._card_total, self._card_assets, self._card_distribution]:
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.setVisible(False)
            row.addWidget(card)

        return row

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(12)
        bar.addStretch()

        add_btn = QPushButton("+ Adicionar Ativo")
        add_btn.setProperty("class", "primary")
        add_btn.style().unpolish(add_btn)
        add_btn.style().polish(add_btn)
        add_btn.clicked.connect(self._open_add_asset_dialog)
        bar.addWidget(add_btn)

        op_btn = QPushButton("↕ Registrar Operação")
        op_btn.clicked.connect(self._open_operation_dialog)
        bar.addWidget(op_btn)

        return bar

    def _build_liquidity_section(self) -> QWidget:
        """
        Seção de breakdown de liquidez D+0, D+1, D+2, vencimento.

        Cada janela tem um sub-card com o valor total e percentual do portfólio.
        Abaixo dos cards, um gráfico de barras mostra a liquidez ACUMULADA por prazo.
        """
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("Liquidez da Carteira")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        # Cada card de liquidez é um _LiquidityCard com cor associada à urgência
        self._liq_d0 = _LiquidityCard("D+0", "Disponível hoje", _GREEN)
        self._liq_d1 = _LiquidityCard("D+1", "Próximo dia útil", _BLUE)
        self._liq_d2 = _LiquidityCard("D+2", "Liquidação B3", _ORANGE)
        self._liq_mat = _LiquidityCard("Vencimento", "Apenas no vencimento", _RED)

        for card in [self._liq_d0, self._liq_d1, self._liq_d2, self._liq_mat]:
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            cards_row.addWidget(card)

        layout.addLayout(cards_row)

        # Gráfico de liquidez acumulada por prazo
        chart_label = QLabel("Quanto posso resgatar sem perda até cada prazo?")
        chart_label.setStyleSheet("color: #8B90A7; font-size: 11px;")
        layout.addWidget(chart_label)

        self._liquidity_chart = LiquidityCumulativeCanvas()
        self._liquidity_chart.setFixedHeight(180)
        layout.addWidget(self._liquidity_chart)

        return container

    # ------------------------------------------------------------------
    # Carregamento
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        self._set_content_visible(False)
        self._loading_label.setText("Carregando carteira…")
        self._loading_label.setVisible(True)

        self._worker = InvestmentsWorker(self._client)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_data_ready(
        self, assets: list[dict], positions: list[dict], portfolio: dict, liquidity: dict
    ) -> None:
        self._assets = assets
        self._raw_assets = [
            {
                "id": pos["asset_id"],
                "ticker": pos.get("ticker"),
                "name": pos.get("name", ""),
                "asset_type": pos.get("asset_type", ""),
            }
            for pos in positions
        ]

        self._loading_label.setVisible(False)
        self._set_content_visible(True)

        # Cards de resumo
        total = float(portfolio.get("total_invested", 0))
        self._card_total.set_value(_fmt_brl(total))
        self._card_assets.set_value(str(len(positions)))

        # Maior posição por tipo (para o card de distribuição)
        by_type = portfolio.get("by_type", [])
        if by_type:
            top = max(by_type, key=lambda e: float(e.get("net_invested", 0)))
            type_name = _ASSET_TYPE_DISPLAY.get(top["asset_type"], top["asset_type"])
            net = float(top.get("net_invested", 0))
            pct = (net / total * 100) if total > 0 else 0
            self._card_distribution.set_value(
                type_name, sub=f"{_fmt_brl(net)} ({pct:.1f}%)"
            )
        else:
            self._card_distribution.set_value("—")

        # Seções por categoria (Etapas 1-4)
        self._populate_categories(positions)
        self._portfolio_content.setVisible(True)

        # Breakdown de liquidez
        self._populate_liquidity(liquidity, total)

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._set_content_visible(False)

    # ------------------------------------------------------------------
    # Helpers de UI
    # ------------------------------------------------------------------

    def _set_content_visible(self, visible: bool) -> None:
        for card in [self._card_total, self._card_assets, self._card_distribution]:
            card.setVisible(visible)
        self._portfolio_content.setVisible(visible)
        self._liquidity_section.setVisible(visible)



    def _populate_categories(self, positions: list[dict]) -> None:
        """
        Etapas 1-4: limpa o container e recria uma AssetCategorySection
        para cada tipo de ativo presente, seguido do rodapé consolidado.
        """
        # Limpa seções anteriores
        while self._portfolio_content_layout.count():
            item = self._portfolio_content_layout.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        if not positions:
            empty = QLabel("Nenhum ativo cadastrado. Clique em '+ Adicionar Ativo' para começar.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #8B90A7; font-size: 13px; padding: 40px;")
            self._portfolio_content_layout.addWidget(empty)
            return

        # Agrupa por tipo
        by_type: dict[str, list[dict]] = {}
        for pos in positions:
            atype = pos.get("asset_type", "outros")
            by_type.setdefault(atype, []).append(pos)

        # Adiciona seções na ordem preferida
        for atype in _CATEGORY_ORDER:
            if atype not in by_type:
                continue
            section = AssetCategorySection(atype, by_type[atype])
            section.edit_requested.connect(self._open_edit_asset_dialog)
            section.delete_requested.connect(self._confirm_delete_asset)
            self._portfolio_content_layout.addWidget(section)

        # Tipos fora da ordem padrão (ex: novos tipos adicionados no futuro)
        for atype, pos_list in by_type.items():
            if atype not in _CATEGORY_ORDER:
                section = AssetCategorySection(atype, pos_list)
                section.edit_requested.connect(self._open_edit_asset_dialog)
                section.delete_requested.connect(self._confirm_delete_asset)
                self._portfolio_content_layout.addWidget(section)

        # Rodapé consolidado (Etapa 4)
        self._portfolio_content_layout.addWidget(_ConsolidatedFooter(positions))

    def _populate_liquidity(self, liquidity: dict, total_portfolio: float) -> None:
        d0 = float(liquidity.get("d0_value", 0))
        d1 = float(liquidity.get("d1_value", 0))
        d2 = float(liquidity.get("d2_value", 0))
        mat = float(liquidity.get("maturity_value", 0))
        total = float(liquidity.get("total_portfolio", total_portfolio)) or 1

        def _pct(v: float) -> str:
            return f"{v / total * 100:.1f}% do portfólio"

        self._liq_d0.set_value(_fmt_brl(d0), sub=_pct(d0))
        self._liq_d1.set_value(_fmt_brl(d1), sub=_pct(d1))
        self._liq_d2.set_value(_fmt_brl(d2), sub=_pct(d2))
        self._liq_mat.set_value(_fmt_brl(mat), sub=_pct(mat))

        # Atualiza o gráfico acumulado com os mesmos valores
        self._liquidity_chart.update_data(d0, d1, d2, mat)

    # ------------------------------------------------------------------
    # Diálogos
    # ------------------------------------------------------------------

    def _confirm_delete_asset(self, asset_id: int, ticker: str) -> None:
        """Confirma exclusão de ativo e remove se confirmado."""
        reply = QMessageBox.question(
            self,
            "Excluir ativo",
            f"Deseja realmente excluir o ativo <b>{ticker}</b>?<br>"
            "Esta ação não pode ser desfeita.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._client.delete_asset(asset_id)
            self.load_data()
        except ApiError as exc:
            QMessageBox.critical(self, "Erro ao excluir", str(exc))

    def _open_edit_asset_dialog(self, asset_id: int) -> None:
        asset = next((a for a in self._assets if a["id"] == asset_id), None)
        if asset is None:
            QMessageBox.warning(self, "Ativo não encontrado", "Dados do ativo não encontrados.")
            return
        dialog = EditAssetDialog(asset, client=self._client, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # 1. Atualiza dados do ativo
        payload = dialog.get_payload()
        if not (self._update_worker and self._update_worker.isRunning()):
            self._update_worker = UpdateAssetWorker(self._client, asset_id, payload)
            self._update_worker.updated.connect(lambda _: self.load_data())
            self._update_worker.error_occurred.connect(
                lambda msg: QMessageBox.critical(self, "Erro ao atualizar", msg)
            )
            self._update_worker.start()

        # 2. Ajusta posição se os valores da aba 2 divergem da posição atual
        pos_data = dialog.get_position_data()
        if pos_data:
            pos_worker = AdjustPositionWorker(self._client, asset_id, pos_data)
            pos_worker.done.connect(lambda _result: self.load_data())
            pos_worker.error_occurred.connect(
                lambda msg: QMessageBox.critical(self, "Erro ao ajustar posição", msg)
            )
            pos_worker.start()
            self._pos_workers: list = getattr(self, "_pos_workers", [])
            self._pos_workers.append(pos_worker)

    def _open_add_asset_dialog(self) -> None:
        dialog = NewAssetDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()

        if self._save_worker and self._save_worker.isRunning():
            return
        self._save_worker = SaveAssetWorker(self._client, payload)
        self._save_worker.saved.connect(lambda _: self.load_data())
        self._save_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao salvar", msg)
        )
        self._save_worker.start()

    def _open_operation_dialog(self) -> None:
        if not self._raw_assets:
            QMessageBox.information(
                self,
                "Sem ativos",
                "Cadastre pelo menos um ativo antes de registrar uma operação.",
            )
            return

        dialog = NewOperationDialog(self._raw_assets, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        asset_id = dialog.get_asset_id()
        payload = dialog.get_payload()

        if self._save_worker and self._save_worker.isRunning():
            return
        self._save_worker = SaveOperationWorker(self._client, asset_id, payload)
        self._save_worker.saved.connect(self.load_data)
        self._save_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao salvar", msg)
        )
        self._save_worker.start()

    # ------------------------------------------------------------------
    # Proventos
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._load_dividends()
        elif index == 2 and not self._risk_loaded:
            self._load_risk()
        elif index == 3 and not self._rent_loaded:
            self._load_rentabilidade()

    def _load_dividends(self) -> None:
        if self._dividend_worker and self._dividend_worker.isRunning():
            return

        self._div_loading.setVisible(True)
        self._div_table.setVisible(False)
        self._div_chart.setVisible(False)
        self._div_chart_label.setVisible(False)

        self._dividend_worker = DividendWorker(self._client)
        self._dividend_worker.data_ready.connect(self._on_dividends_ready)
        self._dividend_worker.error_occurred.connect(self._on_div_error)
        self._dividend_worker.start()

    def _on_dividends_ready(self, dividends: list[dict], summary: dict) -> None:
        self._div_loading.setVisible(False)
        self._div_table.setVisible(True)
        self._div_chart.setVisible(True)
        self._div_chart_label.setVisible(True)

        # Cards
        total = float(summary.get("total_received", 0))
        avg = float(summary.get("average_monthly", 0))
        top_ticker = summary.get("biggest_payer_ticker") or "—"
        top_total = float(summary.get("biggest_payer_total") or 0)

        self._div_card_total.set_value(_fmt_brl(total))
        self._div_card_avg.set_value(_fmt_brl(avg))
        self._div_card_top.set_value(
            top_ticker, sub=_fmt_brl(top_total) if top_total else ""
        )

        # Tabela
        self._div_table.setRowCount(len(dividends))
        for row, d in enumerate(dividends):
            cells = [
                (d.get("payment_date", ""), _WHITE),
                (d.get("ticker") or d.get("asset_name") or "—", _BLUE),
                (_DIVIDEND_TYPE_DISPLAY.get(d.get("dividend_type", ""), d.get("dividend_type", "")), _MUTED),
                (_fmt_brl(float(d.get("amount_per_unit", 0))), _WHITE),
                (_fmt_brl(float(d.get("total_amount", 0))), _GREEN),
                (_fmt_brl(float(d.get("tax_withheld", 0))), _ORANGE if float(d.get("tax_withheld", 0)) > 0 else _MUTED),
            ]
            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                if col in (3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._div_table.setItem(row, col, item)

        # Gráfico
        by_month = summary.get("by_month", {})
        self._div_chart.plot({k: float(v) for k, v in by_month.items()})

    def _on_div_error(self, message: str) -> None:
        self._div_loading.setText(f"Erro ao carregar proventos: {message}")

    def _open_dividend_dialog(self) -> None:
        if not self._raw_assets:
            QMessageBox.information(
                self,
                "Sem ativos",
                "Cadastre pelo menos um ativo antes de registrar um provento.",
            )
            return

        dialog = NewDividendDialog(self._raw_assets, self._client, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        payload = dialog.get_payload()
        if self._save_dividend_worker and self._save_dividend_worker.isRunning():
            return
        self._save_dividend_worker = SaveDividendWorker(self._client, payload)
        self._save_dividend_worker.saved.connect(self._load_dividends)
        self._save_dividend_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao salvar provento", msg)
        )
        self._save_dividend_worker.start()

    # ------------------------------------------------------------------
    # Aba Análise de Risco
    # ------------------------------------------------------------------

    def _build_risk_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(14)

        # Cards de métricas da carteira
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self._risk_beta_card  = _RiskMetricCard("Beta da Carteira", "—",
            "Sensibilidade vs IBOVESPA (1 = mesmo risco do índice)")
        self._risk_vol_card   = _RiskMetricCard("Volatilidade (a.a.)", "—",
            "Desvio padrão anualizado dos retornos diários")
        self._risk_var_card   = _RiskMetricCard("VaR 95% (diário)", "—",
            "Perda máxima esperada em 1 dia com 95% de confiança")
        for c in (self._risk_beta_card, self._risk_vol_card, self._risk_var_card):
            cards_row.addWidget(c)
        lay.addLayout(cards_row)

        # Gráfico de diversificação
        div_lbl = QLabel("Diversificação por Classe de Ativo")
        div_lbl.setStyleSheet("color: #8B90A7; font-size: 11px; font-weight: 600;")
        lay.addWidget(div_lbl)
        self._div_canvas = DiversificationCanvas()
        lay.addWidget(self._div_canvas)

        # Tabela por ativo
        risk_lbl = QLabel("Risco por Ativo")
        risk_lbl.setStyleSheet("color: #8B90A7; font-size: 11px; font-weight: 600;")
        lay.addWidget(risk_lbl)
        self._risk_table = self._build_risk_table()
        lay.addWidget(self._risk_table)

        self._risk_loading = QLabel("Calculando métricas de risco…")
        self._risk_loading.setObjectName("loadingLabel")
        self._risk_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._risk_loading.setVisible(False)
        lay.addWidget(self._risk_loading)

        return tab

    def _build_rentabilidade_tab(self) -> QWidget:
        """
        Aba de rentabilidade: mostra o retorno da carteira de renda variável
        comparado com CDI e IBOVESPA no período de 12 meses.
        """
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(14)

        # Cards de métricas rápidas
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self._rent_card_port  = _RiskMetricCard("Retorno Carteira (12m)", "—",
            "Retorno estimado da carteira de renda variável nos últimos 12 meses")
        self._rent_card_cdi   = _RiskMetricCard("CDI (12m)", "—",
            "Taxa CDI acumulada no período (fonte: opportunity-cost)")
        self._rent_card_alpha = _RiskMetricCard("Alpha (Carteira − CDI)", "—",
            "Diferença entre o retorno da carteira e o CDI no período")
        for c in (self._rent_card_port, self._rent_card_cdi, self._rent_card_alpha):
            cards_row.addWidget(c)
        lay.addLayout(cards_row)

        # Gráfico comparativo
        chart_lbl = QLabel("Comparação de retorno — carteira vs CDI vs IBOVESPA (12 meses)")
        chart_lbl.setStyleSheet("color: #8B90A7; font-size: 11px; font-weight: 600;")
        lay.addWidget(chart_lbl)

        self._rent_canvas = RentabilidadeCanvas()
        self._rent_canvas.setMinimumHeight(200)
        lay.addWidget(self._rent_canvas)

        # Nota metodológica
        note = QLabel(
            "⚠  O retorno da carteira é calculado com base no custo médio ponderado "
            "dos ativos de renda variável versus o CDI acumulado no período."
            " IBOVESPA via BOVA11 (yfinance)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6B7080; font-size: 10px;")
        lay.addWidget(note)

        # Loading / erro
        self._rent_loading = QLabel("Calculando rentabilidade…")
        self._rent_loading.setObjectName("loadingLabel")
        self._rent_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rent_loading.setVisible(False)
        lay.addWidget(self._rent_loading)

        lay.addStretch()
        return tab

    def _build_risk_table(self) -> QTableWidget:
        headers = ["Ticker", "Nome", "Classe", "Peso %", "Beta", "Volatil. a.a.", "VaR 95%"]
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        h = t.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        return t

    def _load_risk(self) -> None:
        if self._risk_worker and self._risk_worker.isRunning():
            return
        self._risk_loading.setVisible(True)
        self._risk_table.setVisible(False)
        self._risk_worker = RiskWorker(self._client)
        self._risk_worker.data_ready.connect(self._on_risk_ready)
        self._risk_worker.error_occurred.connect(
            lambda msg: (
                self._risk_loading.setText(f"Erro: {msg}"),
                self._risk_table.setVisible(False),
            )
        )
        self._risk_worker.start()

    def _load_rentabilidade(self) -> None:
        if self._rent_worker and self._rent_worker.isRunning():
            return
        self._rent_loading.setVisible(True)
        self._rent_canvas.setVisible(False)
        self._rent_worker = RentabilidadeWorker(self._client)
        self._rent_worker.data_ready.connect(self._on_rentabilidade_ready)
        self._rent_worker.error_occurred.connect(
            lambda msg: (
                self._rent_loading.setText(f"Erro ao carregar: {msg}"),
            )
        )
        self._rent_worker.start()

    def _on_rentabilidade_ready(self, data: dict) -> None:
        self._rent_loaded = True
        self._rent_loading.setVisible(False)
        self._rent_canvas.setVisible(True)

        opp  = data.get("opportunity", {})
        ibov = data.get("ibov", {})

        port_pct = opp.get("portfolio_return_pct")
        cdi_pct  = opp.get("cdi_return_pct")
        alpha    = opp.get("alpha_pct")

        # IBOV: tenta chaves comuns que a API pode retornar
        ibov_pct = (
            ibov.get("asset_return_pct")
            or ibov.get("ticker_return_pct")
            or ibov.get("return_pct")
            or ibov.get("total_return_pct")
        )

        def _pct(v, suffix="%"):
            return f"{v:+.2f}{suffix}" if v is not None else "N/D"

        alpha_color = _GREEN if (alpha or 0) >= 0 else _RED
        self._rent_card_port.set_value(_pct(port_pct))
        self._rent_card_cdi.set_value(_pct(cdi_pct))
        self._rent_card_alpha._val.setStyleSheet(
            f"color: {alpha_color}; font-size: 18px; font-weight: 700;"
        )
        self._rent_card_alpha.set_value(_pct(alpha))

        self._rent_canvas.plot(
            port_pct,
            cdi_pct,
            ibov_pct,
        )

    def _on_risk_ready(self, data: dict) -> None:
        self._risk_loaded = True
        self._risk_loading.setVisible(False)
        self._risk_table.setVisible(True)

        def _fmt_pct(v, suffix=""):
            return f"{v:.2f}{suffix}" if v is not None else "N/D"

        beta = data.get("portfolio_beta")
        vol  = data.get("portfolio_volatility")
        var  = data.get("portfolio_var_95")

        self._risk_beta_card.set_value(_fmt_pct(beta))
        self._risk_vol_card.set_value(_fmt_pct(vol, "%"))
        self._risk_var_card.set_value(_fmt_pct(var, "%"))

        self._div_canvas.plot(data.get("diversification", []))

        assets = data.get("assets", [])
        self._risk_table.setRowCount(len(assets))
        for row, a in enumerate(assets):
            cells = [
                a.get("ticker", "—"),
                a.get("name", ""),
                a.get("asset_type", ""),
                f"{a.get('weight_pct', 0):.1f}%",
                _fmt_pct(a.get("beta")),
                _fmt_pct(a.get("volatility"), "%"),
                _fmt_pct(a.get("var_95"), "%"),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setForeground(QColor(_BLUE))
                elif col in (4, 5, 6) and text != "N/D":
                    item.setForeground(QColor(_ORANGE))
                self._risk_table.setItem(row, col, item)


# ======================================================================
# Componentes de UI reutilizáveis
# ======================================================================


class _RiskMetricCard(QFrame):
    """Card simples para exibir uma métrica de risco com tooltip explicativo."""

    def __init__(self, title: str, value: str, tooltip: str = "") -> None:
        super().__init__()
        self.setObjectName("summaryCard")
        if tooltip:
            self.setToolTip(tooltip)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(4)

        t = QLabel(title)
        t.setStyleSheet("color: #8B90A7; font-size: 11px; font-weight: 600;")
        self._val = QLabel(value)
        self._val.setStyleSheet("color: #C8CAD8; font-size: 18px; font-weight: 700;")

        lay.addWidget(t)
        lay.addWidget(self._val)

    def set_value(self, text: str) -> None:
        self._val.setText(text)


class _SummaryCard(QFrame):
    """Card compacto para exibir um indicador resumido (reutiliza estilo summaryCard)."""

    def __init__(self, title: str, default_color: str = _WHITE) -> None:
        super().__init__()
        self.setObjectName("summaryCard")
        self._default_color = default_color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(6)

        self._title = QLabel(title)
        self._title.setObjectName("cardTitle")

        self._value = QLabel("—")
        self._value.setObjectName("cardValue")
        self._value.setStyleSheet(f"color: {default_color};")

        self._sub = QLabel("")
        self._sub.setObjectName("cardSub")
        self._sub.setStyleSheet("color: #8B90A7; font-size: 11px;")
        self._sub.setVisible(False)

        layout.addWidget(self._title)
        layout.addWidget(self._value)
        layout.addWidget(self._sub)

    def set_value(self, value: str, color: str | None = None, sub: str = "") -> None:
        self._value.setText(value)
        self._value.setStyleSheet(f"color: {color or self._default_color};")
        self._sub.setText(sub)
        self._sub.setVisible(bool(sub))


class LiquidityCumulativeCanvas(FigureCanvasQTAgg):
    """
    Gráfico de barras horizontais empilhadas mostrando a liquidez ACUMULADA.

    Cada barra representa quanto do portfólio fica disponível em cada prazo:
      D+0           → disponível hoje (contas + ativos imediatos)
      D+0 + D+1     → disponível até amanhã
      D+0 + D+1 + D+2 → disponível em até 2 dias úteis
      Total         → valor total (inclui ativos travados até vencimento)

    A visualização acumulada responde: "se eu precisar de R$X em D+1, tenho?"
    """

    _COLORS = [_GREEN, _BLUE, _ORANGE, _MUTED]
    _LABELS = ["D+0\n(hoje)", "D+1\n(amanhã)", "D+2\n(B3)", "Vencimento"]

    def __init__(self, parent: QWidget | None = None) -> None:
        fig = Figure(figsize=(6, 2.2), facecolor="#1E2138")
        super().__init__(fig)
        self.setParent(parent)
        self._ax = fig.add_subplot(111)
        self._fig = fig
        self._draw_empty()

    def _draw_empty(self) -> None:
        ax = self._ax
        ax.clear()
        ax.set_facecolor("#1E2138")
        ax.text(
            0.5, 0.5, "Sem dados de liquidez",
            ha="center", va="center", color=_MUTED,
            fontsize=10, transform=ax.transAxes,
        )
        ax.axis("off")
        self._fig.tight_layout()
        self.draw()

    def update_data(self, d0: float, d1: float, d2: float, maturity: float) -> None:
        """Redesenha o gráfico com os valores de cada janela."""
        ax = self._ax
        ax.clear()
        ax.set_facecolor("#1E2138")

        # Valores acumulados: quanto está disponível até cada prazo
        cumulative = [d0, d0 + d1, d0 + d1 + d2, d0 + d1 + d2 + maturity]
        total = cumulative[-1] or 1  # evitar divisão por zero

        y_pos = [3, 2, 1, 0]  # de cima para baixo: D+0, D+1, D+2, Total

        for i, (val, color, label) in enumerate(zip(cumulative, self._COLORS, self._LABELS)):
            pct = val / total * 100
            ax.barh(
                y_pos[i], val,
                color=color, alpha=0.85,
                height=0.55,
            )
            # Valor em BRL dentro ou à direita da barra
            formatted = f"R$ {val:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
            ax.text(
                val + total * 0.01, y_pos[i],
                f"{formatted}  ({pct:.1f}%)",
                va="center", color=_WHITE, fontsize=8.5,
            )

        ax.set_yticks(y_pos)
        ax.set_yticklabels(self._LABELS, color=_MUTED, fontsize=8.5)
        ax.set_xlabel("Valor acumulado disponível (R$)", color=_MUTED, fontsize=8)
        ax.tick_params(axis="x", colors=_MUTED, labelsize=7.5)
        ax.tick_params(axis="y", left=False)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.xaxis.label.set_color(_MUTED)
        ax.set_title("Liquidez acumulada por prazo", color=_LIGHT, fontsize=9, pad=6)
        ax.set_xlim(0, total * 1.35)

        self._fig.tight_layout(pad=1.0)
        self.draw()


class _LiquidityCard(QFrame):
    """
    Card de janela de liquidez: exibe o valor disponível e o percentual do portfólio.

    A cor da borda esquerda indica urgência/disponibilidade:
      verde  → D+0 (imediato)
      azul   → D+1
      laranja → D+2
      cinza  → vencimento (ilíquido)
    """

    def __init__(self, window: str, subtitle: str, color: str) -> None:
        super().__init__()
        self.setObjectName("summaryCard")
        # Borda esquerda colorida para identificar visualmente a janela
        self.setStyleSheet(
            f"QFrame#summaryCard {{ border-left: 4px solid {color}; border-radius: 10px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(4)

        window_lbl = QLabel(window)
        window_lbl.setObjectName("cardTitle")

        subtitle_lbl = QLabel(subtitle)
        subtitle_lbl.setStyleSheet("color: #8B90A7; font-size: 11px;")

        self._value_lbl = QLabel("—")
        self._value_lbl.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: 700;")

        self._sub_lbl = QLabel("")
        self._sub_lbl.setStyleSheet("color: #8B90A7; font-size: 11px;")

        layout.addWidget(window_lbl)
        layout.addWidget(subtitle_lbl)
        layout.addWidget(self._value_lbl)
        layout.addWidget(self._sub_lbl)

    def set_value(self, value: str, sub: str = "") -> None:
        self._value_lbl.setText(value)
        self._sub_lbl.setText(sub)
        self._sub_lbl.setVisible(bool(sub))


# ======================================================================
# Utilitários
# ======================================================================


def _fmt_brl(value: float) -> str:
    """Formata como moeda brasileira: R$ 1.234,56"""
    try:
        formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        prefix = "-R$ " if value < 0 else "R$ "
        return f"{prefix}{formatted}"
    except (TypeError, ValueError):
        return "—"


def _fmt_qty(qty: float) -> str:
    """
    Formata quantidade de ativos.

    Ações e FIIs: sempre inteiros (100, 50).
    Cripto e alguns ETFs: podem ter decimais (0.00512 BTC).
    Exibe casas decimais apenas se necessário.
    """
    if qty == int(qty):
        return f"{int(qty):,}".replace(",", ".")
    return f"{qty:.6f}".rstrip("0")
