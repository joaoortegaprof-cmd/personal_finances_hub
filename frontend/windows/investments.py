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
            assets = self._client.get_assets()
            portfolio = self._client.get_portfolio_summary()
            liquidity = self._client.get_liquidity()

            # Busca posição consolidada para cada ativo (N+1 necessário — ver docstring)
            positions: list[dict] = []
            for asset in assets:
                try:
                    pos = self._client.get_asset_position(asset["id"])
                    positions.append(pos)
                except ApiError:
                    # Ativo sem operações registradas — posição zerada
                    positions.append({
                        "asset_id": asset["id"],
                        "ticker": asset.get("ticker"),
                        "name": asset["name"],
                        "asset_type": asset["asset_type"],
                        "net_quantity": "0",
                        "avg_price": "0",
                        "estimated_cost": "0",
                    })

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
        lookup_btn = QPushButton("↻")
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


class PositionAdjustWorker(QThread):
    """Zera a posição atual e cria uma nova via operações compra/venda."""

    done    = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        client: ApiClient,
        asset_id: int,
        quantity: float,
        avg_price: float,
        op_date: date,
    ) -> None:
        super().__init__()
        self._client    = client
        self._asset_id  = asset_id
        self._quantity  = quantity
        self._avg_price = avg_price
        self._op_date   = op_date

    def run(self) -> None:
        try:
            pos = self._client.get_asset_position(self._asset_id)
            current_qty = float(pos.get("net_quantity", 0))
            current_avg = float(pos.get("avg_price", 0))

            if current_qty > 0:
                self._client.create_asset_operation(
                    self._asset_id,
                    {
                        "operation_date": self._op_date.isoformat(),
                        "quantity": f"{current_qty:.6f}",
                        "unit_price": f"{current_avg:.2f}",
                        "fees": "0.00",
                        "operation_type": "venda",
                        "notes": "Ajuste manual de posição",
                    },
                )

            if self._quantity > 0:
                self._client.create_asset_operation(
                    self._asset_id,
                    {
                        "operation_date": self._op_date.isoformat(),
                        "quantity": f"{self._quantity:.6f}",
                        "unit_price": f"{self._avg_price:.2f}",
                        "fees": "0.00",
                        "operation_type": "compra",
                        "notes": "Ajuste manual de posição",
                    },
                )
            self.done.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class EditAssetDialog(NewAssetDialog):
    """
    Formulário modal para editar um ativo — duas abas:
      1. Dados do Ativo  — mesmos campos do cadastro
      2. Posição na Carteira — ajuste direto de qty/preço médio
    """

    def __init__(
        self,
        asset: dict[str, Any],
        client: ApiClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        self._edit_client = client
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

        # ── Aba 2: Posição na Carteira ────────────────────────────────
        tab2 = QWidget()
        t2_layout = QVBoxLayout(tab2)
        t2_layout.setContentsMargins(0, 12, 0, 0)
        t2_layout.setSpacing(12)

        warn = QLabel(
            "Atenção: isto substituirá a posição atual pelo valor informado. "
            "O histórico de operações será preservado."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #FFB347; font-size: 11px;")
        t2_layout.addWidget(warn)

        pos_form = QFormLayout()
        pos_form.setSpacing(12)
        pos_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._pos_qty = QDoubleSpinBox()
        self._pos_qty.setRange(0.0, 99_999_999.0)
        self._pos_qty.setDecimals(6)
        self._pos_qty.setValue(0.0)
        pos_form.addRow("Quantidade", self._pos_qty)

        self._pos_avg_price = QDoubleSpinBox()
        self._pos_avg_price.setRange(0.0, 999_999.99)
        self._pos_avg_price.setDecimals(2)
        self._pos_avg_price.setPrefix("R$ ")
        self._pos_avg_price.setValue(0.0)
        pos_form.addRow("Preço médio", self._pos_avg_price)

        self._pos_date = QDateEdit()
        self._pos_date.setCalendarPopup(True)
        self._pos_date.setDisplayFormat("dd/MM/yyyy")
        self._pos_date.setDate(QDate.currentDate())
        pos_form.addRow("Data da posição", self._pos_date)

        t2_layout.addLayout(pos_form)

        recalc_btn = QPushButton("Recalcular pelo histórico")
        recalc_btn.setToolTip("Busca as operações reais e preenche qty/preço médio")
        recalc_btn.clicked.connect(self._recalc_from_history)
        t2_layout.addWidget(recalc_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        t2_layout.addStretch()
        tabs.addTab(tab2, "Posição na Carteira")

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

    def _recalc_from_history(self) -> None:
        if not self._edit_client:
            return
        try:
            pos = self._edit_client.get_asset_position(self._asset_id)
            qty = float(pos.get("net_quantity", 0))
            avg = float(pos.get("avg_price", 0))
            self._pos_qty.setValue(qty)
            self._pos_avg_price.setValue(avg)
        except Exception:
            pass

    def get_position_data(self) -> dict[str, Any] | None:
        """Retorna dados de ajuste de posição, ou None se não foi preenchido."""
        qty = self._pos_qty.value()
        avg = self._pos_avg_price.value()
        if qty == 0.0 and avg == 0.0:
            return None
        qd = self._pos_date.date()
        return {
            "quantity": qty,
            "avg_price": avg,
            "date": date(qd.year(), qd.month(), qd.day()),
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
        fig = Figure(figsize=(10, 3), facecolor="#1A1D2E")
        self.axes = fig.add_subplot(111)
        super().__init__(fig)
        self._style_axes()

    def _style_axes(self) -> None:
        ax = self.axes
        ax.set_facecolor("#1A1D2E")
        ax.tick_params(colors="#8B90A7", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#2A2D3E")

    def plot(self, by_month: dict[str, float]) -> None:
        self.axes.cla()
        self._style_axes()
        if not by_month:
            self.draw()
            return

        labels = list(by_month.keys())
        values = [float(v) for v in by_month.values()]
        colors = ["#00C896" if v > 0 else "#8B90A7" for v in values]

        x_pos = range(len(labels))
        bars = self.axes.bar(x_pos, values, color=colors, width=0.6)
        self.axes.set_xticks(list(x_pos))
        short_labels = [lbl[5:] if len(lbl) >= 7 else lbl for lbl in labels]
        self.axes.set_xticklabels(short_labels, rotation=30, ha="right", fontsize=7)
        self.axes.set_ylabel("R$", color="#8B90A7", fontsize=8)
        self.axes.yaxis.set_tick_params(labelcolor="#8B90A7")

        # Anotação de valor em cima de cada barra
        for bar, val in zip(bars, values):
            if val > 0:
                self.axes.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(values) * 0.01,
                    f"R${val:.0f}",
                    ha="center", va="bottom",
                    color="#E8EAED", fontsize=6,
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

        main.addWidget(self._tab_widget)
        main.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_portfolio_tab(self) -> QWidget:
        """Aba com tabela de posições e breakdown de liquidez."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(16)

        # Toolbar de ações
        layout.addLayout(self._build_toolbar())

        # Loading
        self._loading_label = QLabel("Carregando carteira…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._loading_label)

        # Título da tabela
        table_title = QLabel("Posições na Carteira")
        table_title.setObjectName("sectionTitle")
        table_title.setVisible(False)
        self._table_title = table_title
        layout.addWidget(table_title)

        # Tabela de ativos
        self._table = self._build_table()
        self._table.setVisible(False)
        layout.addWidget(self._table)

        # Seção de liquidez
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
        self._div_card_total = _SummaryCard("Total Recebido no Ano", "#00C896")
        self._div_card_avg = _SummaryCard("Média Mensal", "#4A9EFF")
        self._div_card_top = _SummaryCard("Maior Pagador", "#FFB347")
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

        self._card_total = _SummaryCard("Total Investido", "#4A9EFF")
        self._card_assets = _SummaryCard("Ativos na Carteira", "#E8EAED")
        self._card_distribution = _SummaryCard("Maior Posição", "#FFB347")

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

    def _build_table(self) -> QTableWidget:
        headers = ["Ticker", "Nome", "Tipo", "Quantidade", "Preço Médio", "Valor Investido", "Ações"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_COL_ACTIONS, 40)

        return table

    def _build_liquidity_section(self) -> QWidget:
        """
        Seção de breakdown de liquidez D+0, D+1, D+2, vencimento.

        Cada janela tem um sub-card com o valor total e percentual do portfólio.
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
        self._liq_d0 = _LiquidityCard("D+0", "Disponível hoje", "#00C896")
        self._liq_d1 = _LiquidityCard("D+1", "Próximo dia útil", "#4A9EFF")
        self._liq_d2 = _LiquidityCard("D+2", "Liquidação B3", "#FFB347")
        self._liq_mat = _LiquidityCard("Vencimento", "Apenas no vencimento", "#8B90A7")

        for card in [self._liq_d0, self._liq_d1, self._liq_d2, self._liq_mat]:
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            cards_row.addWidget(card)

        layout.addLayout(cards_row)
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

        # Tabela de posições
        self._populate_table(positions)

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
        self._table.setVisible(visible)
        self._table_title.setVisible(visible)
        self._liquidity_section.setVisible(visible)

    def _populate_table(self, positions: list[dict]) -> None:
        # Ordena por valor investido decrescente para mostrar posições maiores primeiro
        positions_sorted = sorted(
            positions, key=lambda p: float(p.get("estimated_cost", 0)), reverse=True
        )
        self._table.setRowCount(len(positions_sorted))

        for row, pos in enumerate(positions_sorted):
            net_qty = float(pos.get("net_quantity", 0))
            avg_price = float(pos.get("avg_price", 0))
            cost = float(pos.get("estimated_cost", 0))
            asset_type = pos.get("asset_type", "")

            cells = [
                (pos.get("ticker") or "—", "#4A9EFF"),
                (pos.get("name", ""), "#E8EAED"),
                (_ASSET_TYPE_DISPLAY.get(asset_type, asset_type), "#8B90A7"),
                (_fmt_qty(net_qty), "#E8EAED"),
                (_fmt_brl(avg_price), "#E8EAED"),
                (_fmt_brl(cost), "#00C896" if cost > 0 else "#8B90A7"),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                if col in (_COL_QTY, _COL_AVG, _COL_COST):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._table.setItem(row, col, item)

            asset_id = pos["asset_id"]
            cell_w = QWidget()
            cell_lay = QHBoxLayout(cell_w)
            cell_lay.setContentsMargins(4, 2, 4, 2)
            cell_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit_btn = QPushButton("✏️")
            edit_btn.setFixedSize(32, 32)
            edit_btn.setToolTip("Editar ativo")
            edit_btn.clicked.connect(lambda _, aid=asset_id: self._open_edit_asset_dialog(aid))
            cell_lay.addWidget(edit_btn)
            self._table.setCellWidget(row, _COL_ACTIONS, cell_w)

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

    # ------------------------------------------------------------------
    # Diálogos
    # ------------------------------------------------------------------

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

        # 2. Ajusta posição se a aba 2 foi preenchida
        pos_data = dialog.get_position_data()
        if pos_data:
            pos_worker = PositionAdjustWorker(
                self._client,
                asset_id,
                pos_data["quantity"],
                pos_data["avg_price"],
                pos_data["date"],
            )
            pos_worker.done.connect(lambda: self.load_data())
            pos_worker.error_occurred.connect(
                lambda msg: QMessageBox.critical(self, "Erro ao ajustar posição", msg)
            )
            pos_worker.start()
            self._pos_workers = getattr(self, "_pos_workers", [])
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
                (d.get("payment_date", ""), "#E8EAED"),
                (d.get("ticker") or d.get("asset_name") or "—", "#4A9EFF"),
                (_DIVIDEND_TYPE_DISPLAY.get(d.get("dividend_type", ""), d.get("dividend_type", "")), "#8B90A7"),
                (_fmt_brl(float(d.get("amount_per_unit", 0))), "#E8EAED"),
                (_fmt_brl(float(d.get("total_amount", 0))), "#00C896"),
                (_fmt_brl(float(d.get("tax_withheld", 0))), "#FFB347" if float(d.get("tax_withheld", 0)) > 0 else "#8B90A7"),
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


# ======================================================================
# Componentes de UI reutilizáveis
# ======================================================================


class _SummaryCard(QFrame):
    """Card compacto para exibir um indicador resumido (reutiliza estilo summaryCard)."""

    def __init__(self, title: str, default_color: str = "#E8EAED") -> None:
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
