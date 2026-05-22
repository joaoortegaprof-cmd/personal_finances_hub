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
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
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
from frontend.components.signals import app_signals


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
            cards    = self._client.get_cards()
            accounts = self._client.get_accounts()
            for card in cards:
                try:
                    lim = self._client.get_card_available_limit(card["id"])
                    card["_available"] = lim.get("available", card.get("credit_limit", 0))
                except ApiError:
                    card["_available"] = card.get("credit_limit", 0)
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


class UpdateCardWorker(QThread):
    """Executa PUT /cards/{id} em background."""

    updated = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, client: ApiClient, card_id: int, payload: dict[str, Any]) -> None:
        super().__init__()
        self._client = client
        self._card_id = card_id
        self._payload = payload

    def run(self) -> None:
        try:
            result = self._client.update_card(self._card_id, self._payload)
            self.updated.emit(result)
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
_CC_NAME    = 0
_CC_BANK    = 1
_CC_LAST4   = 2
_CC_LIMIT   = 3
_CC_AVAIL   = 4
_CC_CLOSE   = 5
_CC_DUE     = 6
_CC_ACTIONS = 7

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

    _DEFAULT_COLOR = "#7B61FF"

    def __init__(self, accounts: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Novo Cartão de Crédito")
        self.setMinimumWidth(440)
        self._accounts = accounts
        self._card_color = self._DEFAULT_COLOR
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

        # Seletor de cor
        color_row = QHBoxLayout()
        color_row.setSpacing(10)
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(40, 28)
        self._color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color_btn.setToolTip("Clique para escolher a cor do cartão")
        self._color_btn.clicked.connect(self._pick_color)
        self._color_preview = QLabel(self._card_color)
        self._color_preview.setStyleSheet("color: #8B90A7; font-size: 11px;")
        color_row.addWidget(self._color_btn)
        color_row.addWidget(self._color_preview)
        color_row.addStretch()
        self._update_color_btn()
        form.addRow("Cor do cartão", color_row)

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

    def _pick_color(self) -> None:
        """Abre QColorDialog e atualiza a cor selecionada."""
        initial = QColor(self._card_color)
        color = QColorDialog.getColor(initial, self, "Escolher cor do cartão")
        if color.isValid():
            self._card_color = color.name().upper()
            self._update_color_btn()

    def _update_color_btn(self) -> None:
        """Atualiza o botão de cor com a cor atual."""
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background-color: {self._card_color}; "
            f"border: 2px solid #4A4D6A; border-radius: 4px; }}"
        )
        self._color_preview.setText(self._card_color)

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
            "card_color": self._card_color,
        }
        account_id = self._account_combo.currentData()
        if account_id is not None:
            payload["payment_account_id"] = account_id
        return payload


# ======================================================================
# Diálogo de edição de cartão
# ======================================================================


class EditCardDialog(CardDialog):
    """Formulário modal para editar um cartão existente (pré-preenchido)."""

    def __init__(self, card: dict[str, Any], accounts: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(accounts, parent)
        self.setWindowTitle("Editar Cartão de Crédito")
        self._card_id = card["id"]
        self._prefill(card)

    def _prefill(self, card: dict[str, Any]) -> None:
        self._name.setText(card.get("name", ""))
        self._bank.setText(card.get("bank_name", ""))
        self._last4.setText(card.get("last_four_digits", ""))
        try:
            self._limit.setValue(float(card.get("credit_limit", 0)))
        except (TypeError, ValueError):
            pass
        self._closing_day.setValue(card.get("closing_day", 10))
        self._due_day.setValue(card.get("due_day", 17))

        payment_acc = card.get("payment_account_id")
        if payment_acc is not None:
            for i in range(self._account_combo.count()):
                if self._account_combo.itemData(i) == payment_acc:
                    self._account_combo.setCurrentIndex(i)
                    break

        color = card.get("card_color", self._DEFAULT_COLOR)
        self._card_color = color if color else self._DEFAULT_COLOR
        self._update_color_btn()


# ======================================================================
# Widget visual de cartão de crédito
# ======================================================================


class CreditCardWidget(QFrame):
    """
    Card visual 320×180 px com gradiente, número mascarado, limites e barra de uso.
    Emite card_clicked(dict) ao ser clicado. Destaca borda quando selecionado.
    """

    card_clicked = pyqtSignal(dict)

    def __init__(self, card: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._card = card
        self._selected = False
        self.setFixedSize(320, 180)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_ui()
        self._apply_style()

    # ------------------------------------------------------------------
    # UI interna
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(4)

        # Linha 1: banco (esq) + "CRÉDITO" (dir)
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        bank_lbl = QLabel(self._card.get("bank_name", ""))
        bank_lbl.setStyleSheet(
            "color: rgba(255,255,255,220); font-size: 12px; font-weight: 700;"
            " background: transparent; letter-spacing: 1px;"
        )
        type_lbl = QLabel("CRÉDITO")
        type_lbl.setStyleSheet(
            "color: rgba(255,255,255,150); font-size: 10px; background: transparent;"
        )
        row1.addWidget(bank_lbl)
        row1.addStretch()
        row1.addWidget(type_lbl)
        layout.addLayout(row1)

        layout.addStretch()

        # Linha 2: número mascarado
        last4 = self._card.get("last_four_digits", "????")
        num_lbl = QLabel(f"•••• •••• •••• {last4}")
        num_lbl.setStyleSheet(
            "color: white; font-size: 17px; font-weight: 700;"
            " letter-spacing: 3px; background: transparent;"
        )
        num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(num_lbl)

        layout.addStretch()

        # Linha 3: nome do cartão
        name_lbl = QLabel(self._card.get("name", "").upper())
        name_lbl.setStyleSheet(
            "color: rgba(255,255,255,190); font-size: 10px; font-weight: 600;"
            " letter-spacing: 1px; background: transparent;"
        )
        layout.addWidget(name_lbl)

        # Linha 4: limites
        limit     = float(self._card.get("credit_limit", 0))
        available = float(self._card.get("_available", limit))
        used_pct  = int((limit - available) / max(limit, 1) * 100)
        avail_color = (
            "#6EFFCE" if available >= limit * 0.3
            else "#FFD06B" if available > 0
            else "#FF8080"
        )

        limits_row = QHBoxLayout()
        limits_row.setContentsMargins(0, 2, 0, 2)
        total_lbl = QLabel(f"Total {_fmt_brl(limit)}")
        total_lbl.setStyleSheet(
            "color: rgba(255,255,255,160); font-size: 9px; background: transparent;"
        )
        avail_lbl = QLabel(f"Disp. {_fmt_brl(available)}")
        avail_lbl.setStyleSheet(
            f"color: {avail_color}; font-size: 9px; font-weight: 600;"
            " background: transparent;"
        )
        avail_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        limits_row.addWidget(total_lbl)
        limits_row.addStretch()
        limits_row.addWidget(avail_lbl)
        layout.addLayout(limits_row)

        # Barra de uso do limite
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(used_pct)
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setStyleSheet("""
            QProgressBar {
                background: rgba(255,255,255,50);
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: rgba(255,255,255,200);
                border-radius: 2px;
            }
        """)
        layout.addWidget(bar)

    def _apply_style(self) -> None:
        color = self._card.get("card_color", "#7B61FF")
        try:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            darker = f"#{max(0,r-50):02X}{max(0,g-50):02X}{max(0,b-50):02X}"
        except (ValueError, IndexError):
            color, darker = "#7B61FF", "#5A44CC"

        border = "3px solid white" if self._selected else "2px solid rgba(255,255,255,30)"
        self.setStyleSheet(f"""
            CreditCardWidget {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {color}, stop:1 {darker});
                border-radius: 12px;
                border: {border};
            }}
        """)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.card_clicked.emit(self._card)
        super().mousePressEvent(event)


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
        self._update_worker: UpdateCardWorker | None = None
        self._delete_worker: DeleteCardWorker | None = None
        self._pay_worker: PayInvoiceWorker | None = None
        self._cards: list[dict] = []
        self._accounts: list[dict] = []
        self._card_widgets: list[CreditCardWidget] = []
        self._selected_card_id: int | None = None
        self._selected_card: dict | None = None
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

        # --- Header ---
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

        # --- Loading ---
        self._loading_label = QLabel("Carregando cartões…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._loading_label)

        # --- Grade de cards visuais (2 por linha) ---
        self._cards_grid_container = QWidget()
        self._cards_grid_container.setVisible(False)
        self._cards_grid = QGridLayout(self._cards_grid_container)
        self._cards_grid.setSpacing(20)
        self._cards_grid.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        main.addWidget(self._cards_grid_container)

        # --- Painel de ações do cartão selecionado ---
        self._action_panel = QFrame()
        self._action_panel.setObjectName("summaryCard")
        self._action_panel.setVisible(False)
        ap_layout = QHBoxLayout(self._action_panel)
        ap_layout.setContentsMargins(16, 10, 16, 10)
        ap_layout.setSpacing(12)

        self._selected_card_label = QLabel("")
        self._selected_card_label.setStyleSheet(
            "color: #E8EAED; font-weight: 600; font-size: 13px;"
        )
        ap_layout.addWidget(self._selected_card_label)
        ap_layout.addStretch()

        edit_btn = QPushButton("✏️  Editar")
        edit_btn.setToolTip("Editar cartão selecionado")
        edit_btn.clicked.connect(self._edit_selected_card)
        ap_layout.addWidget(edit_btn)

        del_btn = QPushButton("🗑️  Excluir")
        del_btn.setToolTip("Excluir cartão selecionado")
        del_btn.setStyleSheet("QPushButton { color: #FF6B6B; }")
        del_btn.clicked.connect(self._delete_selected_card)
        ap_layout.addWidget(del_btn)
        main.addWidget(self._action_panel)

        # --- Seção de faturas ---
        inv_label = QLabel("Faturas")
        inv_label.setObjectName("sectionTitle")
        main.addWidget(inv_label)

        self._inv_loading = QLabel("Clique em um cartão para ver as faturas.")
        self._inv_loading.setObjectName("loadingLabel")
        self._inv_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._inv_loading)

        self._inv_table = self._build_invoices_table()
        self._inv_table.setVisible(False)
        main.addWidget(self._inv_table)

        main.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

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
        self._cards_grid_container.setVisible(False)
        self._action_panel.setVisible(False)
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
        self._populate_cards_grid(cards)

    def _on_error(self, message: str) -> None:
        self._loading_label.setText(f"Erro ao carregar: {message}")
        self._cards_grid_container.setVisible(False)

    def _populate_cards_grid(self, cards: list[dict]) -> None:
        """Preenche a grade 2-por-linha com CreditCardWidget."""
        # Limpa widgets anteriores
        while self._cards_grid.count():
            item = self._cards_grid.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        self._card_widgets = []

        if not cards:
            self._cards_grid_container.setVisible(False)
            self._loading_label.setText("Nenhum cartão cadastrado.")
            self._loading_label.setVisible(True)
            return

        for idx, card in enumerate(cards):
            widget = CreditCardWidget(card)
            widget.card_clicked.connect(self._on_card_widget_clicked)
            row_idx, col_idx = divmod(idx, 2)
            self._cards_grid.addWidget(widget, row_idx, col_idx)
            self._card_widgets.append(widget)

        self._cards_grid_container.setVisible(True)

    # ------------------------------------------------------------------
    # Seleção de cartão
    # ------------------------------------------------------------------

    def _on_card_widget_clicked(self, card: dict) -> None:
        """Seleciona o cartão clicado, destaca widget, atualiza painel de ações e carrega faturas."""
        card_id = card["id"]
        for widget in self._card_widgets:
            widget.set_selected(widget._card["id"] == card_id)
        self._selected_card = card
        self._selected_card_id = card_id
        name = card.get("name", "")
        last4 = card.get("last_four_digits", "")
        self._selected_card_label.setText(f"{name}  ••••  {last4}")
        self._action_panel.setVisible(True)
        self._load_invoices(card_id)

    def _edit_selected_card(self) -> None:
        if self._selected_card is None:
            return
        self._open_edit_card_dialog(self._selected_card)

    def _delete_selected_card(self) -> None:
        if self._selected_card is None:
            return
        self._confirm_delete_card(self._selected_card)

    # ------------------------------------------------------------------
    # Dados — Faturas
    # ------------------------------------------------------------------

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
        self._save_worker.saved.connect(lambda _: (app_signals.data_changed.emit(), self.load_data()))
        self._save_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao salvar", msg)
        )
        self._save_worker.start()

    def _open_edit_card_dialog(self, card: dict) -> None:
        dialog = EditCardDialog(card, self._accounts, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        if self._update_worker and self._update_worker.isRunning():
            return
        self._update_worker = UpdateCardWorker(self._client, card["id"], payload)
        self._update_worker.updated.connect(lambda _: (app_signals.data_changed.emit(), self.load_data()))
        self._update_worker.error_occurred.connect(
            lambda msg: QMessageBox.critical(self, "Erro ao atualizar", msg)
        )
        self._update_worker.start()

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


def _fmt_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return d.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso
