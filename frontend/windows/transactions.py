"""
Página de Lançamentos — registro e visualização de receitas e despesas.

Layout:
  ┌──────────────────────────────────────────────────────────────┐
  │  [Busca…]  [Tipo ▾]  [Mês ▾]          [+ Novo Lançamento]   │  ← toolbar
  ├──────────────────────────────────────────────────────────────┤
  │  Data  │  Descrição  │  Categoria  │  Conta  │  Tipo  │ Valor│  ← tabela
  │  …     │  …          │  …          │  …      │  …     │  …  │
  ├──────────────────────────────────────────────────────────────┤
  │  Receitas: R$ X.XXX  │  Despesas: R$ X.XXX  │  Saldo: R$ X  │  ← rodapé
  └──────────────────────────────────────────────────────────────┘

Threading:
  TransactionsWorker busca /transactions e /accounts em background.
  SaveTransactionWorker executa o POST /transactions em background.
  Ambos emitem sinais Qt de volta para a thread principal.

Filtros:
  - Busca por texto: client-side, filtra a coluna Descrição
  - Tipo: client-side, filtra por Receita / Despesa / Transferência
  - Mês: server-side, passa start_date/end_date para a API
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any

from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal
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
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError


# ======================================================================
# Workers — operações de rede em background
# ======================================================================


class TransactionsWorker(QThread):
    """
    Busca a lista de transações e as contas bancárias em paralelo lógico.

    As duas requisições são sequenciais (httpx síncrono) mas ambas rodam
    fora da thread principal, mantendo a UI responsiva.
    """

    # (lista de transações, lista de contas, lista de cartões)
    data_ready = pyqtSignal(list, list, list)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        client: ApiClient,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._start = start_date
        self._end = end_date

    def run(self) -> None:
        try:
            transactions = self._client.get_transactions(
                start_date=self._start,
                end_date=self._end,
            )
            accounts = self._client.get_accounts()
            try:
                cards = self._client.get_cards()
            except ApiError:
                cards = []
            self.data_ready.emit(transactions, accounts, cards)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class UpdateTransactionWorker(QThread):
    """Executa PUT /transactions/{id} em background."""

    updated = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, tx_id: int, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._tx_id = tx_id
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.update_transaction(self._tx_id, self._payload)
            self.updated.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class SaveTransactionWorker(QThread):
    """
    Executa POST /transactions em background para não travar a UI enquanto
    a API processa o novo lançamento.
    """

    saved = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._payload = payload

    def run(self) -> None:
        try:
            self._client.create_transaction(self._payload)
            self.saved.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Diálogo de criação de lançamento
# ======================================================================


# Mapeamento exibição → valor da API para tipos de transação
_TYPE_LABELS = {
    "Receita": "receita",
    "Despesa": "despesa",
    "Transferência": "transferencia",
}

# Mapeamento exibição → valor da API para natureza de despesa
_NATURE_LABELS = {
    "Essencial":     "essential",
    "Supérfluo":     "discretionary",
    "Investimento":  "investment",
    "Transferência": "transfer",
}
_NATURE_DISPLAY = {v: k for k, v in _NATURE_LABELS.items()}

# Cores de badge por natureza (bg_dark, fg_text)
_NATURE_BADGE_COLORS: dict[str, tuple[str, str]] = {
    "essential":     ("#1A3A2A", "#00C896"),
    "discretionary": ("#3A2800", "#FFB347"),
    "investment":    ("#1A2A3A", "#4A9EFF"),
    "transfer":      ("#2A2D3E", "#8B90A7"),
}

# Auto-seleção de natureza pela categoria
_CAT_TO_NATURE: dict[str, str] = {
    "moradia":        "essential",
    "supermercado":   "essential",
    "saude":          "essential",
    "transporte":     "essential",
    "educacao":       "essential",
    "restaurante":    "discretionary",
    "entretenimento": "discretionary",
    "compras":        "discretionary",
    "viagem":         "discretionary",
    "investimento":   "investment",
}

# Mapeamento exibição → valor da API para categorias
_CATEGORY_LABELS = {
    "Salário": "salario",
    "Dividendos": "dividendos",
    "Moradia": "moradia",
    "Supermercado": "supermercado",
    "Transporte": "transporte",
    "Saúde": "saude",
    "Educação": "educacao",
    "Restaurante": "restaurante",
    "Entretenimento": "entretenimento",
    "Compras": "compras",
    "Viagem": "viagem",
    "Cartão de Crédito": "cartao_credito",
    "Empréstimo": "emprestimo",
    "Investimento": "investimento",
    "Outros": "outros",
}


class NewTransactionDialog(QDialog):
    """
    Formulário modal para registrar um novo lançamento financeiro.

    Recebe a lista de contas para popular o combo de conta — assim evitamos
    uma chamada HTTP adicional dentro do diálogo.

    Retorna os dados coletados via `get_payload()` após exec() == Accepted.
    """

    def __init__(self, accounts: list[dict], cards: list[dict] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Novo Lançamento")
        self.setMinimumWidth(480)
        self._accounts = accounts
        self._cards    = cards or []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("Novo Lançamento")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Descrição
        self._desc = QLineEdit()
        self._desc.setPlaceholderText("Ex: Salário, Mercado, Conta de luz…")
        form.addRow("Descrição *", self._desc)

        # Valor
        self._amount = QDoubleSpinBox()
        self._amount.setRange(0.01, 9_999_999.99)
        self._amount.setDecimals(2)
        self._amount.setPrefix("R$ ")
        self._amount.setSingleStep(10.0)
        form.addRow("Valor *", self._amount)

        # Data
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(
            # QDateEdit usa QDate (year, month, day)
            __import__("PyQt6.QtCore", fromlist=["QDate"]).QDate.currentDate()
        )
        self._date_edit.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Data *", self._date_edit)

        # Tipo
        self._type_combo = QComboBox()
        for label in _TYPE_LABELS:
            self._type_combo.addItem(label)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Tipo *", self._type_combo)

        # Categoria
        self._cat_combo = QComboBox()
        for label in _CATEGORY_LABELS:
            self._cat_combo.addItem(label)
        # Pré-seleciona "Outros" para não confundir
        idx = list(_CATEGORY_LABELS.keys()).index("Outros")
        self._cat_combo.setCurrentIndex(idx)
        self._cat_combo.currentIndexChanged.connect(self._on_category_changed)
        form.addRow("Categoria", self._cat_combo)

        # Natureza (visível apenas para despesas)
        self._nature_label = QLabel("Natureza")
        self._nature_combo = QComboBox()
        for label in ["Essencial", "Supérfluo", "Investimento"]:
            self._nature_combo.addItem(label)
        self._nature_row_label = self._nature_label
        form.addRow(self._nature_label, self._nature_combo)

        # Conta bancária (opcional — transações de cartão não precisam)
        self._account_combo = QComboBox()
        self._account_combo.addItem("— Nenhuma —", userData=None)
        for acc in self._accounts:
            label = f"{acc['name']} ({acc['bank_name']})"
            self._account_combo.addItem(label, userData=acc["id"])
        form.addRow("Conta", self._account_combo)

        # Linha de crédito / Conta (visível apenas para despesas e transferências)
        self._credit_line_label = QLabel("Linha de crédito")
        self._credit_line_combo = QComboBox()
        self._credit_line_combo.addItem("— Nenhuma —", userData=None)
        # Contas no combo de crédito
        for acc in self._accounts:
            label = f"{acc['name']} ({acc['bank_name']}) — Conta"
            self._credit_line_combo.addItem(label, userData=("account", acc["id"]))
        # Cartões no combo de crédito
        for card in self._cards:
            label = f"{card['name']} - *{card.get('last_four_digits','????')} — Cartão"
            self._credit_line_combo.addItem(label, userData=("card", card["id"], card))
        self._credit_line_combo.currentIndexChanged.connect(self._on_credit_line_changed)
        form.addRow(self._credit_line_label, self._credit_line_combo)

        # Limite disponível (mostra quando cartão selecionado)
        self._limit_label = QLabel("")
        self._limit_label.setStyleSheet("color: #4A9EFF; font-size: 11px;")
        self._limit_label.hide()
        form.addRow("", self._limit_label)

        # Observações
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("Observações opcionais…")
        self._notes.setMaximumHeight(80)
        form.addRow("Observações", self._notes)

        layout.addLayout(form)

        # Estado inicial: campos condicionais baseados no tipo padrão (Receita)
        self._on_type_changed()

        # Botões padrão: Cancelar e Salvar
        # QDialogButtonBox gera botões com labels corretos para o idioma do OS;
        # usamos Save/Cancel que o Qt traduz como "Salvar"/"Cancelar" em pt_BR.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        # Estiliza o botão primário de salvar
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setProperty("class", "success")
            save_btn.style().unpolish(save_btn)
            save_btn.style().polish(save_btn)

        layout.addWidget(buttons)

    def _on_type_changed(self) -> None:
        """Mostra campo Natureza apenas para despesas; linha de crédito para despesas/transferências."""
        tx_type = self._type_combo.currentText()
        is_expense  = tx_type == "Despesa"
        needs_credit = tx_type in ("Despesa", "Transferência")
        self._nature_label.setVisible(is_expense)
        self._nature_combo.setVisible(is_expense)
        self._credit_line_label.setVisible(needs_credit)
        self._credit_line_combo.setVisible(needs_credit)
        if not needs_credit:
            self._limit_label.hide()

    def _on_credit_line_changed(self) -> None:
        """Mostra limite disponível quando um cartão é selecionado."""
        data = self._credit_line_combo.currentData()
        if data and isinstance(data, tuple) and data[0] == "card" and len(data) >= 3:
            card = data[2]
            limit = float(card.get("credit_limit", 0))
            self._limit_label.setText(f"Limite total: R$ {limit:,.2f}".replace(",", "."))
            self._limit_label.show()
        else:
            self._limit_label.hide()

    def _on_category_changed(self) -> None:
        """Auto-seleciona natureza com base na categoria escolhida."""
        cat_api = _CATEGORY_LABELS.get(self._cat_combo.currentText(), "")
        nature  = _CAT_TO_NATURE.get(cat_api)
        if nature:
            display = _NATURE_DISPLAY.get(nature, "")
            idx = self._nature_combo.findText(display)
            if idx >= 0:
                self._nature_combo.setCurrentIndex(idx)

    def _on_accept(self) -> None:
        """Valida o formulário antes de aceitar o diálogo."""
        if not self._desc.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe a descrição do lançamento.")
            self._desc.setFocus()
            return
        if self._amount.value() <= 0:
            QMessageBox.warning(self, "Campo obrigatório", "O valor deve ser maior que zero.")
            self._amount.setFocus()
            return
        self.accept()

    def get_payload(self) -> dict[str, Any]:
        """
        Retorna o dicionário pronto para enviar ao POST /transactions.

        Converte os valores exibidos para os valores esperados pela API
        (strings de enum, datas em ISO, Decimal serializado como string).
        """
        qdate = self._date_edit.date()
        tx_date = date(qdate.year(), qdate.month(), qdate.day())

        tx_type = _TYPE_LABELS[self._type_combo.currentText()]
        payload: dict[str, Any] = {
            "description": self._desc.text().strip(),
            # Serializa como string para compatibilidade JSON com Decimal
            "amount": f"{self._amount.value():.2f}",
            "transaction_date": tx_date.isoformat(),
            "transaction_type": tx_type,
            "category": _CATEGORY_LABELS[self._cat_combo.currentText()],
            "notes": self._notes.toPlainText().strip() or None,
        }

        # Inclui natureza apenas para despesas
        if tx_type == "despesa" and self._nature_combo.isVisible():
            nature_display = self._nature_combo.currentText()
            payload["expense_nature"] = _NATURE_LABELS.get(nature_display)

        account_id = self._account_combo.currentData()
        if account_id is not None:
            payload["account_id"] = account_id

        # Linha de crédito selecionada (conta ou cartão)
        cl_data = self._credit_line_combo.currentData()
        if cl_data and isinstance(cl_data, tuple):
            if cl_data[0] == "account" and not payload.get("account_id"):
                payload["account_id"] = cl_data[1]
            elif cl_data[0] == "card":
                payload["credit_card_id"] = cl_data[1]

        return payload


class EditTransactionDialog(NewTransactionDialog):
    """Formulário modal para editar um lançamento existente (pré-preenchido)."""

    def __init__(self, tx: dict[str, Any], accounts: list[dict], cards: list[dict] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(accounts, cards, parent)
        self.setWindowTitle("Editar Lançamento")
        self._tx_id = tx["id"]
        self._prefill(tx)

    def _prefill(self, tx: dict[str, Any]) -> None:
        self._desc.setText(tx.get("description", ""))

        try:
            self._amount.setValue(float(tx.get("amount", 0)))
        except (TypeError, ValueError):
            pass

        iso = tx.get("transaction_date", "")
        if iso:
            from PyQt6.QtCore import QDate
            try:
                d = date.fromisoformat(iso)
                self._date_edit.setDate(QDate(d.year, d.month, d.day))
            except (ValueError, TypeError):
                pass

        _type_rev = {v: k for k, v in _TYPE_LABELS.items()}
        tx_type = tx.get("transaction_type", "despesa")
        display = _type_rev.get(tx_type, "Despesa")
        idx = self._type_combo.findText(display)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)

        _cat_rev = {v: k for k, v in _CATEGORY_LABELS.items()}
        cat = tx.get("category", "outros")
        cat_display = _cat_rev.get(cat, "Outros")
        idx = self._cat_combo.findText(cat_display)
        if idx >= 0:
            self._cat_combo.setCurrentIndex(idx)

        account_id = tx.get("account_id")
        if account_id is not None:
            for i in range(self._account_combo.count()):
                if self._account_combo.itemData(i) == account_id:
                    self._account_combo.setCurrentIndex(i)
                    break

        notes = tx.get("notes") or ""
        self._notes.setPlainText(notes)

        nature = tx.get("expense_nature")
        if nature:
            display = _NATURE_DISPLAY.get(nature, "")
            idx = self._nature_combo.findText(display)
            if idx >= 0:
                self._nature_combo.setCurrentIndex(idx)


# ======================================================================
# Página principal de Lançamentos
# ======================================================================

# Mapeamento valor da API → label exibido na tabela
_TYPE_DISPLAY = {"receita": "Receita", "despesa": "Despesa", "transferencia": "Transferência"}
_CAT_DISPLAY = {v: k for k, v in _CATEGORY_LABELS.items()}

# Colunas da tabela (índices usados em todas as referências)
_COL_DATE = 0
_COL_DESC = 1
_COL_CAT = 2
_COL_ACCOUNT = 3
_COL_TYPE = 4
_COL_AMOUNT = 5
_COL_ACTIONS = 6

# Colunas da tabela de dívidas
_DEBT_COL_NAME = 0
_DEBT_COL_INST = 1
_DEBT_COL_TYPE = 2
_DEBT_COL_REMAINING = 3
_DEBT_COL_INSTALLMENT = 4
_DEBT_COL_RATE = 5
_DEBT_COL_PARCELAS = 6
_DEBT_COL_ACTIONS = 7

_DEBT_TYPE_LABELS = {
    "Financiamento":       "financiamento",
    "Empréstimo Pessoal":  "emprestimo_pessoal",
    "Cartão Rotativo":     "cartao_rotativo",
    "Cheque Especial":     "cheque_especial",
    "Outro":               "outro",
}
_DEBT_TYPE_DISPLAY = {v: k for k, v in _DEBT_TYPE_LABELS.items()}


# ======================================================================
# Workers de dívidas
# ======================================================================


class SaveDebtWorker(QThread):
    """Salva uma nova dívida via POST /debts."""

    saved          = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client  = client
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.create_debt(self._payload)
            self.saved.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class UpdateDebtWorker(QThread):
    """Atualiza uma dívida via PUT /debts/{id}."""

    updated        = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, debt_id: int, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client  = client
        self._debt_id = debt_id
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.update_debt(self._debt_id, self._payload)
            self.updated.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class DeleteDebtWorker(QThread):
    """Exclui (desativa) uma dívida via DELETE /debts/{id}."""

    done           = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, debt_id: int) -> None:
        super().__init__()
        self._client  = client
        self._debt_id = debt_id

    def run(self) -> None:
        try:
            self._client.delete_debt(self._debt_id)
            self.done.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Diálogo de dívidas
# ======================================================================


class NewDebtDialog(QDialog):
    """Formulário modal para cadastrar ou editar uma dívida/financiamento."""

    def __init__(self, debt: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit_mode = debt is not None
        self.setWindowTitle("Editar Dívida" if self._edit_mode else "Nova Dívida")
        self.setMinimumWidth(500)
        self._build_ui()
        if debt:
            self._prefill(debt)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._debt_name = QLineEdit()
        self._debt_name.setPlaceholderText("Ex: Financiamento Carro, Empréstimo Banco X")
        form.addRow("Nome *", self._debt_name)

        self._institution = QLineEdit()
        self._institution.setPlaceholderText("Ex: Bradesco, Nubank, Santander")
        form.addRow("Instituição *", self._institution)

        self._debt_type = QComboBox()
        for label in _DEBT_TYPE_LABELS:
            self._debt_type.addItem(label)
        form.addRow("Tipo *", self._debt_type)

        self._total_amount = QDoubleSpinBox()
        self._total_amount.setRange(0.01, 99_999_999.99)
        self._total_amount.setDecimals(2)
        self._total_amount.setPrefix("R$ ")
        self._total_amount.setValue(0.01)
        form.addRow("Valor total original *", self._total_amount)

        self._remaining_amount = QDoubleSpinBox()
        self._remaining_amount.setRange(0.0, 99_999_999.99)
        self._remaining_amount.setDecimals(2)
        self._remaining_amount.setPrefix("R$ ")
        self._remaining_amount.setValue(0.0)
        form.addRow("Saldo devedor atual *", self._remaining_amount)

        self._interest_rate = QDoubleSpinBox()
        self._interest_rate.setRange(0.01, 100.0)
        self._interest_rate.setDecimals(4)
        self._interest_rate.setSuffix("% a.m.")
        self._interest_rate.setValue(1.0)
        form.addRow("Taxa de juros mensal *", self._interest_rate)

        self._installment_amount = QDoubleSpinBox()
        self._installment_amount.setRange(0.01, 99_999_999.99)
        self._installment_amount.setDecimals(2)
        self._installment_amount.setPrefix("R$ ")
        self._installment_amount.setValue(0.01)
        form.addRow("Valor da parcela *", self._installment_amount)

        self._total_installments = QSpinBox()
        self._total_installments.setRange(1, 600)
        self._total_installments.setValue(12)
        form.addRow("Total de parcelas *", self._total_installments)

        self._paid_installments = QSpinBox()
        self._paid_installments.setRange(0, 600)
        self._paid_installments.setValue(0)
        form.addRow("Parcelas já pagas", self._paid_installments)

        self._start_date = QDateEdit()
        self._start_date.setCalendarPopup(True)
        self._start_date.setDisplayFormat("dd/MM/yyyy")
        self._start_date.setDate(QDate.currentDate())
        form.addRow("Data de início *", self._start_date)

        self._due_day = QSpinBox()
        self._due_day.setRange(1, 28)
        self._due_day.setValue(10)
        form.addRow("Dia de vencimento *", self._due_day)

        self._amortization = QComboBox()
        self._amortization.addItem("PRICE (parcelas fixas)")
        self._amortization.addItem("SAC (amortização constante) — em breve")
        form.addRow("Sistema de amortização", self._amortization)

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
        if not self._debt_name.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe o nome da dívida.")
            self._debt_name.setFocus()
            return
        if not self._institution.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe a instituição financeira.")
            self._institution.setFocus()
            return
        self.accept()

    def get_payload(self) -> dict[str, Any]:
        sd = self._start_date.date()
        return {
            "name":                self._debt_name.text().strip(),
            "institution":         self._institution.text().strip(),
            "debt_type":           _DEBT_TYPE_LABELS[self._debt_type.currentText()],
            "total_amount":        f"{self._total_amount.value():.2f}",
            "remaining_amount":    f"{self._remaining_amount.value():.2f}",
            "interest_rate":       f"{self._interest_rate.value():.4f}",
            "installment_amount":  f"{self._installment_amount.value():.2f}",
            "total_installments":  self._total_installments.value(),
            "paid_installments":   self._paid_installments.value(),
            "start_date":          date(sd.year(), sd.month(), sd.day()).isoformat(),
            "due_day":             self._due_day.value(),
        }

    def get_update_payload(self) -> dict[str, Any]:
        """Retorna apenas campos editáveis para PUT /debts/{id}."""
        return {
            "name":               self._debt_name.text().strip(),
            "institution":        self._institution.text().strip(),
            "remaining_amount":   f"{self._remaining_amount.value():.2f}",
            "interest_rate":      f"{self._interest_rate.value():.4f}",
            "installment_amount": f"{self._installment_amount.value():.2f}",
            "paid_installments":  self._paid_installments.value(),
            "due_day":            self._due_day.value(),
        }

    def _prefill(self, debt: dict[str, Any]) -> None:
        self._debt_name.setText(debt.get("name", ""))
        self._institution.setText(debt.get("institution", ""))

        dt = debt.get("debt_type", "financiamento")
        display = _DEBT_TYPE_DISPLAY.get(dt, "Financiamento")
        idx = self._debt_type.findText(display)
        if idx >= 0:
            self._debt_type.setCurrentIndex(idx)
        self._debt_type.setEnabled(False)

        self._total_amount.setValue(float(debt.get("total_amount", 0)))
        self._remaining_amount.setValue(float(debt.get("remaining_amount", 0)))
        self._interest_rate.setValue(float(debt.get("interest_rate", 1)))
        self._installment_amount.setValue(float(debt.get("installment_amount", 0)))
        self._total_installments.setValue(int(debt.get("total_installments", 1)))
        self._total_installments.setEnabled(False)
        self._paid_installments.setValue(int(debt.get("paid_installments", 0)))
        self._due_day.setValue(int(debt.get("due_day", 10)))

        iso = debt.get("start_date", "")
        if iso:
            try:
                d = date.fromisoformat(str(iso)[:10])
                self._start_date.setDate(QDate(d.year, d.month, d.day))
            except (ValueError, TypeError):
                pass
        self._start_date.setEnabled(False)


class TransactionsPage(QWidget):
    """
    Página completa de lançamentos com tabela, filtros e rodapé de totais.

    Ciclo de vida:
      __init__ → _build_ui → load_data (busca mês atual)
      Filtros de texto/tipo → _apply_filters (client-side, sem nova requisição)
      Filtro de mês → load_data (nova requisição ao servidor)
      Botão "Novo Lançamento" → NewTransactionDialog → SaveTransactionWorker → load_data
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = ApiClient()
        self._worker: TransactionsWorker | None = None
        self._save_worker: SaveTransactionWorker | None = None
        self._update_worker: UpdateTransactionWorker | None = None
        self._save_debt_worker: SaveDebtWorker | None = None
        self._update_debt_worker: UpdateDebtWorker | None = None
        self._delete_debt_worker: DeleteDebtWorker | None = None

        self._all_transactions: list[dict] = []
        self._filtered_transactions: list[dict] = []
        self._accounts: list[dict] = []
        self._cards: list[dict] = []
        self._debts: list[dict] = []

        self._build_ui()
        self.load_data()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tabs = QTabWidget()
        tabs.addTab(self._build_transactions_tab(), "Lançamentos")
        tabs.addTab(self._build_debts_tab(), "Dívidas e Financiamentos")
        tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(tabs)

        # --- Rodapé de totais (fora do scroll — sempre visível) ---
        outer.addWidget(self._build_footer())

    def _build_transactions_tab(self) -> QWidget:
        """Conteúdo da aba Lançamentos (lógica original)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("dashboardContent")
        main = QVBoxLayout(content)
        main.setContentsMargins(32, 24, 32, 24)
        main.setSpacing(16)

        main.addLayout(self._build_toolbar())

        self._loading_label = QLabel("Carregando lançamentos…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._loading_label)

        self._table = self._build_table()
        self._table.setVisible(False)
        main.addWidget(self._table)
        main.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_debts_tab(self) -> QWidget:
        """Conteúdo da aba Dívidas e Financiamentos."""
        page = QWidget()
        page.setObjectName("dashboardContent")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)
        new_debt_btn = QPushButton("+ Nova Dívida")
        new_debt_btn.setProperty("class", "primary")
        new_debt_btn.style().unpolish(new_debt_btn)
        new_debt_btn.style().polish(new_debt_btn)
        new_debt_btn.clicked.connect(self._open_new_debt_dialog)
        toolbar.addStretch()
        toolbar.addWidget(new_debt_btn)
        outer.addLayout(toolbar)

        self._debt_loading_label = QLabel("Carregando dívidas…")
        self._debt_loading_label.setObjectName("loadingLabel")
        self._debt_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._debt_loading_label.setVisible(False)
        outer.addWidget(self._debt_loading_label)

        self._debt_table = self._build_debt_table()
        outer.addWidget(self._debt_table)
        outer.addStretch()
        return page

    def _build_debt_table(self) -> QTableWidget:
        headers = [
            "Nome", "Instituição", "Tipo", "Saldo Devedor",
            "Parcela", "Taxa Mensal", "Parc. Restantes", "Ações",
        ]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_DEBT_COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_DEBT_COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_DEBT_COL_ACTIONS, 80)
        return table

    def _build_toolbar(self) -> QHBoxLayout:
        """
        Barra de filtros + botão de ação.

        Separamos filtros client-side (busca, tipo) de server-side (mês)
        para evitar requisições desnecessárias a cada tecla digitada.
        """
        bar = QHBoxLayout()
        bar.setSpacing(12)

        # Campo de busca por texto — filtra client-side ao digitar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar por descrição…")
        self._search.setFixedWidth(240)
        self._search.textChanged.connect(self._apply_filters)
        bar.addWidget(self._search)

        # Filtro de tipo — client-side
        self._type_filter = QComboBox()
        self._type_filter.addItems(["Todos os tipos", "Receita", "Despesa", "Transferência"])
        self._type_filter.currentIndexChanged.connect(self._apply_filters)
        bar.addWidget(self._type_filter)

        # Filtro de mês — dispara nova requisição ao mudar
        self._month_filter = QComboBox()
        self._month_filter.setMinimumWidth(140)
        self._populate_month_combo()
        # connect após populat para evitar requisição na inicialização
        self._month_filter.currentIndexChanged.connect(self._on_month_changed)
        bar.addWidget(self._month_filter)

        # Filtro de natureza — client-side
        self._nature_filter = QComboBox()
        self._nature_filter.addItems(
            ["Todas as naturezas", "Essencial", "Supérfluo", "Investimento"]
        )
        self._nature_filter.currentIndexChanged.connect(self._apply_filters)
        bar.addWidget(self._nature_filter)

        bar.addStretch()

        # Botão de ação principal
        new_btn = QPushButton("+ Novo Lançamento")
        new_btn.setProperty("class", "primary")
        new_btn.style().unpolish(new_btn)
        new_btn.style().polish(new_btn)
        new_btn.clicked.connect(self._open_new_dialog)
        bar.addWidget(new_btn)

        return bar

    def _populate_month_combo(self) -> None:
        """
        Preenche o combo de mês com os últimos 12 meses + opção "Todos".

        Usa addItem com userData=(start_date, end_date) para facilitar
        a extração do período na _on_month_changed sem precisar parsear strings.
        """
        today = date.today()
        self._month_filter.addItem("Todos os meses", userData=(None, None))
        self._month_filter.addItem("Mês atual", userData=_month_range(today))

        # Meses anteriores em ordem decrescente (mais recente primeiro)
        for delta in range(1, 12):
            # Calcula mês anterior sem usar timedelta (evita complexidade de dias)
            month = today.month - delta
            year = today.year
            while month <= 0:
                month += 12
                year -= 1
            label = _month_label(year, month)
            self._month_filter.addItem(label, userData=_month_range(date(year, month, 1)))

        # Seleciona "Mês atual" como padrão (índice 1)
        self._month_filter.setCurrentIndex(1)

    def _build_table(self) -> QTableWidget:
        """
        Tabela de transações com colunas fixas.

        setEditTriggers(NoEditTriggers) → somente leitura.
        setAlternatingRowColors → facilita leitura de linhas longas.
        """
        headers = ["Data", "Descrição", "Categoria", "Conta", "Tipo", "Valor", "Ações"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_DESC, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_COL_ACTIONS, 50)

        return table

    def _build_footer(self) -> QFrame:
        """
        Rodapé com totais do período exibido.

        Os valores são calculados a partir das linhas visíveis da tabela
        (após filtros client-side) e atualizados em _refresh_totals.
        """
        footer = QFrame()
        footer.setObjectName("summaryCard")  # reutiliza estilo de card
        footer.setFixedHeight(56)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 8, 24, 8)
        layout.setSpacing(32)

        def _total_label(title: str, color: str) -> QLabel:
            lbl = QLabel(f"{title}: —")
            lbl.setStyleSheet(f"color: {color}; font-weight: 600; font-size: 13px;")
            return lbl

        self._total_income = _total_label("Receitas", "#00C896")
        self._total_expense = _total_label("Despesas", "#FF6B6B")
        self._total_balance = _total_label("Saldo", "#4A9EFF")

        layout.addWidget(self._total_income)
        layout.addWidget(QLabel("|"))
        layout.addWidget(self._total_expense)
        layout.addWidget(QLabel("|"))
        layout.addWidget(self._total_balance)
        layout.addStretch()

        return footer

    # ------------------------------------------------------------------
    # Carregamento de dados
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        """Dispara o worker para buscar transações do período selecionado."""
        if self._worker and self._worker.isRunning():
            return

        start, end = self._month_filter.currentData() or (None, None)

        self._table.setVisible(False)
        self._loading_label.setText("Carregando lançamentos…")
        self._loading_label.setVisible(True)

        self._worker = TransactionsWorker(self._client, start, end)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_data_ready(self, transactions: list[dict], accounts: list[dict], cards: list[dict]) -> None:
        self._accounts = accounts
        self._cards    = cards

        # Constrói mapa id→nome para exibir nome da conta na tabela
        self._account_map: dict[int, str] = {
            acc["id"]: acc["name"] for acc in accounts
        }

        self._all_transactions = transactions
        self._loading_label.setVisible(False)
        self._table.setVisible(True)
        self._apply_filters()

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._table.setVisible(False)

    def _on_month_changed(self) -> None:
        """Mês mudou → nova requisição ao servidor."""
        self.load_data()

    def _apply_filters(self) -> None:
        """
        Aplica filtros client-side (texto e tipo) sobre _all_transactions.

        Client-side porque seria custoso refazer a requisição a cada tecla
        digitada no campo de busca.
        """
        search_text   = self._search.text().strip().lower()
        type_filter   = self._type_filter.currentText()
        nature_filter = self._nature_filter.currentText()

        filtered = []
        for tx in self._all_transactions:
            # Filtro de texto: busca na descrição (case-insensitive)
            if search_text and search_text not in tx.get("description", "").lower():
                continue
            # Filtro de tipo: "Todos os tipos" não filtra
            if type_filter != "Todos os tipos":
                api_type = _TYPE_LABELS.get(type_filter, "")
                if tx.get("transaction_type") != api_type:
                    continue
            # Filtro de natureza: "Todas as naturezas" não filtra
            if nature_filter != "Todas as naturezas":
                api_nature = _NATURE_LABELS.get(nature_filter, "")
                if tx.get("expense_nature") != api_nature:
                    continue
            filtered.append(tx)

        self._filtered_transactions = filtered
        self._populate_table(filtered)
        self._refresh_totals(filtered)

    def _populate_table(self, transactions: list[dict]) -> None:
        """Preenche a tabela com as transações filtradas."""
        self._table.setRowCount(len(transactions))

        for row, tx in enumerate(transactions):
            tx_type = tx.get("transaction_type", "")
            amount = float(tx.get("amount", 0))
            # Receitas em verde, despesas em vermelho, transferências em azul
            amount_color = (
                "#00C896" if tx_type == "receita"
                else "#FF6B6B" if tx_type == "despesa"
                else "#4A9EFF"
            )

            expense_nature = tx.get("expense_nature")
            cat_text = _CAT_DISPLAY.get(tx.get("category", ""), tx.get("category", ""))

            cells = [
                (_fmt_date(tx.get("transaction_date", "")), "#E8EAED"),
                (tx.get("description", ""), "#E8EAED"),
                (cat_text, "#8B90A7"),
                (self._account_map.get(tx.get("account_id"), "—"), "#8B90A7"),
                (_TYPE_DISPLAY.get(tx_type, tx_type), "#8B90A7"),
                (_fmt_brl(amount if tx_type == "receita" else -amount if tx_type == "despesa" else amount), amount_color),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                if col == _COL_CAT and expense_nature and tx_type == "despesa":
                    bg_dark, fg = _NATURE_BADGE_COLORS.get(expense_nature, ("#2A2D3E", "#8B90A7"))
                    item.setBackground(QColor(bg_dark))
                    item.setForeground(QColor(fg))
                if col == _COL_AMOUNT:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._table.setItem(row, col, item)

            edit_btn = QPushButton("✏️")
            edit_btn.setToolTip("Editar lançamento")
            edit_btn.clicked.connect(lambda _, t=tx: self._open_edit_dialog(t))
            self._table.setCellWidget(row, _COL_ACTIONS, edit_btn)

    def _refresh_totals(self, transactions: list[dict]) -> None:
        """Recalcula e exibe os totais de receitas, despesas e saldo."""
        income = sum(
            float(tx["amount"]) for tx in transactions
            if tx.get("transaction_type") == "receita"
        )
        expense = sum(
            float(tx["amount"]) for tx in transactions
            if tx.get("transaction_type") == "despesa"
        )
        balance = income - expense
        balance_color = "#00C896" if balance >= 0 else "#FF6B6B"

        self._total_income.setText(f"Receitas: {_fmt_brl(income)}")
        self._total_expense.setText(f"Despesas: {_fmt_brl(expense)}")
        self._total_balance.setText(f"Saldo: {_fmt_brl(balance)}")
        self._total_balance.setStyleSheet(
            f"color: {balance_color}; font-weight: 600; font-size: 13px;"
        )

    # ------------------------------------------------------------------
    # Diálogo de edição
    # ------------------------------------------------------------------

    def _open_edit_dialog(self, tx: dict) -> None:
        dialog = EditTransactionDialog(tx, self._accounts, self._cards, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        self._start_update(tx["id"], payload)

    def _start_update(self, tx_id: int, payload: dict) -> None:
        if self._update_worker and self._update_worker.isRunning():
            return
        self._update_worker = UpdateTransactionWorker(self._client, tx_id, payload)
        self._update_worker.updated.connect(lambda _: self.load_data())
        self._update_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao atualizar", msg)
        )
        self._update_worker.start()

    # ------------------------------------------------------------------
    # Diálogo de criação
    # ------------------------------------------------------------------

    def _open_new_dialog(self) -> None:
        """Abre o diálogo de novo lançamento. Salva via worker se confirmado."""
        dialog = NewTransactionDialog(self._accounts, self._cards, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        payload = dialog.get_payload()
        self._start_save(payload)

    def _start_save(self, payload: dict) -> None:
        """Dispara o worker de salvamento e desabilita interação durante o processo."""
        if self._save_worker and self._save_worker.isRunning():
            return

        self._save_worker = SaveTransactionWorker(self._client, payload)
        self._save_worker.saved.connect(self._on_saved)
        self._save_worker.error_occurred.connect(self._on_save_error)
        self._save_worker.start()

    def _on_saved(self) -> None:
        """Lançamento salvo com sucesso — recarrega a tabela."""
        self.load_data()

    def _on_save_error(self, message: str) -> None:
        QMessageBox.critical(self, "Erro ao salvar", message)

    # ------------------------------------------------------------------
    # Aba Dívidas: tab change + load + populate
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        if index == 1 and not self._debts:
            self._load_debts()

    def _load_debts(self) -> None:
        self._debt_loading_label.setVisible(True)
        self._debt_table.setVisible(False)
        try:
            self._debts = self._client.get_debts()
        except Exception:
            self._debts = []
        self._debt_loading_label.setVisible(False)
        self._debt_table.setVisible(True)
        self._populate_debt_table(self._debts)

    def _populate_debt_table(self, debts: list[dict]) -> None:
        self._debt_table.setRowCount(len(debts))
        for row, debt in enumerate(debts):
            remaining = int(debt.get("total_installments", 1)) - int(debt.get("paid_installments", 0))
            cells = [
                debt.get("name", ""),
                debt.get("institution", ""),
                _DEBT_TYPE_DISPLAY.get(debt.get("debt_type", ""), debt.get("debt_type", "")),
                _fmt_brl(float(debt.get("remaining_amount", 0))),
                _fmt_brl(float(debt.get("installment_amount", 0))),
                f"{float(debt.get('interest_rate', 0)):.2f}% a.m.",
                str(remaining),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor("#E8EAED" if col in (0, 1) else "#8B90A7"))
                if col in (_DEBT_COL_REMAINING, _DEBT_COL_INSTALLMENT):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._debt_table.setItem(row, col, item)

            # Ações: editar + excluir
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            actions_layout.setSpacing(4)

            edit_btn = QPushButton("✏")
            edit_btn.setFixedWidth(28)
            edit_btn.setToolTip("Editar dívida")
            edit_btn.clicked.connect(lambda _, d=debt: self._open_edit_debt_dialog(d))

            del_btn = QPushButton("✕")
            del_btn.setFixedWidth(28)
            del_btn.setToolTip("Excluir dívida")
            del_btn.setStyleSheet("QPushButton { color: #FF6B6B; }")
            del_btn.clicked.connect(lambda _, d=debt: self._delete_debt(d))

            actions_layout.addWidget(edit_btn)
            actions_layout.addWidget(del_btn)
            self._debt_table.setCellWidget(row, _DEBT_COL_ACTIONS, actions_widget)

    # ------------------------------------------------------------------
    # Diálogos de dívidas
    # ------------------------------------------------------------------

    def _open_new_debt_dialog(self) -> None:
        dialog = NewDebtDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        if self._save_debt_worker and self._save_debt_worker.isRunning():
            return
        self._save_debt_worker = SaveDebtWorker(self._client, payload)
        self._save_debt_worker.saved.connect(lambda _: self._load_debts())
        self._save_debt_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao salvar dívida", msg)
        )
        self._save_debt_worker.start()

    def _open_edit_debt_dialog(self, debt: dict) -> None:
        dialog = NewDebtDialog(debt=debt, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_update_payload()
        if self._update_debt_worker and self._update_debt_worker.isRunning():
            return
        self._update_debt_worker = UpdateDebtWorker(self._client, debt["id"], payload)
        self._update_debt_worker.updated.connect(lambda _: self._load_debts())
        self._update_debt_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao atualizar dívida", msg)
        )
        self._update_debt_worker.start()

    def _delete_debt(self, debt: dict) -> None:
        resp = QMessageBox.question(
            self,
            "Confirmar exclusão",
            f"Deseja excluir a dívida «{debt.get('name', '')}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        if self._delete_debt_worker and self._delete_debt_worker.isRunning():
            return
        self._delete_debt_worker = DeleteDebtWorker(self._client, debt["id"])
        self._delete_debt_worker.done.connect(self._load_debts)
        self._delete_debt_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao excluir dívida", msg)
        )
        self._delete_debt_worker.start()


# ======================================================================
# Utilitários
# ======================================================================


def _month_range(ref: date) -> tuple[date, date]:
    """Retorna (primeiro_dia, último_dia) do mês de ref."""
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    return date(ref.year, ref.month, 1), date(ref.year, ref.month, last_day)


def _month_label(year: int, month: int) -> str:
    """Ex: 2026-04 → 'Abr/2026'"""
    months_pt = [
        "", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
        "Jul", "Ago", "Set", "Out", "Nov", "Dez",
    ]
    return f"{months_pt[month]}/{year}"


def _fmt_date(iso: str) -> str:
    """Converte '2026-05-19' → '19/05/2026'."""
    try:
        d = date.fromisoformat(iso)
        return d.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso


def _fmt_brl(value: float) -> str:
    """Formata como moeda brasileira: R$ 1.234,56"""
    try:
        formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        prefix = "-R$ " if value < 0 else "R$ "
        return f"{prefix}{formatted}"
    except (TypeError, ValueError):
        return "—"
