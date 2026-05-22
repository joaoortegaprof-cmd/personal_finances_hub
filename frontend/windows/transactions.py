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
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.signals import app_signals


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
    "Receita":      "income",
    "Débito":       "debit",
    "Crédito":      "credit",
    "Investimento": "investment",
    "Pag. Fatura":  "invoice",
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

# Fundo sutil por tipo de transação para facilitar leitura da tabela
_ROW_BG = {
    "income":     "#0A2319",
    "debit":      "#291111",
    "credit":     "#261A0A",
    "investment": "#0A1A29",
    "invoice":    "#17181E",
}

_MONTHS_FULL = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


# Configuração visual dos 5 tipos de lançamento
_TYPE_CONFIG = {
    "income":     {"icon": "💰", "label": "Receita",      "color": "#00C896",
                   "desc": "Salários, rendimentos e outras entradas"},
    "debit":      {"icon": "💸", "label": "Débito",       "color": "#FF6B6B",
                   "desc": "Despesas pagas com conta ou dinheiro"},
    "credit":     {"icon": "💳", "label": "Crédito",      "color": "#FFB347",
                   "desc": "Compras realizadas no cartão de crédito"},
    "investment": {"icon": "📈", "label": "Investimento", "color": "#4A9EFF",
                   "desc": "Aportes em ativos e aplicações financeiras"},
    "invoice":    {"icon": "🧾", "label": "Pag. Fatura",  "color": "#8B90A7",
                   "desc": "Pagamento de fatura do cartão de crédito"},
}


class _TypeCard(QFrame):
    """Frame clicável usado na tela de seleção de tipo do wizard."""

    clicked = pyqtSignal(str)

    def __init__(self, tx_type: str, cfg: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tx_type = tx_type
        self.setObjectName("typeCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(60)
        color = cfg["color"]
        self.setStyleSheet(f"""
            QFrame#typeCard {{
                background: #1A1D2E;
                border: 1px solid #2A2D3E;
                border-left: 4px solid {color};
                border-radius: 8px;
            }}
            QFrame#typeCard:hover {{
                background: #1E2235;
            }}
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(12)

        icon_lbl = QLabel(cfg["icon"])
        icon_lbl.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        icon_lbl.setFixedWidth(28)
        row.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        name_lbl = QLabel(cfg["label"])
        name_lbl.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 13px;"
            " background: transparent; border: none;"
        )
        desc_lbl = QLabel(cfg["desc"])
        desc_lbl.setStyleSheet(
            "color: #8B90A7; font-size: 10px; background: transparent; border: none;"
        )
        text_col.addWidget(name_lbl)
        text_col.addWidget(desc_lbl)
        row.addLayout(text_col)
        row.addStretch()

        arrow = QLabel("›")
        arrow.setStyleSheet("color: #4A4D6A; font-size: 18px; background: transparent; border: none;")
        row.addWidget(arrow)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._tx_type)
        super().mousePressEvent(event)


class NewTransactionDialog(QDialog):
    """
    Diálogo em 2 etapas para registrar um novo lançamento financeiro.

    Etapa 1: 5 cartões clicáveis para escolher o tipo.
    Etapa 2: formulário específico para o tipo escolhido.
    """

    def __init__(
        self,
        accounts: list[dict],
        cards: list[dict] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Novo Lançamento")
        self.setMinimumSize(520, 460)
        self._accounts = accounts
        self._cards    = cards or []
        self._tx_type: str | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Estrutura principal
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main = QVBoxLayout(self)
        main.setContentsMargins(24, 20, 24, 20)
        main.setSpacing(16)

        self._title_lbl = QLabel("Novo Lançamento")
        self._title_lbl.setObjectName("sectionTitle")
        main.addWidget(self._title_lbl)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_type_page())   # índice 0
        self._stack.addWidget(self._build_form_page())   # índice 1
        main.addWidget(self._stack)

        # Footer com navegação
        foot = QHBoxLayout()
        self._back_btn = QPushButton("← Voltar")
        self._back_btn.clicked.connect(self._go_to_type_page)
        self._back_btn.setVisible(False)
        foot.addWidget(self._back_btn)
        foot.addStretch()

        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        foot.addWidget(cancel_btn)

        self._save_btn = QPushButton("Salvar")
        self._save_btn.setProperty("class", "success")
        self._save_btn.clicked.connect(self._on_accept)
        self._save_btn.setVisible(False)
        foot.addWidget(self._save_btn)

        main.addLayout(foot)

    # ------------------------------------------------------------------
    # Etapa 1: seleção de tipo
    # ------------------------------------------------------------------

    def _build_type_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        sub = QLabel("Selecione o tipo de lançamento:")
        sub.setStyleSheet("color: #8B90A7; font-size: 12px;")
        layout.addWidget(sub)

        for tx_type, cfg in _TYPE_CONFIG.items():
            card = _TypeCard(tx_type, cfg)
            card.clicked.connect(self._select_type)
            layout.addWidget(card)

        layout.addStretch()
        return page

    # ------------------------------------------------------------------
    # Etapa 2: formulário adaptável
    # ------------------------------------------------------------------

    def _build_form_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        form = QFormLayout(inner)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 4, 4, 4)

        # Descrição
        self._desc = QLineEdit()
        self._desc.setPlaceholderText("Ex: Salário, Mercado, Conta de luz…")
        self._desc_lbl = QLabel("Descrição *")
        form.addRow(self._desc_lbl, self._desc)

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
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Data *", self._date_edit)

        # Categoria
        self._cat_combo = QComboBox()
        for label in _CATEGORY_LABELS:
            self._cat_combo.addItem(label)
        self._cat_combo.setCurrentIndex(list(_CATEGORY_LABELS.keys()).index("Outros"))
        self._cat_combo.currentIndexChanged.connect(self._on_category_changed)
        self._cat_lbl = QLabel("Categoria")
        form.addRow(self._cat_lbl, self._cat_combo)

        # Natureza
        self._nature_combo = QComboBox()
        for label in ["Essencial", "Supérfluo", "Investimento"]:
            self._nature_combo.addItem(label)
        self._nature_lbl = QLabel("Natureza")
        form.addRow(self._nature_lbl, self._nature_combo)

        # Conta bancária
        self._account_combo = QComboBox()
        self._account_combo.addItem("— Nenhuma —", userData=None)
        for acc in self._accounts:
            self._account_combo.addItem(
                f"{acc['name']} ({acc['bank_name']})", userData=acc["id"]
            )
        self._account_lbl = QLabel("Conta *")
        form.addRow(self._account_lbl, self._account_combo)

        # Cartão de crédito
        self._card_combo = QComboBox()
        self._card_combo.addItem("— Nenhum —", userData=None)
        for card in self._cards:
            self._card_combo.addItem(
                f"{card['name']} •••• {card.get('last_four_digits', '????')}",
                userData=card["id"],
            )
        self._card_lbl = QLabel("Cartão *")
        form.addRow(self._card_lbl, self._card_combo)

        # Observações
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("Observações opcionais…")
        self._notes.setMaximumHeight(70)
        form.addRow("Observações", self._notes)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return page

    def _configure_form(self, tx_type: str) -> None:
        """Mostra/oculta campos conforme o tipo selecionado."""
        show_desc    = tx_type != "invoice"
        show_cat     = tx_type in ("income", "debit", "credit")
        show_nature  = tx_type in ("debit", "credit")
        show_account = tx_type in ("income", "debit", "investment", "invoice")
        show_card    = tx_type in ("credit", "invoice")

        account_labels = {
            "invoice":    "Conta de débito *",
            "investment": "Conta / Corretora *",
        }
        self._account_lbl.setText(account_labels.get(tx_type, "Conta *"))
        self._card_lbl.setText("Cartão *" if tx_type == "credit" else "Cartão (opcional)")

        self._desc_lbl.setVisible(show_desc)
        self._desc.setVisible(show_desc)
        self._cat_lbl.setVisible(show_cat)
        self._cat_combo.setVisible(show_cat)
        self._nature_lbl.setVisible(show_nature)
        self._nature_combo.setVisible(show_nature)
        self._account_lbl.setVisible(show_account)
        self._account_combo.setVisible(show_account)
        self._card_lbl.setVisible(show_card)
        self._card_combo.setVisible(show_card)

    # ------------------------------------------------------------------
    # Navegação entre etapas
    # ------------------------------------------------------------------

    def _select_type(self, tx_type: str) -> None:
        self._tx_type = tx_type
        cfg = _TYPE_CONFIG[tx_type]
        self._title_lbl.setText(f"Novo Lançamento — {cfg['label']}")
        self._configure_form(tx_type)
        self._stack.setCurrentIndex(1)
        self._back_btn.setVisible(True)
        self._save_btn.setVisible(True)
        self._save_btn.style().unpolish(self._save_btn)
        self._save_btn.style().polish(self._save_btn)

    def _go_to_type_page(self) -> None:
        self._stack.setCurrentIndex(0)
        self._back_btn.setVisible(False)
        self._save_btn.setVisible(False)
        self._title_lbl.setText("Novo Lançamento")
        self._tx_type = None

    # ------------------------------------------------------------------
    # Validação e payload
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        if self._tx_type is None:
            return
        if not self._desc.isHidden() and not self._desc.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe a descrição do lançamento.")
            self._desc.setFocus()
            return
        if self._amount.value() <= 0:
            QMessageBox.warning(self, "Campo obrigatório", "O valor deve ser maior que zero.")
            return
        if (not self._account_combo.isHidden()
                and self._account_combo.currentData() is None
                and self._tx_type not in ("invoice", "credit")):
            QMessageBox.warning(self, "Campo obrigatório", "Selecione uma conta.")
            return
        if self._tx_type == "credit" and self._card_combo.currentData() is None:
            QMessageBox.warning(self, "Campo obrigatório", "Selecione um cartão de crédito.")
            return
        self.accept()

    def get_payload(self) -> dict[str, Any]:
        qdate = self._date_edit.date()
        tx_date = date(qdate.year(), qdate.month(), qdate.day())
        tx_type = self._tx_type or "debit"

        payload: dict[str, Any] = {
            "description": (
                self._desc.text().strip() if not self._desc.isHidden()
                else "Pagamento de fatura"
            ),
            "amount": f"{self._amount.value():.2f}",
            "transaction_date": tx_date.isoformat(),
            "transaction_type": tx_type,
            "notes": self._notes.toPlainText().strip() or None,
        }

        if not self._cat_combo.isHidden():
            payload["category"] = _CATEGORY_LABELS[self._cat_combo.currentText()]
        elif tx_type == "investment":
            payload["category"] = "investimento"
        elif tx_type == "invoice":
            payload["category"] = "cartao_credito"
        else:
            payload["category"] = "outros"

        if not self._nature_combo.isHidden():
            nature_api = _NATURE_LABELS.get(self._nature_combo.currentText())
            if nature_api:
                payload["expense_nature"] = nature_api
        elif tx_type == "investment":
            payload["expense_nature"] = "investment"

        if not self._account_combo.isHidden():
            account_id = self._account_combo.currentData()
            if account_id is not None:
                payload["account_id"] = account_id

        if not self._card_combo.isHidden():
            card_id = self._card_combo.currentData()
            if card_id is not None:
                payload["credit_card_id"] = card_id

        return payload

    def _on_category_changed(self) -> None:
        cat_api = _CATEGORY_LABELS.get(self._cat_combo.currentText(), "")
        nature  = _CAT_TO_NATURE.get(cat_api)
        if nature:
            display = _NATURE_DISPLAY.get(nature, "")
            idx = self._nature_combo.findText(display)
            if idx >= 0:
                self._nature_combo.setCurrentIndex(idx)


class EditTransactionDialog(NewTransactionDialog):
    """Formulário de edição — pula a seleção de tipo, vai direto ao formulário."""

    def __init__(
        self,
        tx: dict[str, Any],
        accounts: list[dict],
        cards: list[dict] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(accounts, cards, parent)
        self.setWindowTitle("Editar Lançamento")
        self._tx_id = tx["id"]
        # Pula etapa 1: vai direto ao formulário com o tipo do lançamento
        self._select_type(tx.get("transaction_type", "debit"))
        self._back_btn.setVisible(False)
        self._title_lbl.setText("Editar Lançamento")
        self._prefill(tx)

    def _prefill(self, tx: dict[str, Any]) -> None:
        self._desc.setText(tx.get("description", ""))

        try:
            self._amount.setValue(float(tx.get("amount", 0)))
        except (TypeError, ValueError):
            pass

        iso = tx.get("transaction_date", "")
        if iso:
            try:
                d = date.fromisoformat(iso)
                self._date_edit.setDate(QDate(d.year, d.month, d.day))
            except (ValueError, TypeError):
                pass

        _cat_rev = {v: k for k, v in _CATEGORY_LABELS.items()}
        cat_display = _cat_rev.get(tx.get("category", "outros"), "Outros")
        idx = self._cat_combo.findText(cat_display)
        if idx >= 0:
            self._cat_combo.setCurrentIndex(idx)

        account_id = tx.get("account_id")
        if account_id is not None:
            for i in range(self._account_combo.count()):
                if self._account_combo.itemData(i) == account_id:
                    self._account_combo.setCurrentIndex(i)
                    break

        card_id = tx.get("credit_card_id")
        if card_id is not None:
            for i in range(self._card_combo.count()):
                if self._card_combo.itemData(i) == card_id:
                    self._card_combo.setCurrentIndex(i)
                    break

        self._notes.setPlainText(tx.get("notes") or "")

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
_TYPE_DISPLAY = {
    "income":     "Receita",
    "debit":      "Débito",
    "credit":     "Crédito",
    "investment": "Investimento",
    "invoice":    "Pag. Fatura",
}
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

        # Estado do navegador de meses (0 = todos os meses)
        today = date.today()
        self._nav_year:  int = today.year
        self._nav_month: int = today.month

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
        main.addWidget(self._build_month_header())

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
        table.setColumnWidth(_DEBT_COL_ACTIONS, 76)
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
        self._type_filter.addItems([
            "Todos os tipos", "Receita", "Débito", "Crédito", "Investimento", "Pag. Fatura"
        ])
        self._type_filter.currentIndexChanged.connect(self._apply_filters)
        bar.addWidget(self._type_filter)

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

    def _build_month_header(self) -> QWidget:
        """Cabeçalho de seção com navegador de meses centralizado."""
        header = QWidget()
        header.setObjectName("monthHeader")
        header.setStyleSheet(
            "QWidget#monthHeader { background: #222640; border-radius: 8px; }"
        )

        lay = QHBoxLayout(header)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)

        self._prev_month_btn = QPushButton("◀")
        self._prev_month_btn.setFixedSize(32, 32)
        self._prev_month_btn.setToolTip("Mês anterior")
        self._prev_month_btn.clicked.connect(self._on_prev_month)

        self._month_nav_label = QLabel()
        self._month_nav_label.setMinimumWidth(180)
        self._month_nav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._month_nav_label.setStyleSheet(
            "color: #E8EAED; font-weight: 700; font-size: 15px; background: transparent;"
        )

        self._next_month_btn = QPushButton("▶")
        self._next_month_btn.setFixedSize(32, 32)
        self._next_month_btn.setToolTip("Próximo mês")
        self._next_month_btn.clicked.connect(self._on_next_month)

        lay.addStretch()
        lay.addWidget(self._prev_month_btn)
        lay.addWidget(self._month_nav_label)
        lay.addWidget(self._next_month_btn)
        lay.addStretch()

        self._update_month_nav()
        return header

    def _update_month_nav(self) -> None:
        """Atualiza label e estado dos botões do navegador de meses."""
        today = date.today()
        if self._nav_month == 0:
            self._month_nav_label.setText("Todos os meses")
            self._next_month_btn.setEnabled(False)
        else:
            self._month_nav_label.setText(
                f"{_MONTHS_FULL[self._nav_month]} {self._nav_year}"
            )
            at_current = (
                self._nav_year == today.year and self._nav_month == today.month
            )
            self._next_month_btn.setEnabled(not at_current)

    def _on_prev_month(self) -> None:
        """Navega para o mês anterior (ou sai do modo 'todos')."""
        if self._nav_month == 0:
            today = date.today()
            self._nav_year, self._nav_month = today.year, today.month
        else:
            m = self._nav_month - 1
            y = self._nav_year
            if m <= 0:
                m, y = 12, y - 1
            self._nav_month, self._nav_year = m, y
        self._update_month_nav()
        self.load_data()

    def _on_next_month(self) -> None:
        """Navega para o mês seguinte (limitado ao mês atual)."""
        today = date.today()
        if self._nav_month == 0:
            return
        m = self._nav_month + 1
        y = self._nav_year
        if m > 12:
            m, y = 1, y + 1
        if y > today.year or (y == today.year and m > today.month):
            return
        self._nav_month, self._nav_year = m, y
        self._update_month_nav()
        self.load_data()

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
        table.setAlternatingRowColors(False)
        table.verticalHeader().setVisible(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_DESC, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_COL_ACTIONS, 40)

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

        if self._nav_month == 0:
            start, end = None, None
        else:
            nav_date = date(self._nav_year, self._nav_month, 1)
            start, end = _month_range(nav_date)

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

        _AMOUNT_COLORS = {
            "income":     "#00C896",
            "debit":      "#FF6B6B",
            "credit":     "#FF6B6B",
            "investment": "#4A9EFF",
            "invoice":    "#8B90A7",
        }

        for row, tx in enumerate(transactions):
            tx_type = tx.get("transaction_type", "")
            amount = float(tx.get("amount", 0))
            amount_color = _AMOUNT_COLORS.get(tx_type, "#E8EAED")
            row_bg = _ROW_BG.get(tx_type)

            expense_nature = tx.get("expense_nature")
            cat_text = _CAT_DISPLAY.get(tx.get("category", ""), tx.get("category", ""))

            cells = [
                (_fmt_date(tx.get("transaction_date", "")), "#E8EAED"),
                (tx.get("description", ""), "#E8EAED"),
                (cat_text, "#8B90A7"),
                (self._account_map.get(tx.get("account_id"), "—"), "#8B90A7"),
                (_TYPE_DISPLAY.get(tx_type, tx_type), "#8B90A7"),
                (_fmt_brl(amount if tx_type == "income" else -amount), amount_color),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                if row_bg:
                    item.setBackground(QColor(row_bg))
                if col == _COL_CAT and expense_nature and tx_type in ("debit", "credit"):
                    # badge de natureza sobrepõe o fundo da linha
                    bg_dark, fg = _NATURE_BADGE_COLORS.get(expense_nature, ("#2A2D3E", "#8B90A7"))
                    item.setBackground(QColor(bg_dark))
                    item.setForeground(QColor(fg))
                if col == _COL_AMOUNT:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._table.setItem(row, col, item)

            edit_btn, cell = _icon_btn("✏️", "Editar lançamento")
            edit_btn.clicked.connect(lambda _, t=tx: self._open_edit_dialog(t))
            self._table.setCellWidget(row, _COL_ACTIONS, cell)

    def _refresh_totals(self, transactions: list[dict]) -> None:
        """Recalcula e exibe os totais de receitas, despesas e saldo."""
        income = sum(
            float(tx["amount"]) for tx in transactions
            if tx.get("transaction_type") == "income"
        )
        expense = sum(
            float(tx["amount"]) for tx in transactions
            if tx.get("transaction_type") in ("debit", "credit", "investment")
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
        self._update_worker.updated.connect(lambda _: (app_signals.data_changed.emit(), self.load_data()))
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
        app_signals.data_changed.emit()
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
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(4)
            actions_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            edit_btn = QPushButton("✏️")
            edit_btn.setFixedSize(32, 32)
            edit_btn.setToolTip("Editar dívida")
            edit_btn.clicked.connect(lambda _, d=debt: self._open_edit_debt_dialog(d))

            del_btn = QPushButton("🗑️")
            del_btn.setFixedSize(32, 32)
            del_btn.setToolTip("Excluir dívida")
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
        self._save_debt_worker.saved.connect(lambda _: (app_signals.data_changed.emit(), self._load_debts()))
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
        self._update_debt_worker.updated.connect(lambda _: (app_signals.data_changed.emit(), self._load_debts()))
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
        self._delete_debt_worker.done.connect(lambda: (app_signals.data_changed.emit(), self._load_debts()))
        self._delete_debt_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao excluir dívida", msg)
        )
        self._delete_debt_worker.start()


# ======================================================================
# Utilitários
# ======================================================================


def _icon_btn(icon: str, tooltip: str) -> tuple["QPushButton", "QWidget"]:
    """Retorna (botão, container) com botão de ícone 32×32 centralizado na célula."""
    container = QWidget()
    lay = QHBoxLayout(container)
    lay.setContentsMargins(4, 2, 4, 2)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    btn = QPushButton(icon)
    btn.setFixedSize(32, 32)
    btn.setToolTip(tooltip)
    lay.addWidget(btn)
    return btn, container


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
