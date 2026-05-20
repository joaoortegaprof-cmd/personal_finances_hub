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
    QPlainTextEdit,
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
# Workers — operações de rede em background
# ======================================================================


class TransactionsWorker(QThread):
    """
    Busca a lista de transações e as contas bancárias em paralelo lógico.

    As duas requisições são sequenciais (httpx síncrono) mas ambas rodam
    fora da thread principal, mantendo a UI responsiva.
    """

    # (lista de transações, lista de contas, datas de início/fim usadas)
    data_ready = pyqtSignal(list, list)
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
            self.data_ready.emit(transactions, accounts)
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

    def __init__(self, accounts: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Novo Lançamento")
        self.setMinimumWidth(440)
        self._accounts = accounts
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
        form.addRow("Tipo *", self._type_combo)

        # Categoria
        self._cat_combo = QComboBox()
        for label in _CATEGORY_LABELS:
            self._cat_combo.addItem(label)
        # Pré-seleciona "Outros" para não confundir
        idx = list(_CATEGORY_LABELS.keys()).index("Outros")
        self._cat_combo.setCurrentIndex(idx)
        form.addRow("Categoria", self._cat_combo)

        # Conta bancária (opcional — transações de cartão não precisam)
        self._account_combo = QComboBox()
        self._account_combo.addItem("— Nenhuma —", userData=None)
        for acc in self._accounts:
            label = f"{acc['name']} ({acc['bank_name']})"
            self._account_combo.addItem(label, userData=acc["id"])
        form.addRow("Conta", self._account_combo)

        # Observações
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("Observações opcionais…")
        self._notes.setMaximumHeight(80)
        form.addRow("Observações", self._notes)

        layout.addLayout(form)

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

        payload: dict[str, Any] = {
            "description": self._desc.text().strip(),
            # Serializa como string para compatibilidade JSON com Decimal
            "amount": f"{self._amount.value():.2f}",
            "transaction_date": tx_date.isoformat(),
            "transaction_type": _TYPE_LABELS[self._type_combo.currentText()],
            "category": _CATEGORY_LABELS[self._cat_combo.currentText()],
            "notes": self._notes.toPlainText().strip() or None,
        }

        account_id = self._account_combo.currentData()
        if account_id is not None:
            payload["account_id"] = account_id

        return payload


class EditTransactionDialog(NewTransactionDialog):
    """Formulário modal para editar um lançamento existente (pré-preenchido)."""

    def __init__(self, tx: dict[str, Any], accounts: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(accounts, parent)
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

        self._all_transactions: list[dict] = []
        self._filtered_transactions: list[dict] = []
        self._accounts: list[dict] = []

        self._build_ui()
        self.load_data()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Área com scroll para que a tabela não fique espremida em telas pequenas
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("dashboardContent")  # reutiliza estilo de fundo
        main = QVBoxLayout(content)
        main.setContentsMargins(32, 24, 32, 24)
        main.setSpacing(16)

        # --- Toolbar de filtros ---
        main.addLayout(self._build_toolbar())

        # --- Indicador de loading ---
        self._loading_label = QLabel("Carregando lançamentos…")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._loading_label)

        # --- Tabela ---
        self._table = self._build_table()
        self._table.setVisible(False)
        main.addWidget(self._table)

        main.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # --- Rodapé de totais (fora do scroll — sempre visível) ---
        outer.addWidget(self._build_footer())

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

    def _on_data_ready(self, transactions: list[dict], accounts: list[dict]) -> None:
        self._accounts = accounts

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
        search_text = self._search.text().strip().lower()
        type_filter = self._type_filter.currentText()

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

            cells = [
                (_fmt_date(tx.get("transaction_date", "")), "#E8EAED"),
                (tx.get("description", ""), "#E8EAED"),
                (_CAT_DISPLAY.get(tx.get("category", ""), tx.get("category", "")), "#8B90A7"),
                (self._account_map.get(tx.get("account_id"), "—"), "#8B90A7"),
                (_TYPE_DISPLAY.get(tx_type, tx_type), "#8B90A7"),
                (_fmt_brl(amount if tx_type == "receita" else -amount if tx_type == "despesa" else amount), amount_color),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
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
        dialog = EditTransactionDialog(tx, self._accounts, parent=self)
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
        dialog = NewTransactionDialog(self._accounts, parent=self)
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
