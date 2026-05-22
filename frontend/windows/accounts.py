"""
Página de Configurações de Contas — cadastro e gestão de contas bancárias.

Layout:
  ┌─────────────────────────────────────────────────────────────┐
  │  [+ Nova Conta]                                             │  ← toolbar
  ├─────────────────────────────────────────────────────────────┤
  │  Nome  │  Banco  │  Tipo  │  Moeda  │  Saldo Inicial │ Ações│  ← tabela
  │  …     │  …      │  …     │  …      │  …             │ ✏ 🗑 │
  └─────────────────────────────────────────────────────────────┘

Threading:
  AccountsWorker busca GET /accounts em background.
  SaveAccountWorker executa POST /accounts em background.
  DeleteAccountWorker executa DELETE /accounts/{id} em background.
"""

from __future__ import annotations

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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.signals import app_signals


# ======================================================================
# Workers
# ======================================================================


class AccountsWorker(QThread):
    """Busca a lista de contas em background."""

    data_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            accounts = self._client.get_accounts()
            for acc in accounts:
                try:
                    bal = self._client.get_account_balance(acc["id"])
                    acc["_current_balance"] = bal.get("balance", acc.get("initial_balance", 0))
                except ApiError:
                    acc["_current_balance"] = acc.get("initial_balance", 0)
            self.data_ready.emit(accounts)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class SaveAccountWorker(QThread):
    """Executa POST /accounts em background."""

    saved = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.create_account(self._payload)
            self.saved.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class UpdateAccountWorker(QThread):
    """Executa PUT /accounts/{id} em background."""

    updated = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, account_id: int, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._account_id = account_id
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.update_account(self._account_id, self._payload)
            self.updated.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class DeleteAccountWorker(QThread):
    """Executa DELETE /accounts/{id} em background."""

    deleted = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, account_id: int) -> None:
        super().__init__()
        self._client = client
        self._account_id = account_id

    def run(self) -> None:
        try:
            self._client.delete_account(self._account_id)
            self.deleted.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Mapeamentos de exibição
# ======================================================================

_TYPE_LABELS = {
    "Conta corrente":  "corrente",
    "Poupança":        "poupanca",
    "Investimento":    "investimento",
    "Carteira digital":"digital",
    "Espécie":         "especie",
}
_TYPE_DISPLAY = {v: k for k, v in _TYPE_LABELS.items()}

_COL_NAME    = 0
_COL_BANK    = 1
_COL_TYPE    = 2
_COL_CURRENCY= 3
_COL_BALANCE = 4
_COL_CURRENT = 5
_COL_ACTIONS = 6


# ======================================================================
# Diálogo de criação de conta
# ======================================================================


class AccountDialog(QDialog):
    """Formulário modal para criar uma nova conta bancária."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nova Conta")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("Nova Conta")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Ex: Nubank Pessoal, Bradesco Salário…")
        form.addRow("Nome *", self._name)

        self._bank = QLineEdit()
        self._bank.setPlaceholderText("Ex: Nubank, Bradesco, XP…")
        form.addRow("Banco *", self._bank)

        self._type = QComboBox()
        for label in _TYPE_LABELS:
            self._type.addItem(label)
        form.addRow("Tipo *", self._type)

        self._currency = QComboBox()
        for cur in ["BRL", "USD", "EUR"]:
            self._currency.addItem(cur)
        form.addRow("Moeda", self._currency)

        self._initial_balance = QDoubleSpinBox()
        self._initial_balance.setRange(-9_999_999.99, 9_999_999.99)
        self._initial_balance.setDecimals(2)
        self._initial_balance.setPrefix("R$ ")
        self._initial_balance.setSingleStep(100.0)
        form.addRow("Saldo inicial", self._initial_balance)

        self._balance_date = QDateEdit()
        self._balance_date.setCalendarPopup(True)
        from PyQt6.QtCore import QDate
        self._balance_date.setDate(QDate.currentDate())
        self._balance_date.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Data do saldo *", self._balance_date)

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
        if not self._name.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe o nome da conta.")
            self._name.setFocus()
            return
        if not self._bank.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe o nome do banco.")
            self._bank.setFocus()
            return
        self.accept()

    def get_payload(self) -> dict[str, Any]:
        qdate = self._balance_date.date()
        return {
            "name": self._name.text().strip(),
            "bank_name": self._bank.text().strip(),
            "account_type": _TYPE_LABELS[self._type.currentText()],
            "currency": self._currency.currentText(),
            "initial_balance": f"{self._initial_balance.value():.2f}",
            "initial_balance_date": date(qdate.year(), qdate.month(), qdate.day()).isoformat(),
        }


# ======================================================================
# Diálogo de edição de conta
# ======================================================================


class EditAccountDialog(AccountDialog):
    """Formulário modal para editar uma conta existente (pré-preenchido)."""

    def __init__(self, account: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editar Conta")
        self._account_id = account["id"]
        self._prefill(account)

    def _prefill(self, account: dict[str, Any]) -> None:
        self._name.setText(account.get("name", ""))
        self._bank.setText(account.get("bank_name", ""))

        acc_type = account.get("account_type", "corrente")
        display = _TYPE_DISPLAY.get(acc_type, "Conta corrente")
        idx = self._type.findText(display)
        if idx >= 0:
            self._type.setCurrentIndex(idx)

        currency = account.get("currency", "BRL")
        idx = self._currency.findText(currency)
        if idx >= 0:
            self._currency.setCurrentIndex(idx)

        try:
            self._initial_balance.setValue(float(account.get("initial_balance", 0)))
        except (TypeError, ValueError):
            pass

        iso = account.get("initial_balance_date", "")
        if iso:
            from PyQt6.QtCore import QDate
            try:
                d = date.fromisoformat(iso)
                self._balance_date.setDate(QDate(d.year, d.month, d.day))
            except (ValueError, TypeError):
                pass

    def get_payload(self) -> dict[str, Any]:
        payload = super().get_payload()
        return payload


# ======================================================================
# Página principal de Contas
# ======================================================================


class AccountsPage(QWidget):
    """
    Página de gestão de contas bancárias e carteiras digitais.

    Ciclo de vida:
      __init__ → _build_ui → load_data
      Botão "Nova Conta" → AccountDialog → SaveAccountWorker → load_data
      Botão "Excluir" → confirmação → DeleteAccountWorker → load_data
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: AccountsWorker | None = None
        self._save_worker: SaveAccountWorker | None = None
        self._update_worker: UpdateAccountWorker | None = None
        self._delete_worker: DeleteAccountWorker | None = None
        self._accounts: list[dict] = []
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

        # Cabeçalho da seção
        header_row = QHBoxLayout()
        section_label = QLabel("Contas bancárias e carteiras")
        section_label.setObjectName("sectionTitle")
        header_row.addWidget(section_label)
        header_row.addStretch()

        new_btn = QPushButton("+ Nova Conta")
        new_btn.setProperty("class", "primary")
        new_btn.style().unpolish(new_btn)
        new_btn.style().polish(new_btn)
        new_btn.clicked.connect(self._open_create_dialog)
        header_row.addWidget(new_btn)
        main.addLayout(header_row)

        # Loading
        self._loading_label = QLabel("Carregando contas…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._loading_label)

        # Tabela
        self._table = self._build_table()
        self._table.setVisible(False)
        main.addWidget(self._table)

        main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_table(self) -> QTableWidget:
        headers = ["Nome", "Banco", "Tipo", "Moeda", "Saldo Inicial", "Saldo Atual", "Ações"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_COL_ACTIONS, 76)

        return table

    # ------------------------------------------------------------------
    # Dados
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._table.setVisible(False)
        self._loading_label.setText("Carregando contas…")
        self._loading_label.setVisible(True)

        self._worker = AccountsWorker(self._client)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_data_ready(self, accounts: list[dict]) -> None:
        self._accounts = accounts
        self._loading_label.setVisible(False)
        self._table.setVisible(True)
        self._populate_table(accounts)

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._table.setVisible(False)

    def _populate_table(self, accounts: list[dict]) -> None:
        self._table.setRowCount(len(accounts))
        for row, acc in enumerate(accounts):
            initial_balance = float(acc.get("initial_balance", 0))
            current_balance = float(acc.get("_current_balance", initial_balance))
            current_color   = "#00C896" if current_balance >= 0 else "#FF6B6B"

            data = [
                (acc.get("name", ""), "#E8EAED"),
                (acc.get("bank_name", ""), "#E8EAED"),
                (_TYPE_DISPLAY.get(acc.get("account_type", ""), acc.get("account_type", "")), "#8B90A7"),
                (acc.get("currency", "BRL"), "#8B90A7"),
                (_fmt_brl(initial_balance), "#4A9EFF"),
                (_fmt_brl(current_balance), current_color),
            ]

            for col, (text, color) in enumerate(data):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                if col in (_COL_BALANCE, _COL_CURRENT):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, col, item)

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(4)
            actions_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            edit_btn = QPushButton("✏️")
            edit_btn.setFixedSize(32, 32)
            edit_btn.setToolTip("Editar conta")
            edit_btn.clicked.connect(lambda _, a=acc: self._open_edit_dialog(a))

            del_btn = QPushButton("🗑️")
            del_btn.setFixedSize(32, 32)
            del_btn.setToolTip("Excluir conta")
            del_btn.clicked.connect(lambda _, a=acc: self._confirm_delete(a))

            actions_layout.addWidget(edit_btn)
            actions_layout.addWidget(del_btn)
            self._table.setCellWidget(row, _COL_ACTIONS, actions)

    # ------------------------------------------------------------------
    # Criação
    # ------------------------------------------------------------------

    def _open_create_dialog(self) -> None:
        dialog = AccountDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        self._start_save(payload)

    def _start_save(self, payload: dict) -> None:
        if self._save_worker and self._save_worker.isRunning():
            return
        self._save_worker = SaveAccountWorker(self._client, payload)
        self._save_worker.saved.connect(lambda _: (app_signals.data_changed.emit(), self.load_data()))
        self._save_worker.error_occurred.connect(self._on_save_error)
        self._save_worker.start()

    def _on_save_error(self, message: str) -> None:
        QMessageBox.critical(self, "Erro ao salvar", message)

    # ------------------------------------------------------------------
    # Edição
    # ------------------------------------------------------------------

    def _open_edit_dialog(self, account: dict) -> None:
        dialog = EditAccountDialog(account, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        self._start_update(account["id"], payload)

    def _start_update(self, account_id: int, payload: dict) -> None:
        if self._update_worker and self._update_worker.isRunning():
            return
        self._update_worker = UpdateAccountWorker(self._client, account_id, payload)
        self._update_worker.updated.connect(lambda _: (app_signals.data_changed.emit(), self.load_data()))
        self._update_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao atualizar", msg)
        )
        self._update_worker.start()

    # ------------------------------------------------------------------
    # Exclusão
    # ------------------------------------------------------------------

    def _confirm_delete(self, account: dict) -> None:
        name = account.get("name", "esta conta")
        reply = QMessageBox.question(
            self,
            "Confirmar exclusão",
            f'Tem certeza que deseja excluir a conta "{name}"?\n'
            "Todos os lançamentos vinculados serão desvinculados.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_delete(account["id"])

    def _start_delete(self, account_id: int) -> None:
        if self._delete_worker and self._delete_worker.isRunning():
            return
        self._delete_worker = DeleteAccountWorker(self._client, account_id)
        self._delete_worker.deleted.connect(lambda: (app_signals.data_changed.emit(), self.load_data()))
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
