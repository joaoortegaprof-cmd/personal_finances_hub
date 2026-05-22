"""
Página de Contas — gestão visual de contas bancárias e carteiras.

Layout:
  ┌──────────────────────────────────────────────────────────────────┐
  │  Contas bancárias e carteiras    [+ Nova Conta] [↔ Transferir]   │
  ├──────────────────────────────────────────────────────────────────┤
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
  │  │ AccountCard  │  │ AccountCard  │  │ AccountCard  │  ← grid   │
  │  └──────────────┘  └──────────────┘  └──────────────┘           │
  └──────────────────────────────────────────────────────────────────┘

Cada AccountCard exibe:
  - Accent top-border colorido por tipo de conta
  - Avatar circular com inicial do banco
  - Nome, banco, tipo
  - Saldo atual em destaque
  - Botões: + Lançamento | ↔ Transferir | ✏ Editar | 🗑 Excluir

Threading:
  AccountsWorker — GET /accounts (com saldo atual por conta)
  SaveAccountWorker — POST /accounts
  UpdateAccountWorker — PUT /accounts/{id}
  DeleteAccountWorker — DELETE /accounts/{id}
  TransferWorker — POST /transactions × 2 (débito + receita)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
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
from frontend.components.icons import icon as _svg_icon
from frontend.components.signals import app_signals


# ======================================================================
# Workers
# ======================================================================


class AccountsWorker(QThread):
    """Busca contas e saldo atual de cada uma em background."""

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
    saved = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._payload = payload

    def run(self) -> None:
        try:
            self.saved.emit(self._client.create_account(self._payload))
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class UpdateAccountWorker(QThread):
    updated = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, account_id: int, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._account_id = account_id
        self._payload = payload

    def run(self) -> None:
        try:
            self.updated.emit(self._client.update_account(self._account_id, self._payload))
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class DeleteAccountWorker(QThread):
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


class AccountBalanceWorker(QThread):
    """
    Busca o saldo atualizado de uma única conta em background.

    Emite balance_ready(float) com o saldo calculado, ou error_occurred()
    em caso de falha (sem exibir erro ao usuário — o card mantém o valor anterior).
    """

    balance_ready  = pyqtSignal(float)
    error_occurred = pyqtSignal()

    def __init__(self, client: ApiClient, account_id: int) -> None:
        super().__init__()
        self._client     = client
        self._account_id = account_id

    def run(self) -> None:
        try:
            bal = self._client.get_account_balance(self._account_id)
            self.balance_ready.emit(float(bal.get("balance", 0)))
        except Exception:
            self.error_occurred.emit()


class TransferWorker(QThread):
    """
    Cria duas transações para simular uma transferência entre contas:
      1. DEBIT_EXPENSE (saída) na conta de origem
      2. INCOME (entrada) na conta de destino

    Ambas têm category='transferencia' e a mesma descrição/data.
    """

    done = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        client: ApiClient,
        from_account_id: int,
        to_account_id: int,
        amount: float,
        transfer_date: str,
        description: str,
    ) -> None:
        super().__init__()
        self._client = client
        self._from = from_account_id
        self._to   = to_account_id
        self._amount = amount
        self._date = transfer_date
        self._desc = description

    def run(self) -> None:
        try:
            # Débito na conta de origem
            self._client.create_transaction({
                "description":     self._desc,
                "amount":          self._amount,
                "transaction_date": self._date,
                "transaction_type": "debit",
                "category":        "transferencia",
                "expense_nature":  "transfer",
                "account_id":      self._from,
            })
            # Receita na conta de destino
            self._client.create_transaction({
                "description":     self._desc,
                "amount":          self._amount,
                "transaction_date": self._date,
                "transaction_type": "income",
                "category":        "transferencia",
                "account_id":      self._to,
            })
            self.done.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Mapeamentos
# ======================================================================

_TYPE_LABELS: dict[str, str] = {
    "Conta corrente":  "corrente",
    "Poupança":        "poupanca",
    "Investimento":    "investimento",
    "Carteira digital":"digital",
    "Espécie":         "especie",
}
_TYPE_DISPLAY = {v: k for k, v in _TYPE_LABELS.items()}

# Cor de accent por tipo de conta (top-border do card)
_TYPE_COLOR: dict[str, str] = {
    "corrente":    "#4A9EFF",
    "poupanca":    "#00C896",
    "investimento":"#8B5CF6",
    "digital":     "#FFB347",
    "especie":     "#84CC16",
}

# Ícone SVG por tipo (usado no avatar)
_TYPE_ICON: dict[str, str] = {
    "corrente":    "wallet",
    "poupanca":    "dollar",
    "investimento":"trending_up",
    "digital":     "card",
    "especie":     "dollar",
}


# ======================================================================
# Diálogos de conta (criar / editar)
# ======================================================================


class AccountDialog(QDialog):
    """Formulário modal para criar uma nova conta bancária."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nova Conta")
        self.setMinimumWidth(440)
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
            "name":                 self._name.text().strip(),
            "bank_name":            self._bank.text().strip(),
            "account_type":         _TYPE_LABELS[self._type.currentText()],
            "currency":             self._currency.currentText(),
            "initial_balance":      f"{self._initial_balance.value():.2f}",
            "initial_balance_date": date(qdate.year(), qdate.month(), qdate.day()).isoformat(),
        }


class EditAccountDialog(AccountDialog):
    def __init__(self, account: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editar Conta")
        self._account_id = account["id"]
        self._prefill(account)

    def _prefill(self, account: dict[str, Any]) -> None:
        self._name.setText(account.get("name", ""))
        self._bank.setText(account.get("bank_name", ""))

        display = _TYPE_DISPLAY.get(account.get("account_type", "corrente"), "Conta corrente")
        idx = self._type.findText(display)
        if idx >= 0:
            self._type.setCurrentIndex(idx)

        idx = self._currency.findText(account.get("currency", "BRL"))
        if idx >= 0:
            self._currency.setCurrentIndex(idx)

        try:
            self._initial_balance.setValue(float(account.get("initial_balance", 0)))
        except (TypeError, ValueError):
            pass

        iso = account.get("initial_balance_date", "")
        if iso:
            try:
                d = date.fromisoformat(iso)
                self._balance_date.setDate(QDate(d.year, d.month, d.day))
            except (ValueError, TypeError):
                pass


# ======================================================================
# Diálogo de transferência
# ======================================================================


class TransferDialog(QDialog):
    """
    Formulário para transferir valor entre duas contas.

    Cria dois lançamentos:
      - DEBIT_EXPENSE (saída) na conta de origem
      - INCOME (entrada) na conta de destino
    """

    def __init__(self, accounts: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Transferência entre contas")
        self.setMinimumWidth(440)
        self._accounts = accounts
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("Transferência entre contas")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        sub = QLabel("Cria automaticamente um débito na origem e uma receita no destino.")
        sub.setStyleSheet("color: #8B90A7; font-size: 11px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Conta de origem
        self._from_combo = QComboBox()
        for acc in self._accounts:
            self._from_combo.addItem(acc["name"], userData=acc["id"])
        form.addRow("Origem *", self._from_combo)

        # Conta de destino
        self._to_combo = QComboBox()
        for acc in self._accounts:
            self._to_combo.addItem(acc["name"], userData=acc["id"])
        if self._to_combo.count() > 1:
            self._to_combo.setCurrentIndex(1)
        form.addRow("Destino *", self._to_combo)

        # Valor
        self._amount = QDoubleSpinBox()
        self._amount.setRange(0.01, 9_999_999.99)
        self._amount.setDecimals(2)
        self._amount.setPrefix("R$ ")
        self._amount.setSingleStep(100.0)
        form.addRow("Valor *", self._amount)

        # Data
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Data *", self._date_edit)

        # Descrição
        self._desc = QLineEdit()
        self._desc.setText("Transferência entre contas")
        form.addRow("Descrição", self._desc)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("Transferir")
            ok_btn.setProperty("class", "success")
            ok_btn.style().unpolish(ok_btn)
            ok_btn.style().polish(ok_btn)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        from_id = self._from_combo.currentData()
        to_id   = self._to_combo.currentData()

        if from_id is None or to_id is None:
            QMessageBox.warning(self, "Campo obrigatório", "Selecione as contas.")
            return
        if from_id == to_id:
            QMessageBox.warning(self, "Contas iguais", "Origem e destino devem ser diferentes.")
            return
        if self._amount.value() <= 0:
            QMessageBox.warning(self, "Campo obrigatório", "Informe um valor maior que zero.")
            return
        self.accept()

    def get_params(self) -> dict:
        qdate = self._date_edit.date()
        return {
            "from_account_id": self._from_combo.currentData(),
            "to_account_id":   self._to_combo.currentData(),
            "amount":          self._amount.value(),
            "date":            date(qdate.year(), qdate.month(), qdate.day()).isoformat(),
            "description":     self._desc.text().strip() or "Transferência entre contas",
        }


# ======================================================================
# Widget AccountCard
# ======================================================================


class _AvatarWidget(QWidget):
    """
    Círculo colorido com a inicial do banco — usado no topo do AccountCard.
    Renderizado via paintEvent para evitar artefatos de borda do QLabel.
    """

    def __init__(self, letter: str, color: str, size: int = 40, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._letter = letter.upper()[:1]
        self._color  = QColor(color)
        self._size   = size
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Círculo
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._color))
        painter.drawEllipse(0, 0, self._size, self._size)

        # Letra
        font = QFont()
        font.setPointSize(int(self._size * 0.38))
        font.setWeight(700)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._letter)
        painter.end()


class AccountCard(QFrame):
    """
    Card visual para uma conta bancária.

    ┌──────────────────────────────────────────┐ ← accent border-top (type color)
    │  [●] Nubank Pessoal           [✏]  [🗑]  │ ← avatar + name + action icons
    │      Nubank · Conta corrente              │ ← subtitle
    │                                           │
    │           R$ 12.340,50                    │ ← balance (large)
    │           Saldo atual                     │ ← caption
    │                                           │
    │  [+ Lançamento]        [↔ Transferir]     │ ← action buttons
    └──────────────────────────────────────────┘
    """

    edit_requested     = pyqtSignal(dict)
    delete_requested   = pyqtSignal(dict)
    new_transaction    = pyqtSignal(dict)   # emits account dict
    transfer_from      = pyqtSignal(dict)   # emits account dict (source)

    def __init__(self, account: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._account = account
        self._client: ApiClient = ApiClient()
        self._balance_worker: AccountBalanceWorker | None = None
        self._build(account)

    def _build(self, acc: dict) -> None:
        acc_type     = acc.get("account_type", "corrente")
        type_color   = _TYPE_COLOR.get(acc_type, "#4A9EFF")
        type_label   = _TYPE_DISPLAY.get(acc_type, acc_type)
        bank_name    = acc.get("bank_name", "")
        acc_name     = acc.get("name", "—")
        balance      = float(acc.get("_current_balance", acc.get("initial_balance", 0)))
        bal_color    = "#00C896" if balance >= 0 else "#FF6B6B"

        # Card frame styling — top accent border + subtle matching glow
        self.setObjectName("accountCard")
        self.setFixedWidth(280)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.setStyleSheet(f"""
            QFrame#accountCard {{
                background-color: #1E2235;
                border: 1px solid #2D3152;
                border-top: 3px solid {type_color};
                border-radius: 12px;
            }}
        """)

        main = QVBoxLayout(self)
        main.setContentsMargins(16, 14, 16, 16)
        main.setSpacing(0)

        # ── Linha 1: avatar + nome + edit/delete ──────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        initial = bank_name[:1] if bank_name else "?"
        avatar = _AvatarWidget(initial, type_color, size=38)
        top_row.addWidget(avatar)

        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        name_lbl = QLabel(acc_name)
        name_lbl.setStyleSheet(
            "color: #E8EAED; font-weight: 700; font-size: 13px; background: transparent;"
        )
        name_lbl.setWordWrap(False)
        name_lbl.setMaximumWidth(140)
        sub_lbl = QLabel(f"{bank_name} · {type_label}")
        sub_lbl.setStyleSheet(
            "color: #8B90A7; font-size: 10px; background: transparent;"
        )
        name_col.addWidget(name_lbl)
        name_col.addWidget(sub_lbl)
        top_row.addLayout(name_col, 1)

        # Ações de gerenciamento (edit + delete)
        actions_col = QVBoxLayout()
        actions_col.setSpacing(2)
        actions_col.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        edit_btn = QPushButton()
        edit_btn.setIcon(_svg_icon("edit", "#8B90A7", 12))
        edit_btn.setFixedSize(26, 26)
        edit_btn.setToolTip("Editar conta")
        edit_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 5px; }"
            "QPushButton:hover { background: #2D3152; }"
        )
        edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._account))

        del_btn = QPushButton()
        del_btn.setIcon(_svg_icon("delete", "#FF6B6B", 12))
        del_btn.setFixedSize(26, 26)
        del_btn.setToolTip("Excluir conta")
        del_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 5px; }"
            "QPushButton:hover { background: #3D1E1E; }"
        )
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._account))

        actions_col.addWidget(edit_btn)
        actions_col.addWidget(del_btn)
        top_row.addLayout(actions_col)

        main.addLayout(top_row)
        main.addSpacing(14)

        # ── Linha 2: saldo ────────────────────────────────────────────
        self._balance_lbl = QLabel(_fmt_brl(balance))
        self._balance_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._balance_lbl.setStyleSheet(
            f"color: {bal_color}; font-size: 22px; font-weight: 700; background: transparent;"
        )
        main.addWidget(self._balance_lbl)

        saldo_cap = QLabel("Saldo atual")
        saldo_cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        saldo_cap.setStyleSheet("color: #6B7080; font-size: 10px; background: transparent;")
        main.addWidget(saldo_cap)

        main.addSpacing(16)

        # ── Separador ─────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #2D3152; border: none; max-height: 1px;")
        main.addWidget(sep)
        main.addSpacing(12)

        # ── Linha 3: botões de ação ───────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        launch_btn = QPushButton(" Lançamento")
        launch_btn.setIcon(_svg_icon("add_circle", type_color, 14))
        launch_btn.setToolTip("Novo lançamento nesta conta")
        launch_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {type_color};
                border: 1px solid {type_color}80;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 5px 8px;
            }}
            QPushButton:hover {{
                background-color: {type_color}18;
            }}
        """)
        launch_btn.clicked.connect(lambda: self.new_transaction.emit(self._account))

        transfer_btn = QPushButton(" Transferir")
        transfer_btn.setIcon(_svg_icon("transfer", "#8B90A7", 12))
        transfer_btn.setToolTip("Transferir desta conta")
        transfer_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #8B90A7;
                border: 1px solid #3D4160;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 5px 8px;
            }
            QPushButton:hover {
                background-color: #2D3152;
                color: #C8CAD8;
            }
        """)
        transfer_btn.clicked.connect(lambda: self.transfer_from.emit(self._account))

        btn_row.addWidget(launch_btn, 1)
        btn_row.addWidget(transfer_btn, 1)
        main.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Atualização de saldo em tempo real (Etapa 4)
    # ------------------------------------------------------------------

    def _start_balance_refresh(self) -> None:
        """
        Dispara AccountBalanceWorker para buscar o saldo atualizado.

        Chamado por AccountsPage quando app_signals.data_changed é emitido
        (lançamento adicionado, transferência realizada, etc.).
        Mostra "…" durante o carregamento para indicar que o valor está
        sendo atualizado sem destruir o layout do card.
        """
        if self._balance_worker and self._balance_worker.isRunning():
            return
        account_id = self._account.get("id")
        if not account_id:
            return

        # Estado de carregamento: mesma fonte, cor neutra
        self._balance_lbl.setText("…")
        self._balance_lbl.setStyleSheet(
            "color: #6B7080; font-size: 22px; font-weight: 700; background: transparent;"
        )

        self._balance_worker = AccountBalanceWorker(self._client, account_id)
        self._balance_worker.balance_ready.connect(self.update_balance)
        self._balance_worker.error_occurred.connect(self._on_balance_error)
        self._balance_worker.start()

    def _on_balance_error(self) -> None:
        """Restaura o saldo armazenado quando a requisição falhar."""
        bal = float(
            self._account.get("_current_balance",
            self._account.get("initial_balance", 0))
        )
        self.update_balance(bal)

    def update_balance(self, new_balance: float) -> None:
        """Atualiza o label de saldo e sua cor sem recriar o card."""
        self._account["_current_balance"] = new_balance
        bal_color = "#00C896" if new_balance >= 0 else "#FF6B6B"
        self._balance_lbl.setText(_fmt_brl(new_balance))
        self._balance_lbl.setStyleSheet(
            f"color: {bal_color}; font-size: 22px; font-weight: 700; background: transparent;"
        )


# ======================================================================
# Página principal
# ======================================================================


class AccountsPage(QWidget):
    """
    Página de gestão de contas — layout em grid de cards visuais.

    Ciclo:
      __init__ → _build_ui → load_data
      → AccountsWorker → _on_data_ready → _populate_cards
      card.new_transaction → app_signals.open_new_transaction_for_account
      card.transfer_from → _open_transfer_dialog
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker:        AccountsWorker       | None = None
        self._save_worker:   SaveAccountWorker    | None = None
        self._update_worker: UpdateAccountWorker  | None = None
        self._delete_worker: DeleteAccountWorker  | None = None
        self._transfer_worker: TransferWorker     | None = None
        self._accounts: list[dict] = []
        # Referências aos cards ativos (para disparar refresh de saldo)
        self._cards: list[AccountCard] = []
        self._build_ui()
        self.load_data()

        # Quando qualquer dado muda (lançamento, transferência de outra página…),
        # atualiza os saldos de todos os cards sem recarregar toda a lista.
        app_signals.data_changed.connect(self._refresh_all_balances)

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
        self._main = QVBoxLayout(content)
        self._main.setContentsMargins(32, 24, 32, 24)
        self._main.setSpacing(20)

        # ── Cabeçalho ─────────────────────────────────────────────────
        header_row = QHBoxLayout()
        section_label = QLabel("Contas bancárias e carteiras")
        section_label.setObjectName("sectionTitle")
        header_row.addWidget(section_label)
        header_row.addStretch()

        self._transfer_btn = QPushButton(" Transferência")
        self._transfer_btn.setIcon(_svg_icon("transfer", "#C8CAD8", 14))
        self._transfer_btn.setToolTip("Transferir entre contas")
        self._transfer_btn.setVisible(False)  # visível só quando há ≥ 2 contas
        self._transfer_btn.clicked.connect(self._open_transfer_dialog)
        header_row.addWidget(self._transfer_btn)

        new_btn = QPushButton(" Nova Conta")
        new_btn.setIcon(_svg_icon("add_circle", "#FFFFFF", 16))
        new_btn.setProperty("class", "primary")
        new_btn.style().unpolish(new_btn)
        new_btn.style().polish(new_btn)
        new_btn.clicked.connect(self._open_create_dialog)
        header_row.addWidget(new_btn)
        self._main.addLayout(header_row)

        # ── Loading ───────────────────────────────────────────────────
        self._loading_label = QLabel("Carregando contas…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._main.addWidget(self._loading_label)

        # ── Área dos cards (preenchida em _populate_cards) ────────────
        self._cards_area = QWidget()
        self._cards_layout = _WrapLayout(self._cards_area, h_spacing=16, v_spacing=16)
        self._cards_area.setVisible(False)
        self._main.addWidget(self._cards_area)

        # ── Estado vazio ──────────────────────────────────────────────
        self._empty_widget = self._build_empty_state()
        self._empty_widget.setVisible(False)
        self._main.addWidget(self._empty_widget)

        self._main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    @staticmethod
    def _build_empty_state() -> QWidget:
        """Placeholder exibido quando não há contas cadastradas."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_svg_icon("wallet", "#3D4160", 48).pixmap(48, 48))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon_lbl)

        title = QLabel("Nenhuma conta cadastrada")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #6B7080; font-size: 16px; font-weight: 600;")
        lay.addWidget(title)

        sub = QLabel(
            "Crie sua primeira conta para começar a registrar receitas e despesas."
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #4A4E6A; font-size: 12px;")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        return w

    # ------------------------------------------------------------------
    # Dados
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._cards_area.setVisible(False)
        self._empty_widget.setVisible(False)
        self._loading_label.setText("Carregando contas…")
        self._loading_label.setVisible(True)

        self._worker = AccountsWorker(self._client)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_data_ready(self, accounts: list[dict]) -> None:
        self._accounts = accounts
        self._loading_label.setVisible(False)

        if not accounts:
            self._empty_widget.setVisible(True)
            self._transfer_btn.setVisible(False)
            return

        self._cards_area.setVisible(True)
        self._transfer_btn.setVisible(len(accounts) >= 2)
        self._populate_cards(accounts)

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._cards_area.setVisible(False)

    def _refresh_all_balances(self) -> None:
        """
        Dispara _start_balance_refresh em cada card ativo.

        Chamado quando app_signals.data_changed é emitido por qualquer
        página (TransactionsPage, CardsPage…). Mantém os cards existentes
        e apenas atualiza os valores exibidos.
        """
        for card in self._cards:
            card._start_balance_refresh()

    def _populate_cards(self, accounts: list[dict]) -> None:
        """Remove os cards antigos e cria um novo para cada conta."""
        # Limpa lista de referências antes de deletar os widgets
        self._cards.clear()

        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        for acc in accounts:
            card = AccountCard(acc)
            card.edit_requested.connect(self._open_edit_dialog)
            card.delete_requested.connect(self._confirm_delete)
            card.new_transaction.connect(
                lambda a: app_signals.open_new_transaction_for_account.emit(a["id"])
            )
            card.transfer_from.connect(self._open_transfer_dialog_from)
            self._cards_layout.addWidget(card)
            self._cards.append(card)

    # ------------------------------------------------------------------
    # Criar conta
    # ------------------------------------------------------------------

    def _open_create_dialog(self) -> None:
        dialog = AccountDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._start_save(dialog.get_payload())

    def _start_save(self, payload: dict) -> None:
        if self._save_worker and self._save_worker.isRunning():
            return
        self._save_worker = SaveAccountWorker(self._client, payload)
        self._save_worker.saved.connect(lambda _: (app_signals.data_changed.emit(), self.load_data()))
        self._save_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao salvar", msg)
        )
        self._save_worker.start()

    # ------------------------------------------------------------------
    # Editar conta
    # ------------------------------------------------------------------

    def _open_edit_dialog(self, account: dict) -> None:
        dialog = EditAccountDialog(account, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._start_update(account["id"], dialog.get_payload())

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
    # Excluir conta
    # ------------------------------------------------------------------

    def _confirm_delete(self, account: dict) -> None:
        name = account.get("name", "esta conta")
        reply = QMessageBox.question(
            self,
            "Confirmar exclusão",
            f'Excluir a conta "{name}"?\n'
            "Os lançamentos vinculados serão desvinculados.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._delete_worker and self._delete_worker.isRunning():
            return
        self._delete_worker = DeleteAccountWorker(self._client, account["id"])
        self._delete_worker.deleted.connect(lambda: (app_signals.data_changed.emit(), self.load_data()))
        self._delete_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao excluir", msg)
        )
        self._delete_worker.start()

    # ------------------------------------------------------------------
    # Transferência
    # ------------------------------------------------------------------

    def _open_transfer_dialog(self) -> None:
        """Abre o diálogo de transferência sem conta pré-selecionada."""
        self._open_transfer_dialog_from(None)

    def _open_transfer_dialog_from(self, source_account: dict | None) -> None:
        """Abre o diálogo de transferência (com conta de origem pré-selecionada se informada)."""
        if len(self._accounts) < 2:
            QMessageBox.information(
                self,
                "Transferência indisponível",
                "Você precisa de pelo menos duas contas para realizar uma transferência.",
            )
            return

        dialog = TransferDialog(self._accounts, parent=self)

        # Pré-seleciona a conta de origem se o usuário clicou em "Transferir" em um card
        if source_account is not None:
            src_id = source_account.get("id")
            combo  = dialog._from_combo
            for i in range(combo.count()):
                if combo.itemData(i) == src_id:
                    combo.setCurrentIndex(i)
                    # Ajusta destino para não ser igual à origem
                    dest = dialog._to_combo
                    for j in range(dest.count()):
                        if dest.itemData(j) != src_id:
                            dest.setCurrentIndex(j)
                            break
                    break

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        p = dialog.get_params()
        self._start_transfer(p["from_account_id"], p["to_account_id"],
                             p["amount"], p["date"], p["description"])

    def _start_transfer(
        self,
        from_id: int,
        to_id: int,
        amount: float,
        transfer_date: str,
        description: str,
    ) -> None:
        if self._transfer_worker and self._transfer_worker.isRunning():
            return
        self._transfer_worker = TransferWorker(
            self._client, from_id, to_id, amount, transfer_date, description
        )
        self._transfer_worker.done.connect(self._on_transfer_done)
        self._transfer_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro na transferência", msg)
        )
        self._transfer_worker.start()

    def _on_transfer_done(self) -> None:
        app_signals.data_changed.emit()
        self.load_data()
        QMessageBox.information(self, "Transferência realizada", "Transferência registrada com sucesso.")


# ======================================================================
# _WrapLayout — layout que quebra cards em linhas automaticamente
# ======================================================================


class _WrapLayout:
    """
    Pseudo-layout que organiza widgets em linhas horizontais e quebra
    automaticamente ao atingir a largura disponível.

    Implementado como gerador de QHBoxLayouts dentro de um QVBoxLayout
    no widget pai.
    """

    _CARD_WIDTH = 280
    _MIN_COLS   = 1

    def __init__(self, parent: QWidget, h_spacing: int = 16, v_spacing: int = 16) -> None:
        self._parent     = parent
        self._h_spacing  = h_spacing
        self._v_spacing  = v_spacing
        self._widgets: list[QWidget] = []

        self._vbox = QVBoxLayout(parent)
        self._vbox.setContentsMargins(0, 0, 0, 0)
        self._vbox.setSpacing(v_spacing)
        self._rows: list[QHBoxLayout] = []

    def addWidget(self, widget: QWidget) -> None:
        self._widgets.append(widget)
        self._rebuild()

    def takeAt(self, index: int) -> Any:
        if index < len(self._widgets):
            w = self._widgets.pop(index)
            return _FakeLayoutItem(w)
        return _FakeLayoutItem(None)

    def count(self) -> int:
        return len(self._widgets)

    def _rebuild(self) -> None:
        # Passo 1: desparentar os cards rastreados ANTES de remover as linhas.
        # Se omitido, quando o Python coleta as linhas (sem referência após
        # setParent(None)), o Qt destrói os widgets-filho (os cards), causando
        # RuntimeError ao tentar reutilizá-los.
        for widget in self._widgets:
            widget.setParent(None)  # type: ignore[arg-type]

        # Passo 2: remover e agendar exclusão dos containers de linha
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            if w := item.widget():
                w.deleteLater()   # linha já não tem cards filhos → safe

        self._rows.clear()

        # Calcula colunas disponíveis com base na largura do parent
        available_w = self._parent.width() or 900
        cols = max(
            self._MIN_COLS,
            (available_w + self._h_spacing) // (self._CARD_WIDTH + self._h_spacing),
        )

        # Distribui cards nas linhas
        for i, w in enumerate(self._widgets):
            if i % cols == 0:
                row_w = QWidget()
                row_l = QHBoxLayout(row_w)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(self._h_spacing)
                row_l.setAlignment(Qt.AlignmentFlag.AlignLeft)
                self._rows.append(row_l)
                self._vbox.addWidget(row_w)
            self._rows[-1].addWidget(w)

        # Preenche a última linha com stretch
        if self._rows:
            self._rows[-1].addStretch()


class _FakeLayoutItem:
    """Shim para compatibilidade com a interface de takeAt do _WrapLayout."""
    def __init__(self, widget: QWidget | None) -> None:
        self._w = widget
    def widget(self) -> QWidget | None:
        return self._w


# ======================================================================
# Utilitários
# ======================================================================


def _fmt_brl(value: float) -> str:
    try:
        formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        prefix = "−R$ " if value < 0 else "R$ "
        return f"{prefix}{formatted}"
    except (TypeError, ValueError):
        return "—"
