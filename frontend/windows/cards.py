"""
Página de Cartões e Faturas — gestão de cartões de crédito e acompanhamento de faturas.

Layout:
  ┌─────────────────────────────────────────────────────────────────────┐
  │  [+ Novo Cartão]                                                    │  ← toolbar
  ├──────────────────────────────────────────────────────────────────── ┤
  │  Nome │ Banco │ Final │ Limite │ Fechamento │ Vencimento │ Ações    │  ← tabela cartões
  ├─────────────────────────────────────────────────────────────────────┤
  │  Faturas do cartão selecionado                                      │
  │  Mês/Ano │ Vencimento │ Total │ Status │ Ações                      │  ← tabela faturas
  └─────────────────────────────────────────────────────────────────────┘

Threading:
  CardsWorker busca GET /cards e GET /accounts em background.
  InvoicesWorker busca GET /cards/{id}/invoices em background.
  Mutação (save/delete) em workers dedicados.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError


# ======================================================================
# Workers
# ======================================================================


class CardsWorker(QThread):
    """Busca cartões e contas em background."""

    data_ready = pyqtSignal(list, list)  # (cards, accounts)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            cards = self._client.get_cards()
            accounts = self._client.get_accounts()
            self.data_ready.emit(cards, accounts)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class InvoicesWorker(QThread):
    """Busca faturas de um cartão específico."""

    data_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, card_id: int) -> None:
        super().__init__()
        self._client = client
        self._card_id = card_id

    def run(self) -> None:
        try:
            invoices = self._client.get_card_invoices(self._card_id)
            self.data_ready.emit(invoices)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class SaveCardWorker(QThread):
    """Executa POST /cards em background."""

    saved = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.create_card(self._payload)
            self.saved.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class DeleteCardWorker(QThread):
    """Executa DELETE /cards/{id} em background."""

    deleted = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, card_id: int) -> None:
        super().__init__()
        self._client = client
        self._card_id = card_id

    def run(self) -> None:
        try:
            self._client.delete_card(self._card_id)
            self.deleted.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class PayInvoiceWorker(QThread):
    """Marca fatura como paga via PATCH /cards/{id}/invoices/{inv}/status."""

    done = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, card_id: int, invoice_id: int, new_status: str) -> None:
        super().__init__()
        self._client = client
        self._card_id = card_id
        self._invoice_id = invoice_id
        self._new_status = new_status

    def run(self) -> None:
        try:
            self._client.update_invoice_status(self._card_id, self._invoice_id, self._new_status)
            self.done.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Mapeamentos
# ======================================================================

_STATUS_DISPLAY = {
    "aberta":  "Aberta",
    "fechada": "Fechada",
    "paga":    "Paga",
}
_STATUS_COLOR = {
    "aberta":  "#4A9EFF",
    "fechada": "#FFB347",
    "paga":    "#00C896",
}
_MONTHS_PT = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
              "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

# Colunas da tabela de cartões
_CC_NAME = 0
_CC_BANK = 1
_CC_LAST4 = 2
_CC_LIMIT = 3
_CC_CLOSE = 4
_CC_DUE = 5
_CC_ACTIONS = 6

# Colunas da tabela de faturas
_INV_PERIOD = 0
_INV_DUE = 1
_INV_TOTAL = 2
_INV_STATUS = 3
_INV_ACTIONS = 4


# ======================================================================
# Diálogo de criação de cartão
# ======================================================================


class CardDialog(QDialog):
    """Formulário modal para criar um novo cartão de crédito."""

    def __init__(self, accounts: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Novo Cartão de Crédito")
        self.setMinimumWidth(440)
        self._accounts = accounts
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("Novo Cartão de Crédito")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Ex: Nubank Black, Itaú Visa…")
        form.addRow("Nome *", self._name)

        self._bank = QLineEdit()
        self._bank.setPlaceholderText("Ex: Nubank, Itaú, XP…")
        form.addRow("Banco *", self._bank)

        self._last4 = QLineEdit()
        self._last4.setPlaceholderText("4 dígitos finais")
        self._last4.setMaxLength(4)
        form.addRow("Final do cartão *", self._last4)

        self._limit = QDoubleSpinBox()
        self._limit.setRange(0.01, 999_999.99)
        self._limit.setDecimals(2)
        self._limit.setPrefix("R$ ")
        self._limit.setSingleStep(500.0)
        self._limit.setValue(5000.0)
        form.addRow("Limite *", self._limit)

        self._closing_day = QSpinBox()
        self._closing_day.setRange(1, 28)
        self._closing_day.setValue(10)
        form.addRow("Dia de fechamento *", self._closing_day)

        self._due_day = QSpinBox()
        self._due_day.setRange(1, 28)
        self._due_day.setValue(17)
        form.addRow("Dia de vencimento *", self._due_day)

        self._account_combo = QComboBox()
        self._account_combo.addItem("— Nenhuma —", userData=None)
        for acc in self._accounts:
            self._account_combo.addItem(f"{acc['name']} ({acc['bank_name']})", userData=acc["id"])
        form.addRow("Conta de pagamento", self._account_combo)

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

    def _on_accept(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe o nome do cartão.")
            return
        if not self._bank.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe o banco emissor.")
            return
        if len(self._last4.text().strip()) != 4 or not self._last4.text().strip().isdigit():
            QMessageBox.warning(self, "Campo inválido", "Informe exatamente 4 dígitos numéricos.")
            return
        self.accept()

    def get_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self._name.text().strip(),
            "bank_name": self._bank.text().strip(),
            "last_four_digits": self._last4.text().strip(),
            "credit_limit": f"{self._limit.value():.2f}",
            "closing_day": self._closing_day.value(),
            "due_day": self._due_day.value(),
        }
        account_id = self._account_combo.currentData()
        if account_id is not None:
            payload["payment_account_id"] = account_id
        return payload


# ======================================================================
# Página principal de Cartões
# ======================================================================


class CardsPage(QWidget):
    """
    Página de cartões e faturas.

    Ciclo de vida:
      __init__ → _build_ui → load_data
      Clique em linha → _load_invoices(card_id)
      Botão "Pagar" → PayInvoiceWorker → _load_invoices
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: CardsWorker | None = None
        self._inv_worker: InvoicesWorker | None = None
        self._save_worker: SaveCardWorker | None = None
        self._delete_worker: DeleteCardWorker | None = None
        self._pay_worker: PayInvoiceWorker | None = None
        self._cards: list[dict] = []
        self._accounts: list[dict] = []
        self._selected_card_id: int | None = None
        self._build_ui()
        self.load_data()

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

        # --- Cartões ---
        cards_header = QHBoxLayout()
        cards_label = QLabel("Cartões de crédito")
        cards_label.setObjectName("sectionTitle")
        cards_header.addWidget(cards_label)
        cards_header.addStretch()

        new_btn = QPushButton("+ Novo Cartão")
        new_btn.setProperty("class", "primary")
        new_btn.style().unpolish(new_btn)
        new_btn.style().polish(new_btn)
        new_btn.clicked.connect(self._open_create_dialog)
        cards_header.addWidget(new_btn)
        main.addLayout(cards_header)

        self._loading_label = QLabel("Carregando cartões…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._loading_label)

        self._cards_table = self._build_cards_table()
        self._cards_table.setVisible(False)
        self._cards_table.itemSelectionChanged.connect(self._on_card_selected)
        main.addWidget(self._cards_table)

        # --- Faturas ---
        inv_label = QLabel("Faturas")
        inv_label.setObjectName("sectionTitle")
        main.addWidget(inv_label)

        self._inv_loading = QLabel("Selecione um cartão para ver as faturas.")
        self._inv_loading.setObjectName("loadingLabel")
        self._inv_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._inv_loading)

        self._inv_table = self._build_invoices_table()
        self._inv_table.setVisible(False)
        main.addWidget(self._inv_table)

        main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_cards_table(self) -> QTableWidget:
        headers = ["Nome", "Banco", "Final", "Limite", "Fechamento", "Vencimento", "Ações"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_CC_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_CC_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_CC_ACTIONS, 90)
        return table

    def _build_invoices_table(self) -> QTableWidget:
        headers = ["Período", "Vencimento", "Total", "Status", "Ações"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_INV_PERIOD, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_INV_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_INV_ACTIONS, 100)
        return table

    # ------------------------------------------------------------------
    # Dados — Cartões
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._cards_table.setVisible(False)
        self._loading_label.setText("Carregando cartões…")
        self._loading_label.setVisible(True)

        self._worker = CardsWorker(self._client)
        self._worker.data_ready.connect(self._on_cards_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_cards_ready(self, cards: list[dict], accounts: list[dict]) -> None:
        self._cards = cards
        self._accounts = accounts
        self._loading_label.setVisible(False)
        self._cards_table.setVisible(True)
        self._populate_cards_table(cards)

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._cards_table.setVisible(False)

    def _populate_cards_table(self, cards: list[dict]) -> None:
        self._cards_table.setRowCount(len(cards))
        for row, card in enumerate(cards):
            limit = float(card.get("credit_limit", 0))
            data = [
                (card.get("name", ""), "#E8EAED"),
                (card.get("bank_name", ""), "#E8EAED"),
                (f"••••{card.get('last_four_digits', '')}", "#8B90A7"),
                (_fmt_brl(limit), "#4A9EFF"),
                (f"Dia {card.get('closing_day', '—')}", "#8B90A7"),
                (f"Dia {card.get('due_day', '—')}", "#8B90A7"),
            ]
            for col, (text, color) in enumerate(data):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                if col == _CC_LIMIT:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._cards_table.setItem(row, col, item)

            del_btn = QPushButton("Excluir")
            del_btn.setProperty("class", "danger")
            del_btn.style().unpolish(del_btn)
            del_btn.style().polish(del_btn)
            del_btn.clicked.connect(lambda _, c=card: self._confirm_delete_card(c))
            self._cards_table.setCellWidget(row, _CC_ACTIONS, del_btn)

    # ------------------------------------------------------------------
    # Dados — Faturas
    # ------------------------------------------------------------------

    def _on_card_selected(self) -> None:
        rows = self._cards_table.selectedItems()
        if not rows:
            return
        row = self._cards_table.currentRow()
        if row < 0 or row >= len(self._cards):
            return
        card = self._cards[row]
        self._selected_card_id = card["id"]
        self._load_invoices(card["id"])

    def _load_invoices(self, card_id: int) -> None:
        if self._inv_worker and self._inv_worker.isRunning():
            return
        self._inv_table.setVisible(False)
        self._inv_loading.setText("Carregando faturas…")
        self._inv_loading.setVisible(True)

        self._inv_worker = InvoicesWorker(self._client, card_id)
        self._inv_worker.data_ready.connect(self._on_invoices_ready)
        self._inv_worker.error_occurred.connect(
            lambda msg: self._inv_loading.setText(f"Erro: {msg}")
        )
        self._inv_worker.start()

    def _on_invoices_ready(self, invoices: list[dict]) -> None:
        self._invoices = invoices
        self._inv_loading.setVisible(False)
        self._inv_table.setVisible(True)
        self._populate_invoices_table(invoices)

    def _populate_invoices_table(self, invoices: list[dict]) -> None:
        self._inv_table.setRowCount(len(invoices))
        for row, inv in enumerate(invoices):
            month = inv.get("month", 0)
            year = inv.get("year", 0)
            period = f"{_MONTHS_PT[month]}/{year}" if 1 <= month <= 12 else "—"
            due_date = _fmt_date(inv.get("due_date", ""))
            total = float(inv.get("total_amount", 0))
            inv_status = inv.get("status", "aberta")
            status_label = _STATUS_DISPLAY.get(inv_status, inv_status)
            status_color = _STATUS_COLOR.get(inv_status, "#E8EAED")

            cells = [
                (period, "#E8EAED"),
                (due_date, "#8B90A7"),
                (_fmt_brl(total), "#E8EAED"),
                (status_label, status_color),
            ]
            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                if col == _INV_TOTAL:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._inv_table.setItem(row, col, item)

            # Botão de ação por status
            if inv_status == "aberta":
                action_btn = QPushButton("Fechar")
                action_btn.setProperty("class", "primary")
                action_btn.clicked.connect(
                    lambda _, c=self._selected_card_id, i=inv["id"]:
                        self._update_status(c, i, "fechada")
                )
            elif inv_status == "fechada":
                action_btn = QPushButton("Pagar")
                action_btn.setProperty("class", "success")
                action_btn.clicked.connect(
                    lambda _, c=self._selected_card_id, i=inv["id"]:
                        self._update_status(c, i, "paga")
                )
            else:
                action_btn = QPushButton("Paga ✓")
                action_btn.setEnabled(False)

            action_btn.style().unpolish(action_btn)
            action_btn.style().polish(action_btn)
            self._inv_table.setCellWidget(row, _INV_ACTIONS, action_btn)

    # ------------------------------------------------------------------
    # Ações
    # ------------------------------------------------------------------

    def _update_status(self, card_id: int, invoice_id: int, new_status: str) -> None:
        if self._pay_worker and self._pay_worker.isRunning():
            return
        self._pay_worker = PayInvoiceWorker(self._client, card_id, invoice_id, new_status)
        self._pay_worker.done.connect(lambda: self._load_invoices(card_id))
        self._pay_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro", msg)
        )
        self._pay_worker.start()

    def _open_create_dialog(self) -> None:
        dialog = CardDialog(self._accounts, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        if self._save_worker and self._save_worker.isRunning():
            return
        self._save_worker = SaveCardWorker(self._client, payload)
        self._save_worker.saved.connect(lambda _: self.load_data())
        self._save_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao salvar", msg)
        )
        self._save_worker.start()

    def _confirm_delete_card(self, card: dict) -> None:
        name = card.get("name", "este cartão")
        reply = QMessageBox.question(
            self,
            "Confirmar exclusão",
            f'Tem certeza que deseja excluir o cartão "{name}"?\n'
            "Todas as faturas vinculadas serão removidas.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._delete_worker and self._delete_worker.isRunning():
            return
        self._delete_worker = DeleteCardWorker(self._client, card["id"])
        self._delete_worker.deleted.connect(self.load_data)
        self._delete_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao excluir", msg)
        )
        self._delete_worker.start()


# ======================================================================
# Utilitários
# ======================================================================


def _fmt_brl(value: float) -> str:
    try:
        formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        prefix = "-R$ " if value < 0 else "R$ "
        return f"{prefix}{formatted}"
    except (TypeError, ValueError):
        return "—"


def _fmt_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return d.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso
