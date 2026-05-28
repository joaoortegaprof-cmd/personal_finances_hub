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

import matplotlib
matplotlib.use("qtagg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

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
from frontend.components.colors import (
    COLOR_ASSET      as _GREEN,
    COLOR_EXPENSE    as _RED,
    COLOR_INVESTMENT as _BLUE,
    COLOR_WARNING    as _ORANGE,
    COLOR_NEUTRAL    as _WHITE,
    COLOR_BALANCE    as _LIGHT,
    COLOR_MUTED      as _MUTED,
    COLOR_ASSET_RGB, COLOR_EXPENSE_RGB, COLOR_INVESTMENT_RGB,
)
from frontend.components.icons import icon as _svg_icon, category_icon as _cat_icon
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
    "essential":     ("#1A3A2A", _GREEN),
    "discretionary": ("#3A2800", _ORANGE),
    "investment":    ("#1A2A3A", _BLUE),
    "transfer":      ("#2A2D3E", _MUTED),
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
    "income":     {"icon": "💰", "label": "Receita",      "color": _GREEN,
                   "desc": "Salários, rendimentos e outras entradas"},
    "debit":      {"icon": "💸", "label": "Débito",       "color": _RED,
                   "desc": "Despesas pagas com conta ou dinheiro"},
    "credit":     {"icon": "💳", "label": "Crédito",      "color": _ORANGE,
                   "desc": "Compras realizadas no cartão de crédito"},
    "investment": {"icon": "📈", "label": "Investimento", "color": _BLUE,
                   "desc": "Aportes em ativos e aplicações financeiras"},
    "invoice":    {"icon": "🧾", "label": "Pag. Fatura",  "color": _MUTED,
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
        preselect_account_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Novo Lançamento")
        self.setMinimumSize(520, 460)
        self._accounts = accounts
        self._cards    = cards or []
        self._tx_type: str | None = None
        self._preselect_account_id = preselect_account_id
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
        # Pré-seleciona conta quando iniciado a partir de um card de conta
        if self._preselect_account_id is not None:
            for i in range(self._account_combo.count()):
                if self._account_combo.itemData(i) == self._preselect_account_id:
                    self._account_combo.setCurrentIndex(i)
                    break

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

# Colunas da tabela (nova ordem: descrição-com-ícone | conta | data | natureza | valor | ações)
_COL_DESC    = 0   # widget: ícone SVG de categoria + texto da descrição
_COL_ACCOUNT = 1
_COL_DATE    = 2
_COL_NATURE  = 3   # widget: badge colorido de natureza / tipo
_COL_AMOUNT  = 4
_COL_ACTIONS = 5   # widget: edit + delete

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
# Workers de recorrentes
# ======================================================================

_PERIODICITY_LABELS = {
    "monthly":    "Mensal",
    "weekly":     "Semanal",
    "biweekly":   "Quinzenal",
    "bimonthly":  "Bimestral",
    "quarterly":  "Trimestral",
    "semiannual": "Semestral",
    "annual":     "Anual",
}
_PERIODICITY_VALUES = {v: k for k, v in _PERIODICITY_LABELS.items()}

_REC_COL_NAME       = 0
_REC_COL_CATEGORY   = 1
_REC_COL_AMOUNT     = 2
_REC_COL_PERIOD     = 3
_REC_COL_NEXT_DUE   = 4
_REC_COL_MONTHLY_EQ = 5
_REC_COL_ACTIONS    = 6

_MONTHLY_FACTOR = {
    "weekly": 4.33, "biweekly": 2.17, "monthly": 1.0,
    "bimonthly": 0.5, "quarterly": 0.33, "semiannual": 0.17, "annual": 0.08,
}


class LoadRecurringWorker(QThread):
    done           = pyqtSignal(list, dict)   # (expenses, summary)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            expenses = self._client._get("/recurring-expenses")
            summary  = self._client._get("/recurring-expenses/summary")
            self.done.emit(expenses, summary)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class SaveRecurringWorker(QThread):
    saved          = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, payload: dict) -> None:
        super().__init__()
        self._client  = client
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client._post("/recurring-expenses", self._payload)
            self.saved.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class UpdateRecurringWorker(QThread):
    updated        = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, expense_id: int, payload: dict) -> None:
        super().__init__()
        self._client     = client
        self._expense_id = expense_id
        self._payload    = payload

    def run(self) -> None:
        try:
            result = self._client._put(f"/recurring-expenses/{self._expense_id}", self._payload)
            self.updated.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


class DeleteRecurringWorker(QThread):
    done           = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, expense_id: int) -> None:
        super().__init__()
        self._client     = client
        self._expense_id = expense_id

    def run(self) -> None:
        try:
            self._client._delete(f"/recurring-expenses/{self._expense_id}")
            self.done.emit()
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Worker de fluxo de caixa
# ======================================================================

class CashflowWorker(QThread):
    done           = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, months: int, mode: str) -> None:
        super().__init__()
        self._client = client
        self._months = months
        self._mode   = mode

    def run(self) -> None:
        try:
            result = self._client._get(
                "/cashflow/projection",
                params={"months": self._months, "mode": self._mode},
            )
            self.done.emit(result)
        except ApiError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Erro inesperado: {exc}")


# ======================================================================
# Canvas de fluxo de caixa
# ======================================================================

class CashflowCanvas(FigureCanvas):
    """
    Gráfico combinado de fluxo de caixa:
      - Barras empilhadas (verde=receita / vermelho=despesa) por período
      - Linha de saldo acumulado no eixo secundário
    """

    _BG   = "#1A1D2E"
    _GRID = "#2A2D3E"

    def __init__(self) -> None:
        self._fig = Figure(figsize=(8, 3.2), facecolor=self._BG)
        super().__init__(self._fig)
        self.setMinimumHeight(220)
        self._ax  = self._fig.add_subplot(111)
        self._ax2 = self._ax.twinx()
        self._style_axes()

    def _style_axes(self) -> None:
        for ax in (self._ax, self._ax2):
            ax.set_facecolor(self._BG)
            ax.tick_params(colors=_MUTED, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(self._GRID)
        self._ax.grid(axis="y", color=self._GRID, linewidth=0.5, linestyle="--")
        self._fig.tight_layout(pad=1.2)

    def plot(self, periods: list[dict]) -> None:
        self._ax.cla()
        self._ax2.cla()
        self._style_axes()

        if not periods:
            self._fig.canvas.draw_idle()
            return

        labels   = [p["label"]           for p in periods]
        income   = [p["income"]           for p in periods]
        expenses = [p["expenses"]         for p in periods]
        running  = [p["running_balance"]  for p in periods]

        x = np.arange(len(labels))
        w = 0.4

        self._ax.bar(x - w / 2, income,   width=w, color=COLOR_ASSET_RGB,      alpha=0.85, label="Receita")
        self._ax.bar(x + w / 2, expenses, width=w, color=COLOR_EXPENSE_RGB,    alpha=0.85, label="Despesa")
        self._ax2.plot(x, running, color=COLOR_INVESTMENT_RGB, linewidth=2, marker="o",
                       markersize=4, label="Saldo acum.")

        self._ax.set_xticks(x)
        self._ax.set_xticklabels(labels, rotation=30, ha="right", color=_MUTED, fontsize=7)
        self._ax.set_ylabel("R$", color=_MUTED, fontsize=8)
        self._ax2.set_ylabel("Saldo", color=_BLUE, fontsize=8)
        self._ax2.tick_params(colors=_BLUE)

        # Legenda combinada
        h1, l1 = self._ax.get_legend_handles_labels()
        h2, l2 = self._ax2.get_legend_handles_labels()
        self._ax.legend(
            h1 + h2, l1 + l2,
            facecolor="#1A1D2E", edgecolor="#2A2D3E",
            labelcolor=_LIGHT, fontsize=7, loc="upper right",
        )

        self._fig.tight_layout(pad=1.2)
        self._fig.canvas.draw_idle()


# ======================================================================
# Diálogo de recorrentes
# ======================================================================

class RecurringExpenseDialog(QDialog):
    """Formulário para cadastrar ou editar um gasto recorrente."""

    _CATEGORIES = [
        "moradia", "supermercado", "transporte", "saude", "educacao",
        "entretenimento", "assinatura", "seguros", "financeiro", "outros",
    ]

    def __init__(self, expense: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self._edit_mode = expense is not None
        self.setWindowTitle("Editar Recorrente" if self._edit_mode else "Nova Recorrente")
        self.setMinimumWidth(460)
        self._build_ui()
        if expense:
            self._prefill(expense)

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Ex: Netflix, Aluguel, Plano de Saúde")
        form.addRow("Nome:", self._name)

        self._category = QComboBox()
        self._category.addItems([c.capitalize() for c in self._CATEGORIES])
        form.addRow("Categoria:", self._category)

        self._amount = QDoubleSpinBox()
        self._amount.setRange(0.01, 1_000_000)
        self._amount.setDecimals(2)
        self._amount.setPrefix("R$ ")
        self._amount.setSingleStep(10)
        self._amount.setGroupSeparatorShown(True)
        form.addRow("Valor:", self._amount)

        self._periodicity = QComboBox()
        self._periodicity.addItems(list(_PERIODICITY_LABELS.values()))
        self._periodicity.setCurrentText("Mensal")
        form.addRow("Periodicidade:", self._periodicity)

        self._next_due = QDateEdit()
        self._next_due.setCalendarPopup(True)
        self._next_due.setDisplayFormat("dd/MM/yyyy")
        from PyQt6.QtCore import QDate
        self._next_due.setDate(QDate.currentDate())
        form.addRow("Próximo vencimento:", self._next_due)

        self._notes = QLineEdit()
        self._notes.setPlaceholderText("Observações (opcional)")
        form.addRow("Notas:", self._notes)

        lay.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _validate(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "Campo obrigatório", "Informe o nome do gasto.")
            return
        if self._amount.value() <= 0:
            QMessageBox.warning(self, "Valor inválido", "O valor deve ser maior que zero.")
            return
        self.accept()

    def _prefill(self, expense: dict) -> None:
        self._name.setText(expense.get("name", ""))
        cat = expense.get("category", "outros").lower()
        idx = self._CATEGORIES.index(cat) if cat in self._CATEGORIES else 0
        self._category.setCurrentIndex(idx)
        self._amount.setValue(float(expense.get("amount", 0)))
        period_val = expense.get("periodicity", "monthly")
        period_lbl = _PERIODICITY_LABELS.get(period_val, "Mensal")
        self._periodicity.setCurrentText(period_lbl)
        due = expense.get("next_due_date", "")
        if due:
            from PyQt6.QtCore import QDate
            self._next_due.setDate(QDate.fromString(due[:10], "yyyy-MM-dd"))
        self._notes.setText(expense.get("notes") or "")

    def get_payload(self) -> dict:
        period_lbl = self._periodicity.currentText()
        period_val = _PERIODICITY_VALUES.get(period_lbl, "monthly")
        due = self._next_due.date().toString("yyyy-MM-dd")
        return {
            "name":          self._name.text().strip(),
            "category":      self._CATEGORIES[self._category.currentIndex()],
            "amount":        round(self._amount.value(), 2),
            "periodicity":   period_val,
            "next_due_date": due,
            "notes":         self._notes.text().strip() or None,
        }


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

        self._load_recurring_worker: LoadRecurringWorker | None = None
        self._save_recurring_worker: SaveRecurringWorker | None = None
        self._update_recurring_worker: UpdateRecurringWorker | None = None
        self._delete_recurring_worker: DeleteRecurringWorker | None = None
        self._recurring_loaded: bool = False

        self._cashflow_worker: CashflowWorker | None = None
        self._cashflow_loaded: bool = False

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
        tabs.addTab(self._build_recurring_tab(), "Recorrentes")
        tabs.addTab(self._build_cashflow_tab(), "Fluxo de Caixa")
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

    def _build_recurring_tab(self) -> QWidget:
        """Aba de gastos recorrentes: assinaturas, contratos e mensalidades."""
        page = QWidget()
        page.setObjectName("dashboardContent")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(12)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)
        new_rec_btn = QPushButton("+ Nova Recorrente")
        new_rec_btn.setProperty("class", "primary")
        new_rec_btn.style().unpolish(new_rec_btn)
        new_rec_btn.style().polish(new_rec_btn)
        new_rec_btn.clicked.connect(self._open_new_recurring_dialog)
        toolbar.addStretch()
        toolbar.addWidget(new_rec_btn)
        outer.addLayout(toolbar)

        # Cards de resumo
        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        self._rec_monthly_card = self._make_summary_card("Comprometido/mês", "—", _RED)
        self._rec_annual_card  = self._make_summary_card("Comprometido/ano", "—", _ORANGE)
        self._rec_count_card   = self._make_summary_card("Ativos",           "—", _BLUE)
        for c in (self._rec_monthly_card, self._rec_annual_card, self._rec_count_card):
            summary_row.addWidget(c)
        outer.addLayout(summary_row)

        # Alertas de vencimento
        self._rec_alert_widget = QWidget()
        self._rec_alert_widget.setVisible(False)
        alert_lay = QVBoxLayout(self._rec_alert_widget)
        alert_lay.setContentsMargins(0, 0, 0, 0)
        alert_lay.setSpacing(4)
        self._rec_alert_label = QLabel()
        self._rec_alert_label.setStyleSheet(
            "color: #FFB347; font-size: 12px; font-weight: 600; "
            "background: #261A00; border-radius: 6px; padding: 8px 12px;"
        )
        self._rec_alert_label.setWordWrap(True)
        alert_lay.addWidget(self._rec_alert_label)
        outer.addWidget(self._rec_alert_widget)

        # Loading
        self._rec_loading_label = QLabel("Carregando recorrentes…")
        self._rec_loading_label.setObjectName("loadingLabel")
        self._rec_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_loading_label.setVisible(False)
        outer.addWidget(self._rec_loading_label)

        # Tabela
        self._rec_table = self._build_recurring_table()
        outer.addWidget(self._rec_table)
        outer.addStretch()
        return page

    def _make_summary_card(self, title: str, value: str, color: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("simCard")
        frame.setStyleSheet(
            f"QFrame#simCard {{ background: #222640; border-radius: 8px; "
            f"border-left: 3px solid {color}; padding: 4px; }}"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("color: #C8CAD8; font-size: 11px;")
        v = QLabel(value)
        v.setObjectName("cardValue")
        v.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: 700;")
        lay.addWidget(t)
        lay.addWidget(v)
        return frame

    def _build_recurring_table(self) -> QTableWidget:
        headers = ["Nome", "Categoria", "Valor", "Periodicidade",
                   "Próx. Vencimento", "Equiv./mês", "Ações"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_REC_COL_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_REC_COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_REC_COL_ACTIONS, 76)
        return table

    # ------------------------------------------------------------------
    # Aba Fluxo de Caixa
    # ------------------------------------------------------------------

    def _build_cashflow_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("dashboardContent")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(32, 20, 32, 20)
        outer.setSpacing(12)

        # ── Controles ────────────────────────────────────────────────
        controls = QHBoxLayout()
        controls.setSpacing(12)

        mode_lbl = QLabel("Agrupamento:")
        mode_lbl.setStyleSheet("color: #C8CAD8; font-size: 12px;")
        self._cf_mode_combo = QComboBox()
        self._cf_mode_combo.addItem("Semanal", "weekly")
        self._cf_mode_combo.addItem("Mensal",  "monthly")

        months_lbl = QLabel("Horizonte:")
        months_lbl.setStyleSheet("color: #C8CAD8; font-size: 12px;")
        self._cf_months_spin = QSpinBox()
        self._cf_months_spin.setRange(1, 12)
        self._cf_months_spin.setValue(3)
        self._cf_months_spin.setSuffix(" meses")

        self._cf_update_btn = QPushButton("  Atualizar")
        self._cf_update_btn.setIcon(_svg_icon("refresh", _LIGHT, 14))
        self._cf_update_btn.clicked.connect(self._reload_cashflow)

        controls.addWidget(mode_lbl)
        controls.addWidget(self._cf_mode_combo)
        controls.addWidget(months_lbl)
        controls.addWidget(self._cf_months_spin)
        controls.addStretch()
        controls.addWidget(self._cf_update_btn)
        outer.addLayout(controls)

        # ── Cards de totais ─────────────────────────────────────────
        totals_row = QHBoxLayout()
        totals_row.setSpacing(12)
        self._cf_income_card  = self._make_summary_card("Receita projetada",  "—", _GREEN)
        self._cf_expense_card = self._make_summary_card("Despesa projetada",  "—", _RED)
        self._cf_balance_card = self._make_summary_card("Saldo do período",   "—", _BLUE)
        for c in (self._cf_income_card, self._cf_expense_card, self._cf_balance_card):
            totals_row.addWidget(c)
        outer.addLayout(totals_row)

        # ── Gráfico ──────────────────────────────────────────────────
        self._cf_canvas = CashflowCanvas()
        outer.addWidget(self._cf_canvas)

        # ── Tabela de eventos ────────────────────────────────────────
        outer.addWidget(_section_lbl("Eventos projetados"))
        self._cf_events_table = self._build_cashflow_events_table()
        outer.addWidget(self._cf_events_table)

        self._cf_loading_label = QLabel("Carregando projeção…")
        self._cf_loading_label.setObjectName("loadingLabel")
        self._cf_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cf_loading_label.setVisible(False)
        outer.addWidget(self._cf_loading_label)

        return page

    def _build_cashflow_events_table(self) -> QTableWidget:
        headers = ["Data", "Descrição", "Categoria", "Tipo", "Valor"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setMaximumHeight(280)
        return table

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

        # Botão de importação de extrato
        import_btn = QPushButton("↑ Importar extrato")
        import_btn.setToolTip("Importar lançamentos de arquivo OFX ou CSV")
        import_btn.clicked.connect(self._open_import_wizard)
        bar.addWidget(import_btn)

        # Botão de ação principal
        new_btn = QPushButton(" Novo Lançamento")
        new_btn.setIcon(_svg_icon("add_circle", "#FFFFFF", 16))
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

        self._prev_month_btn = QPushButton()
        self._prev_month_btn.setIcon(_svg_icon("arrow_left", _LIGHT, 16))
        self._prev_month_btn.setFixedSize(32, 32)
        self._prev_month_btn.setToolTip("Mês anterior")
        self._prev_month_btn.clicked.connect(self._on_prev_month)

        self._month_nav_label = QLabel()
        self._month_nav_label.setMinimumWidth(180)
        self._month_nav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._month_nav_label.setStyleSheet(
            "color: #E8EAED; font-weight: 700; font-size: 15px; background: transparent;"
        )

        self._next_month_btn = QPushButton()
        self._next_month_btn.setIcon(_svg_icon("arrow_right", _LIGHT, 16))
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
        Tabela de transações redesenhada com tema claro.

        Colunas: Descrição (ícone+texto) | Conta | Data | Natureza (badge) | Valor | Ações
        - Tema claro (#F8F9FC) contrastando com a sidebar/header escuros.
        - Linhas com fundo sutil colorido pelo tipo de transação.
        - Altura de linha 44 px para melhor legibilidade.
        """
        headers = ["Descrição", "Conta", "Data", "Natureza", "Valor", ""]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(False)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)

        # Altura de linha generosa para o conteúdo dos widgets de célula
        table.verticalHeader().setDefaultSectionSize(44)

        # Tema claro — substitui o QSS global escuro apenas neste widget
        table.setStyleSheet("""
            QTableWidget {
                background-color: #F8F9FC;
                alternate-background-color: #F0F2F8;
                color: #1A1D2E;
                border: none;
                border-radius: 0px;
                font-size: 13px;
                outline: none;
            }
            QHeaderView::section {
                background-color: #ECEEF6;
                color: #6B7080;
                font-weight: 600;
                font-size: 11px;
                letter-spacing: 0.4px;
                border: none;
                border-bottom: 2px solid #D5D9EE;
                padding: 6px 12px;
            }
            QTableWidget::item {
                padding: 4px 12px;
                border-bottom: 1px solid #EAECF4;
                color: #1A1D2E;
            }
            QTableWidget::item:selected {
                background-color: #DDE8FF;
                color: #1A1D2E;
            }
            QTableWidget::item:hover {
                background-color: #EEF2FF;
            }
            QScrollBar:vertical {
                background: #F0F2F8;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #C5CAE9;
                border-radius: 4px;
                min-height: 30px;
            }
        """)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_DESC, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_NATURE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(_COL_AMOUNT, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(_COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_COL_NATURE, 130)
        table.setColumnWidth(_COL_AMOUNT, 120)
        table.setColumnWidth(_COL_ACTIONS, 76)

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

        self._total_income = _total_label("Receitas", _GREEN)
        self._total_expense = _total_label("Despesas", _RED)
        self._total_balance = _total_label("Saldo", _BLUE)

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

        # Cores do valor por tipo (sobre tema claro)
        _AMOUNT_COLORS = {
            "income":     _GREEN,       # COLOR_ASSET — verde
            "debit":      _RED,         # COLOR_EXPENSE — vermelho
            "credit":     _ORANGE,      # COLOR_WARNING — laranja
            "investment": _BLUE,        # COLOR_INVESTMENT — azul
            "invoice":    _MUTED,       # COLOR_MUTED — cinza
        }

        # Fundo sutil por tipo (tema claro — tints pastel)
        _ROW_BG_LIGHT = {
            "income":     "#EDF9F4",
            "debit":      "#FFF0F0",
            "credit":     "#FFF6EC",
            "investment": "#EEF4FF",
            "invoice":    "#F5F5FB",
        }

        # Cores de ícone de categoria por tipo de transação
        _CAT_ICON_COLOR = {
            "income":     _GREEN,
            "debit":      _RED,
            "credit":     _ORANGE,
            "investment": _BLUE,
            "invoice":    _MUTED,
        }

        # Badge de natureza/tipo (bg, fg) — sobre tema claro
        _NATURE_BADGE_LIGHT: dict[str, tuple[str, str]] = {
            "essential":     ("#D1F5E9", "#0B6B48"),
            "discretionary": ("#FFECD6", "#8A4200"),
            "investment":    ("#D8EAFF", "#0A4CA0"),
            "transfer":      ("#E8E9F5", "#4A4E6A"),
        }

        # Badge do tipo quando sem natureza
        _TYPE_BADGE_LIGHT: dict[str, tuple[str, str]] = {
            "income":     ("#D1F5E9", "#0B6B48"),
            "debit":      ("#FFE0E0", "#8A1C1C"),
            "credit":     ("#FFECD6", "#8A4200"),
            "investment": ("#D8EAFF", "#0A4CA0"),
            "invoice":    ("#E8E9F5", "#4A4E6A"),
        }

        for row, tx in enumerate(transactions):
            tx_type = tx.get("transaction_type", "")
            amount = float(tx.get("amount", 0))
            amount_color = _AMOUNT_COLORS.get(tx_type, "#1A1D2E")
            row_bg_color = _ROW_BG_LIGHT.get(tx_type, "#F8F9FC")

            expense_nature = tx.get("expense_nature")
            category = tx.get("category", "outros")
            cat_icon_color = _CAT_ICON_COLOR.get(tx_type, "#6B7080")

            # ── Col 0: ícone de categoria + descrição ─────────────────────
            desc_widget = QWidget()
            desc_widget.setStyleSheet(f"background-color: {row_bg_color};")
            desc_lay = QHBoxLayout(desc_widget)
            desc_lay.setContentsMargins(10, 4, 8, 4)
            desc_lay.setSpacing(8)

            icon_lbl = QLabel()
            icon_lbl.setPixmap(_cat_icon(category, cat_icon_color, 16).pixmap(16, 16))
            icon_lbl.setFixedSize(20, 20)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl.setStyleSheet("background: transparent;")

            desc_lbl = QLabel(tx.get("description", ""))
            desc_lbl.setStyleSheet(
                "color: #1A1D2E; font-weight: 500; font-size: 13px; background: transparent;"
            )
            desc_lbl.setToolTip(tx.get("description", ""))

            desc_lay.addWidget(icon_lbl)
            desc_lay.addWidget(desc_lbl, 1)
            self._table.setCellWidget(row, _COL_DESC, desc_widget)

            # ── Col 1: Conta ──────────────────────────────────────────────
            acc_text = self._account_map.get(tx.get("account_id"), "—")
            acc_item = QTableWidgetItem(acc_text)
            acc_item.setForeground(QColor("#5A5E78"))
            acc_item.setBackground(QColor(row_bg_color))
            self._table.setItem(row, _COL_ACCOUNT, acc_item)

            # ── Col 2: Data ───────────────────────────────────────────────
            date_item = QTableWidgetItem(_fmt_date(tx.get("transaction_date", "")))
            date_item.setForeground(QColor("#6B7080"))
            date_item.setBackground(QColor(row_bg_color))
            self._table.setItem(row, _COL_DATE, date_item)

            # ── Col 3: Badge de natureza (ou tipo quando sem natureza) ────
            if expense_nature and tx_type in ("debit", "credit", "invoice"):
                badge_bg, badge_fg = _NATURE_BADGE_LIGHT.get(
                    expense_nature, ("#E8E9F5", "#4A4E6A")
                )
                badge_text = _NATURE_DISPLAY.get(expense_nature, expense_nature)
            else:
                badge_bg, badge_fg = _TYPE_BADGE_LIGHT.get(tx_type, ("#E8E9F5", "#4A4E6A"))
                badge_text = _TYPE_DISPLAY.get(tx_type, tx_type)

            badge_widget = QWidget()
            badge_widget.setStyleSheet(f"background-color: {row_bg_color};")
            badge_lay = QHBoxLayout(badge_widget)
            badge_lay.setContentsMargins(6, 4, 6, 4)
            badge_lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            badge_lbl = QLabel(badge_text)
            badge_lbl.setStyleSheet(
                f"background-color: {badge_bg}; color: {badge_fg};"
                " border-radius: 8px; padding: 2px 8px;"
                " font-size: 10px; font-weight: 600; letter-spacing: 0.2px;"
            )
            badge_lbl.setFixedHeight(20)
            badge_lay.addWidget(badge_lbl)
            self._table.setCellWidget(row, _COL_NATURE, badge_widget)

            # ── Col 4: Valor (alinhado à direita, colorido) ───────────────
            sign = "" if tx_type == "income" else "−"
            amount_item = QTableWidgetItem(f"{sign}{_fmt_brl(amount)}")
            amount_item.setForeground(QColor(amount_color))
            amount_item.setBackground(QColor(row_bg_color))
            amount_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # Negrito para receitas e despesas maiores
            font = amount_item.font()
            font.setWeight(600)
            amount_item.setFont(font)
            self._table.setItem(row, _COL_AMOUNT, amount_item)

            # ── Col 5: Ações (editar + excluir) ───────────────────────────
            actions_w = QWidget()
            actions_w.setStyleSheet(f"background-color: {row_bg_color};")
            act_lay = QHBoxLayout(actions_w)
            act_lay.setContentsMargins(4, 4, 4, 4)
            act_lay.setSpacing(2)
            act_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

            edit_btn = QPushButton()
            edit_btn.setIcon(_svg_icon("edit", "#5A5E78", 14))
            edit_btn.setFixedSize(30, 30)
            edit_btn.setToolTip("Editar lançamento")
            edit_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 6px; }"
                "QPushButton:hover { background: #E0E4F5; }"
            )
            edit_btn.clicked.connect(lambda _, t=tx: self._open_edit_dialog(t))

            del_btn = QPushButton()
            del_btn.setIcon(_svg_icon("delete", "#C43030", 14))
            del_btn.setFixedSize(30, 30)
            del_btn.setToolTip("Excluir lançamento")
            del_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 6px; }"
                "QPushButton:hover { background: #FFE0E0; }"
            )
            del_btn.clicked.connect(lambda _, t=tx: self._delete_transaction(t))

            act_lay.addWidget(edit_btn)
            act_lay.addWidget(del_btn)
            self._table.setCellWidget(row, _COL_ACTIONS, actions_w)

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
        balance_color = _GREEN if balance >= 0 else _RED

        self._total_income.setText(f"Receitas: {_fmt_brl(income)}")
        self._total_expense.setText(f"Despesas: {_fmt_brl(expense)}")
        self._total_balance.setText(f"Saldo: {_fmt_brl(balance)}")
        self._total_balance.setStyleSheet(
            f"color: {balance_color}; font-weight: 600; font-size: 13px;"
        )

    # ------------------------------------------------------------------
    # Exclusão de lançamento (iniciada pelo botão delete da tabela)
    # ------------------------------------------------------------------

    def _delete_transaction(self, tx: dict) -> None:
        """Pede confirmação e exclui o lançamento via API."""
        desc = tx.get("description", "—")
        reply = QMessageBox.question(
            self,
            "Excluir lançamento",
            f"Excluir '{desc}'? Esta ação não pode ser desfeita.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._client.delete_transaction(tx["id"])
            app_signals.data_changed.emit()
            self.load_data()
        except ApiError as exc:
            QMessageBox.warning(self, "Erro", f"Não foi possível excluir: {exc}")

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
    # Wizard de importação de extrato
    # ------------------------------------------------------------------

    def _open_import_wizard(self) -> None:
        """Abre o wizard de importação de extratos OFX/CSV."""
        from frontend.windows.import_wizard import ImportWizard  # importação lazy

        if not self._accounts:
            QMessageBox.information(
                self,
                "Nenhuma conta cadastrada",
                "Cadastre ao menos uma conta em Contas antes de importar extratos.",
            )
            return

        wizard = ImportWizard(self._client, self._accounts, parent=self)
        wizard.import_completed.connect(self.load_data)
        wizard.exec()

    # ------------------------------------------------------------------
    # Diálogo de criação
    # ------------------------------------------------------------------

    def _open_new_dialog(self) -> None:
        """Abre o diálogo de novo lançamento. Salva via worker se confirmado."""
        if not self._accounts:
            QMessageBox.information(
                self,
                "Nenhuma conta cadastrada",
                "Cadastre ao menos uma conta em Contas antes de registrar lançamentos.",
            )
            return
        dialog = NewTransactionDialog(self._accounts, self._cards, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        payload = dialog.get_payload()
        self._start_save(payload)

    def open_new_for_account(self, account_id: int) -> None:
        """
        Abre o diálogo de novo lançamento com uma conta pré-selecionada.
        Chamado pela MainWindow quando o usuário clica em '+ Lançamento' em um card de conta.
        """
        dialog = NewTransactionDialog(
            self._accounts,
            self._cards,
            parent=self,
            preselect_account_id=account_id,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._start_save(dialog.get_payload())

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
        elif index == 2 and not self._recurring_loaded:
            self._load_recurring()
        elif index == 3 and not self._cashflow_loaded:
            self._load_cashflow()

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
                item.setForeground(QColor(_WHITE if col in (0, 1) else _MUTED))
                if col in (_DEBT_COL_REMAINING, _DEBT_COL_INSTALLMENT):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._debt_table.setItem(row, col, item)

            # Ações: editar + excluir
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(4)
            actions_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            edit_btn = QPushButton()
            edit_btn.setIcon(_svg_icon("edit", _LIGHT, 14))
            edit_btn.setFixedSize(32, 32)
            edit_btn.setToolTip("Editar dívida")
            edit_btn.clicked.connect(lambda _, d=debt: self._open_edit_debt_dialog(d))

            del_btn = QPushButton()
            del_btn.setIcon(_svg_icon("delete", _RED, 14))
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

    # ------------------------------------------------------------------
    # Aba Recorrentes: load + populate + CRUD
    # ------------------------------------------------------------------

    def _load_recurring(self) -> None:
        if self._load_recurring_worker and self._load_recurring_worker.isRunning():
            return
        self._rec_loading_label.setVisible(True)
        self._rec_table.setVisible(False)
        self._load_recurring_worker = LoadRecurringWorker(self._client)
        self._load_recurring_worker.done.connect(self._on_recurring_loaded)
        self._load_recurring_worker.error_occurred.connect(
            lambda msg: (
                self._rec_loading_label.setText(f"Erro: {msg}"),
                self._rec_loading_label.setVisible(True),
            )
        )
        self._load_recurring_worker.start()

    def _on_recurring_loaded(self, expenses: list[dict], summary: dict) -> None:
        self._recurring_loaded = True
        self._rec_loading_label.setVisible(False)
        self._rec_table.setVisible(True)
        self._populate_recurring_table(expenses)
        self._update_recurring_summary(summary)

    def _update_recurring_summary(self, summary: dict) -> None:
        monthly = summary.get("monthly_total", 0)
        annual  = summary.get("annual_total", 0)
        count   = summary.get("active_count", 0)

        def _brl(v):
            return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        # Atualiza labels dos cards (segundo QLabel de cada frame)
        for card, val in [
            (self._rec_monthly_card, _brl(monthly)),
            (self._rec_annual_card,  _brl(annual)),
            (self._rec_count_card,   str(count)),
        ]:
            labels = card.findChildren(QLabel)
            if len(labels) >= 2:
                labels[1].setText(val)

        # Alertas de vencimento próximo
        upcoming = summary.get("upcoming", [])
        overdue  = summary.get("overdue", [])
        alerts: list[str] = []
        if overdue:
            names = ", ".join(e["name"] for e in overdue[:3])
            suffix = f" (+{len(overdue)-3} mais)" if len(overdue) > 3 else ""
            alerts.append(f"⚠️  Vencidos: {names}{suffix}")
        if upcoming:
            names = ", ".join(e["name"] for e in upcoming[:3])
            suffix = f" (+{len(upcoming)-3} mais)" if len(upcoming) > 3 else ""
            alerts.append(f"🔔  Vencem em 7 dias: {names}{suffix}")

        if alerts:
            self._rec_alert_label.setText("   |   ".join(alerts))
            self._rec_alert_widget.setVisible(True)
        else:
            self._rec_alert_widget.setVisible(False)

    def _populate_recurring_table(self, expenses: list[dict]) -> None:
        today = date.today()
        self._rec_table.setRowCount(len(expenses))
        for row, exp in enumerate(expenses):
            due_str    = exp.get("next_due_date", "")[:10]
            period_val = exp.get("periodicity", "monthly")
            amount     = float(exp.get("amount", 0))
            monthly_eq = amount * _MONTHLY_FACTOR.get(period_val, 1.0)

            # Cor da linha por proximidade do vencimento
            try:
                due_date = date.fromisoformat(due_str)
                days_left = (due_date - today).days
                if days_left < 0:
                    row_bg = "#291111"   # vermelho — vencido
                elif days_left <= 7:
                    row_bg = "#261A00"   # laranja — vencendo em breve
                else:
                    row_bg = ""
            except ValueError:
                days_left = 999
                row_bg = ""

            cells = [
                exp.get("name", ""),
                exp.get("category", "outros").capitalize(),
                f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                _PERIODICITY_LABELS.get(period_val, period_val),
                due_str,
                f"R$ {monthly_eq:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if row_bg:
                    item.setBackground(QColor(row_bg))
                if col == _REC_COL_AMOUNT:
                    item.setForeground(QColor(_RED))
                self._rec_table.setItem(row, col, item)

            # Botões de ação
            edit_btn, edit_container = _icon_btn("edit", "Editar")
            del_btn,  del_container  = _icon_btn("delete", "Excluir")
            edit_btn.clicked.connect(lambda _, e=exp: self._open_edit_recurring_dialog(e))
            del_btn.clicked.connect(lambda _, e=exp: self._delete_recurring(e))

            actions = QWidget()
            act_lay = QHBoxLayout(actions)
            act_lay.setContentsMargins(4, 2, 4, 2)
            act_lay.setSpacing(2)
            act_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            act_lay.addWidget(edit_btn)
            act_lay.addWidget(del_btn)
            self._rec_table.setCellWidget(row, _REC_COL_ACTIONS, actions)

    def _open_new_recurring_dialog(self) -> None:
        dialog = RecurringExpenseDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        if self._save_recurring_worker and self._save_recurring_worker.isRunning():
            return
        self._save_recurring_worker = SaveRecurringWorker(self._client, payload)
        self._save_recurring_worker.saved.connect(lambda _: self._reload_recurring())
        self._save_recurring_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao salvar", msg)
        )
        self._save_recurring_worker.start()

    def _open_edit_recurring_dialog(self, expense: dict) -> None:
        dialog = RecurringExpenseDialog(expense=expense, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        if self._update_recurring_worker and self._update_recurring_worker.isRunning():
            return
        self._update_recurring_worker = UpdateRecurringWorker(
            self._client, expense["id"], payload
        )
        self._update_recurring_worker.updated.connect(lambda _: self._reload_recurring())
        self._update_recurring_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao atualizar", msg)
        )
        self._update_recurring_worker.start()

    def _delete_recurring(self, expense: dict) -> None:
        reply = QMessageBox.question(
            self, "Confirmar exclusão",
            f"Deseja excluir «{expense.get('name', '')}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._delete_recurring_worker and self._delete_recurring_worker.isRunning():
            return
        self._delete_recurring_worker = DeleteRecurringWorker(self._client, expense["id"])
        self._delete_recurring_worker.done.connect(self._reload_recurring)
        self._delete_recurring_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao excluir", msg)
        )
        self._delete_recurring_worker.start()

    def _reload_recurring(self) -> None:
        self._recurring_loaded = False
        self._load_recurring()

    # ------------------------------------------------------------------
    # Aba Fluxo de Caixa: load + populate + reload
    # ------------------------------------------------------------------

    def _load_cashflow(self) -> None:
        if self._cashflow_worker and self._cashflow_worker.isRunning():
            return
        months = self._cf_months_spin.value()
        mode   = self._cf_mode_combo.currentData()
        self._cf_loading_label.setText("Carregando projeção…")
        self._cf_loading_label.setVisible(True)
        self._cf_canvas.setVisible(False)
        self._cf_events_table.setVisible(False)

        self._cashflow_worker = CashflowWorker(self._client, months, mode)
        self._cashflow_worker.done.connect(self._on_cashflow_loaded)
        self._cashflow_worker.error_occurred.connect(
            lambda msg: (
                self._cf_loading_label.setText(f"Erro: {msg}"),
                self._cf_canvas.setVisible(False),
            )
        )
        self._cashflow_worker.start()

    def _on_cashflow_loaded(self, data: dict) -> None:
        self._cashflow_loaded = True
        self._cf_loading_label.setVisible(False)
        self._cf_canvas.setVisible(True)
        self._cf_events_table.setVisible(True)

        periods = data.get("periods", [])
        events  = data.get("events",  [])

        # Atualiza cards de totais
        def _brl(v):
            return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        total_income   = data.get("total_income",   0)
        total_expenses = data.get("total_expenses", 0)
        balance        = total_income - total_expenses

        for card, val in [
            (self._cf_income_card,  _brl(total_income)),
            (self._cf_expense_card, _brl(total_expenses)),
            (self._cf_balance_card, _brl(balance)),
        ]:
            labels = card.findChildren(QLabel)
            if len(labels) >= 2:
                labels[1].setText(val)

        self._cf_canvas.plot(periods)
        self._populate_cashflow_events(events)

    def _populate_cashflow_events(self, events: list[dict]) -> None:
        _EVENT_LABELS = {
            "income":    "Receita",
            "recurring": "Recorrente",
            "debt":      "Dívida",
            "invoice":   "Fatura",
        }
        _EVENT_COLORS = {
            "income":    _GREEN,
            "recurring": _ORANGE,
            "debt":      _RED,
            "invoice":   _MUTED,
        }

        self._cf_events_table.setRowCount(len(events))
        for row, ev in enumerate(events):
            ev_type = ev.get("type", "")
            amount  = float(ev.get("amount", 0))
            color   = _EVENT_COLORS.get(ev_type, _LIGHT)
            cells = [
                _fmt_date(ev.get("date", "")),
                ev.get("description", ""),
                ev.get("category", ""),
                _EVENT_LABELS.get(ev_type, ev_type),
                _fmt_brl(amount),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 4:
                    item.setForeground(QColor(color))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._cf_events_table.setItem(row, col, item)

    def _reload_cashflow(self) -> None:
        self._cashflow_loaded = False
        self._load_cashflow()


# ======================================================================
# Utilitários
# ======================================================================


def _section_lbl(text: str) -> QLabel:
    """Rótulo de seção com estilo de subtítulo."""
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #8B90A7; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;")
    return lbl


def _icon_btn(icon_name: str, tooltip: str) -> tuple["QPushButton", "QWidget"]:
    """Retorna (botão, container) com botão de ícone SVG 32×32 centralizado na célula."""
    container = QWidget()
    lay = QHBoxLayout(container)
    lay.setContentsMargins(4, 2, 4, 2)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    btn = QPushButton()
    # escolhe cor do ícone conforme a ação
    _color_map = {"edit": _LIGHT, "delete": _RED, "view": _BLUE, "pay": _GREEN}
    btn.setIcon(_svg_icon(icon_name, _color_map.get(icon_name, _LIGHT), 14))
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
