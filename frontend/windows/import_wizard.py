"""
FinanceHub — Wizard de Importação de Extratos
=============================================

QWizard de 3 passos para importar arquivos OFX/QFX ou CSV.

Passo 1 — Selecionar arquivo:
  - Botão "Escolher OFX/QFX" ou "Escolher CSV"
  - Para CSV: configuração de colunas e formato
  - Dropdown: conta de destino
  - Botão "Analisar arquivo" → chama API /import/preview

Passo 2 — Revisar transações:
  - Tabela com status colorido: Nova | Duplicata | Possível duplicata
  - Checkboxes individuais para incluir/excluir
  - Rodapé com resumo: "X novas | Y duplicatas | Z selecionadas"

Passo 3 — Resultado:
  - Barra de progresso durante importação
  - Resumo: "X lançamentos importados"
  - Lista de erros se houver
"""

from __future__ import annotations

import csv
import io
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from frontend.components.api_client import ApiClient, ApiError
from frontend.components.colors import (
    COLOR_ASSET    as _GREEN,
    COLOR_EXPENSE  as _RED,
    COLOR_WARNING  as _ORANGE,
    COLOR_MUTED    as _MUTED,
    COLOR_NEUTRAL  as _WHITE,
)

# ──────────────────────────────────────────────────────────────────────────────
# Workers
# ──────────────────────────────────────────────────────────────────────────────

class PreviewWorker(QThread):
    """Faz upload e preview em background."""

    done    = pyqtSignal(dict)
    failed  = pyqtSignal(str)

    def __init__(
        self,
        client: ApiClient,
        file_path: str,
        account_id: int,
        fmt: str,
        csv_config: dict[str, str],
    ) -> None:
        super().__init__()
        self._client     = client
        self._file_path  = file_path
        self._account_id = account_id
        self._fmt        = fmt
        self._csv_config = csv_config

    def run(self) -> None:
        try:
            if self._fmt == "ofx":
                result = self._client.preview_import_ofx(
                    self._file_path, self._account_id
                )
            else:
                result = self._client.preview_import_csv(
                    self._file_path,
                    self._account_id,
                    date_col=self._csv_config.get("date_col", "Data"),
                    amount_col=self._csv_config.get("amount_col", "Valor"),
                    desc_col=self._csv_config.get("desc_col", "Descrição"),
                    date_format=self._csv_config.get("date_format", "%d/%m/%Y"),
                    delimiter=self._csv_config.get("delimiter", ";"),
                )
            self.done.emit(result)
        except ApiError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Erro inesperado: {exc}")


class ImportWorker(QThread):
    """Importa transações confirmadas em background."""

    done    = pyqtSignal(dict)
    failed  = pyqtSignal(str)

    def __init__(
        self,
        client: ApiClient,
        transactions: list[dict],
        account_id: int,
    ) -> None:
        super().__init__()
        self._client       = client
        self._transactions = transactions
        self._account_id   = account_id

    def run(self) -> None:
        try:
            result = self._client.confirm_import(self._transactions, self._account_id)
            self.done.emit(result)
        except ApiError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Erro inesperado: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Página 1 — Selecionar arquivo
# ──────────────────────────────────────────────────────────────────────────────

class _Page1Select(QWizardPage):
    """Seleção do arquivo e configuração inicial."""

    # Emitido quando o preview foi concluído (dados para a página 2)
    preview_ready = pyqtSignal(dict)

    def __init__(self, client: ApiClient, accounts: list[dict]) -> None:
        super().__init__()
        self._client   = client
        self._accounts = accounts
        self._fmt      = "ofx"
        self._file_path = ""
        self._worker: PreviewWorker | None = None
        self._preview_data: dict = {}

        self.setTitle("Passo 1 — Selecionar arquivo")
        self.setSubTitle(
            "Escolha um arquivo de extrato OFX (Banco do Brasil, Itau, Bradesco) "
            "ou CSV generico. Selecione a conta de destino e clique em "
            "'Analisar arquivo' para detectar duplicatas."
        )
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(16)

        # ── Tipo de arquivo ──────────────────────────────────────────
        type_row = QHBoxLayout()
        ofx_btn = QPushButton("📄  Escolher OFX / QFX")
        ofx_btn.clicked.connect(lambda: self._pick_file("ofx"))
        csv_btn = QPushButton("📋  Escolher CSV")
        csv_btn.clicked.connect(lambda: self._pick_file("csv"))
        type_row.addWidget(ofx_btn)
        type_row.addWidget(csv_btn)
        type_row.addStretch()
        lay.addLayout(type_row)

        # Arquivo selecionado
        self._file_lbl = QLabel("Nenhum arquivo selecionado.")
        self._file_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        lay.addWidget(self._file_lbl)

        # ── Conta de destino ─────────────────────────────────────────
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._account_combo = QComboBox()
        for acc in self._accounts:
            self._account_combo.addItem(
                acc.get("name", "—"), acc["id"]
            )
        self._account_combo.setMinimumWidth(240)
        form.addRow("Conta de destino:", self._account_combo)
        lay.addLayout(form)

        # ── Configuração CSV (oculta por padrão) ─────────────────────
        self._csv_frame = QFrame()
        self._csv_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._csv_frame.setVisible(False)
        csv_form = QFormLayout(self._csv_frame)
        csv_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._date_col_edit   = QLineEdit("Data")
        self._amount_col_edit = QLineEdit("Valor")
        self._desc_col_edit   = QLineEdit("Descrição")

        self._date_fmt_combo = QComboBox()
        self._date_fmt_combo.addItems([
            "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y",
        ])

        self._delim_combo = QComboBox()
        self._delim_combo.addItem("Ponto-e-vírgula (;)", ";")
        self._delim_combo.addItem("Vírgula (,)", ",")
        self._delim_combo.addItem("Tab", "\t")

        csv_form.addRow("Coluna de data:", self._date_col_edit)
        csv_form.addRow("Coluna de valor:", self._amount_col_edit)
        csv_form.addRow("Coluna de descrição:", self._desc_col_edit)
        csv_form.addRow("Formato de data:", self._date_fmt_combo)
        csv_form.addRow("Separador:", self._delim_combo)
        lay.addWidget(self._csv_frame)

        # ── Botão analisar ───────────────────────────────────────────
        analyze_row = QHBoxLayout()
        self._analyze_btn = QPushButton("🔍  Analisar arquivo")
        self._analyze_btn.setProperty("class", "primary")
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.clicked.connect(self._analyze)
        analyze_row.addStretch()
        analyze_row.addWidget(self._analyze_btn)
        lay.addLayout(analyze_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {_MUTED};")
        lay.addWidget(self._status_lbl)

        lay.addStretch()

    def _pick_file(self, fmt: str) -> None:
        self._fmt = fmt
        if fmt == "ofx":
            path, _ = QFileDialog.getOpenFileName(
                self, "Selecionar extrato OFX", "",
                "Extratos OFX/QFX (*.ofx *.qfx);;Todos os arquivos (*)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Selecionar CSV", "",
                "Arquivo CSV (*.csv *.txt);;Todos os arquivos (*)"
            )

        if not path:
            return

        self._file_path = path
        self._file_lbl.setText(f"Arquivo: {path.split('/')[-1]}")
        self._file_lbl.setStyleSheet(f"color: {_WHITE}; font-size: 11px;")
        self._csv_frame.setVisible(fmt == "csv")
        self._analyze_btn.setEnabled(True)
        self._status_lbl.setText("")

        # Para CSV, tenta ler as colunas automaticamente
        if fmt == "csv":
            self._auto_detect_csv_cols(path)

    def _auto_detect_csv_cols(self, path: str) -> None:
        """Tenta detectar as colunas do CSV lendo o cabeçalho."""
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                first_line = fh.readline()
            # Tenta delimitar
            for delim in (";", ",", "\t"):
                parts = first_line.strip().split(delim)
                if len(parts) >= 2:
                    # Atualiza combo e campos com os nomes reais das colunas
                    cols = [p.strip().strip('"') for p in parts]
                    # Identifica colunas por heurística
                    for c in cols:
                        cl = c.lower()
                        if "data" in cl or "date" in cl:
                            self._date_col_edit.setText(c)
                        elif "valor" in cl or "amount" in cl or "value" in cl:
                            self._amount_col_edit.setText(c)
                        elif "desc" in cl or "hist" in cl or "memo" in cl:
                            self._desc_col_edit.setText(c)
                    # Seleciona o delimitador detectado
                    idx = {";": 0, ",": 1, "\t": 2}.get(delim, 0)
                    self._delim_combo.setCurrentIndex(idx)
                    break
        except Exception:
            pass  # Falha silenciosa — usuário configura manualmente

    def _analyze(self) -> None:
        if not self._file_path:
            return

        account_id = self._account_combo.currentData()
        if account_id is None:
            QMessageBox.warning(self, "Conta", "Selecione uma conta de destino.")
            return

        self._analyze_btn.setEnabled(False)
        self._status_lbl.setText("Analisando arquivo…")
        self._status_lbl.setStyleSheet(f"color: {_MUTED};")

        csv_config = {
            "date_col":    self._date_col_edit.text(),
            "amount_col":  self._amount_col_edit.text(),
            "desc_col":    self._desc_col_edit.text(),
            "date_format": self._date_fmt_combo.currentText(),
            "delimiter":   self._delim_combo.currentData() or ";",
        }

        self._worker = PreviewWorker(
            self._client, self._file_path, account_id, self._fmt, csv_config
        )
        self._worker.done.connect(self._on_preview_done)
        self._worker.failed.connect(self._on_preview_failed)
        self._worker.start()

    def _on_preview_done(self, data: dict) -> None:
        self._preview_data = data
        total = data.get("total", 0)
        new   = data.get("new", 0)
        dups  = data.get("duplicates", 0)
        pos   = data.get("possible", 0)

        self._status_lbl.setText(
            f"✓  {total} transações encontradas: "
            f"{new} novas, {dups} duplicatas, {pos} possíveis duplicatas"
        )
        self._status_lbl.setStyleSheet(f"color: {_GREEN};")
        self._analyze_btn.setEnabled(True)
        self.preview_ready.emit(data)
        # Avança automaticamente para a próxima página
        self.wizard().next()

    def _on_preview_failed(self, msg: str) -> None:
        self._status_lbl.setText(f"Erro: {msg}")
        self._status_lbl.setStyleSheet(f"color: {_RED};")
        self._analyze_btn.setEnabled(True)

    def get_account_id(self) -> int | None:
        return self._account_combo.currentData()

    def get_preview_data(self) -> dict:
        return self._preview_data

    def isComplete(self) -> bool:
        return bool(self._file_path)


# ──────────────────────────────────────────────────────────────────────────────
# Página 2 — Revisar transações
# ──────────────────────────────────────────────────────────────────────────────

class _Page2Review(QWizardPage):
    """Revisão das transações com checkboxes e status de duplicata."""

    def __init__(self) -> None:
        super().__init__()
        self._transactions: list[dict] = []
        self._checks: list[QCheckBox] = []

        self.setTitle("Passo 2 — Revisar transações")
        self.setSubTitle(
            "Revise as transações encontradas. Desmarque duplicatas ou lançamentos "
            "que não deseja importar."
        )
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # Toolbar de seleção
        sel_row = QHBoxLayout()
        mark_all_btn = QPushButton("Marcar todas")
        mark_all_btn.clicked.connect(lambda: self._set_all(True))
        unmark_dups_btn = QPushButton("Desmarcar duplicatas")
        unmark_dups_btn.clicked.connect(self._unmark_duplicates)
        sel_row.addWidget(mark_all_btn)
        sel_row.addWidget(unmark_dups_btn)
        sel_row.addStretch()
        lay.addLayout(sel_row)

        # Tabela de transações
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["✓", "Data", "Descrição", "Valor", "Status"]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 32)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(300)
        lay.addWidget(self._table)

        # Rodapé com resumo
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        lay.addWidget(self._summary_lbl)

    def load_transactions(self, transactions: list[dict]) -> None:
        """Popula a tabela com as transações do preview."""
        self._transactions = transactions
        self._checks = []
        self._table.setRowCount(0)
        self._table.setRowCount(len(transactions))

        _STATUS_STYLE: dict[str, tuple[str, str]] = {
            "new":               ("Nova",                _GREEN),
            "duplicate":         ("Duplicata",           _RED),
            "possible_duplicate":("Possível duplicata",  _ORANGE),
        }

        for row, tx in enumerate(transactions):
            status    = tx.get("status", "new")
            selected  = tx.get("selected", status != "duplicate")
            amount    = float(tx.get("amount", 0))
            raw_date  = tx.get("date", "")
            desc      = tx.get("description", "—")
            label, color = _STATUS_STYLE.get(status, ("?", _MUTED))

            # Col 0: checkbox
            chk = QCheckBox()
            chk.setChecked(selected)
            chk.stateChanged.connect(self._update_summary)
            chk_cell = QWidget()
            chk_lay = QHBoxLayout(chk_cell)
            chk_lay.setContentsMargins(4, 0, 4, 0)
            chk_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_lay.addWidget(chk)
            self._table.setCellWidget(row, 0, chk_cell)
            self._checks.append(chk)

            # Col 1: data
            date_item = QTableWidgetItem(raw_date)
            date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, date_item)

            # Col 2: descrição
            desc_item = QTableWidgetItem(desc)
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 2, desc_item)

            # Col 3: valor com cor
            val_str = f"R$ {abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            if amount < 0:
                val_str = "-" + val_str
            val_item = QTableWidgetItem(val_str)
            val_item.setFlags(val_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_item.setForeground(QColor(_GREEN if amount >= 0 else _RED))
            self._table.setItem(row, 3, val_item)

            # Col 4: status badge
            status_item = QTableWidgetItem(label)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(QColor(color))
            self._table.setItem(row, 4, status_item)

        self._update_summary()

    def _set_all(self, checked: bool) -> None:
        for chk in self._checks:
            chk.setChecked(checked)

    def _unmark_duplicates(self) -> None:
        for i, tx in enumerate(self._transactions):
            if tx.get("status") == "duplicate":
                self._checks[i].setChecked(False)

    def _update_summary(self) -> None:
        selected = sum(1 for c in self._checks if c.isChecked())
        total    = len(self._checks)
        new      = sum(
            1 for i, tx in enumerate(self._transactions)
            if tx.get("status") == "new" and self._checks[i].isChecked()
        )
        dups     = sum(
            1 for i, tx in enumerate(self._transactions)
            if tx.get("status") == "duplicate"
        )
        self._summary_lbl.setText(
            f"{new} novas  ·  {dups} duplicatas  ·  {selected}/{total} selecionadas para importar"
        )
        self.completeChanged.emit()

    def get_selected_transactions(self) -> list[dict]:
        """Retorna apenas as transações selecionadas (checkbox marcado)."""
        return [
            self._transactions[i]
            for i, chk in enumerate(self._checks)
            if chk.isChecked()
        ]

    def isComplete(self) -> bool:
        return any(c.isChecked() for c in self._checks)


# ──────────────────────────────────────────────────────────────────────────────
# Página 3 — Resultado
# ──────────────────────────────────────────────────────────────────────────────

class _Page3Result(QWizardPage):
    """Barra de progresso e resultado da importação."""

    import_finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._worker: ImportWorker | None = None

        self.setTitle("Passo 3 — Resultado")
        self.setSubTitle("Importando lançamentos para o FinanceHub…")
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(16)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminado
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        self._result_lbl = QLabel("Pronto para importar.")
        self._result_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_lbl.setStyleSheet("font-size: 14px;")
        lay.addWidget(self._result_lbl)

        self._errors_lbl = QLabel("")
        self._errors_lbl.setStyleSheet(f"color: {_RED}; font-size: 11px;")
        self._errors_lbl.setWordWrap(True)
        lay.addWidget(self._errors_lbl)

        lay.addStretch()

    def run_import(
        self,
        client: ApiClient,
        transactions: list[dict],
        account_id: int,
    ) -> None:
        """Dispara o worker de importação."""
        if not transactions:
            self._result_lbl.setText("Nenhuma transação selecionada.")
            return

        self._progress.setVisible(True)
        self._result_lbl.setText("Importando…")
        self._result_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 14px;")

        self._worker = ImportWorker(client, transactions, account_id)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, result: dict) -> None:
        self._progress.setVisible(False)
        imported = result.get("imported", 0)
        skipped  = result.get("skipped", 0)
        errors   = result.get("errors", [])

        self._result_lbl.setText(
            f"✅  {imported} lançamentos importados com sucesso!"
        )
        self._result_lbl.setStyleSheet(f"color: {_GREEN}; font-size: 14px; font-weight: 700;")

        if skipped:
            self._result_lbl.setText(
                self._result_lbl.text() + f"\n{skipped} ignorados (sem valor)."
            )

        if errors:
            self._errors_lbl.setText("Erros:\n" + "\n".join(errors[:5]))

        self.setSubTitle("Importação concluída.")
        self.import_finished.emit()
        self.completeChanged.emit()

    def _on_failed(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._result_lbl.setText(f"❌  Erro: {msg}")
        self._result_lbl.setStyleSheet(f"color: {_RED}; font-size: 14px;")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._worker is not None and not self._worker.isRunning()


# ──────────────────────────────────────────────────────────────────────────────
# Wizard principal
# ──────────────────────────────────────────────────────────────────────────────

class ImportWizard(QWizard):
    """
    Wizard de importação de extratos.

    Emite `import_completed` quando a importação for concluída com sucesso —
    o caller deve recarregar a lista de transações.
    """

    import_completed = pyqtSignal()

    def __init__(self, client: ApiClient, accounts: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client   = client
        self._accounts = accounts

        self.setWindowTitle("Importar Extrato Bancário")
        self.setMinimumSize(720, 540)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        # Páginas
        self._page1 = _Page1Select(client, accounts)
        self._page2 = _Page2Review()
        self._page3 = _Page3Result()

        self.addPage(self._page1)
        self.addPage(self._page2)
        self.addPage(self._page3)

        # Conecta preview → populate review page
        self._page1.preview_ready.connect(self._on_preview_ready)

        # Quando avança para página 3, dispara importação
        self.currentIdChanged.connect(self._on_page_changed)

        # Quando importação concluída, emite sinal externo
        self._page3.import_finished.connect(self.import_completed)

    def _on_preview_ready(self, data: dict) -> None:
        self._page2.load_transactions(data.get("transactions", []))

    def _on_page_changed(self, page_id: int) -> None:
        """Dispara importação ao entrar na página 3."""
        if page_id == self.indexOf(self._page3):
            selected = self._page2.get_selected_transactions()
            account_id = self._page1.get_account_id()
            if selected and account_id:
                self._page3.run_import(self._client, selected, account_id)
