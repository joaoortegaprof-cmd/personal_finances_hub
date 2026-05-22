"""
Janela principal do FinanceHub.

Estrutura de layout:
  ┌──────────┬──────────────────────────────────┐
  │          │  Header (logo + título da página) │
  │ Sidebar  ├──────────────────────────────────┤
  │  (nav)   │                                  │
  │          │   QStackedWidget (páginas)        │
  │          │                                  │
  └──────────┴──────────────────────────────────┘

A sidebar ocupa largura fixa de 220 px. O QStackedWidget troca de página
sem destruir os widgets — preserva o estado (dados carregados) ao navegar.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.components.signals import app_signals
from frontend.windows.accounts import AccountsPage
from frontend.windows.cards import CardsPage
from frontend.windows.dashboard import DashboardPage
from frontend.windows.investments import InvestmentsPage
from frontend.windows.market import MarketPage
from frontend.windows.reports import ReportsPage
from frontend.windows.settings_page import SettingsPage
from frontend.windows.simulation import SimulationPage
from frontend.windows.tax import TaxPage
from frontend.windows.transactions import TransactionsPage
from frontend.services.notification_service import NotificationService
from frontend.components.api_client import ApiClient

# Caminho do tema QSS relativo a este arquivo
_THEME_PATH = Path(__file__).parent.parent / "styles" / "theme.qss"

# Intervalo entre verificações de alertas (30 minutos em ms)
_NOTIFICATION_INTERVAL_MS = 30 * 60 * 1000


class _NotificationWorker(QThread):
    """Executa NotificationService.check_and_notify() fora da thread principal."""

    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self._service = NotificationService(client)

    def run(self) -> None:
        self._service.check_and_notify()


# Itens da sidebar: (label exibido, índice no QStackedWidget)
_NAV_ITEMS: list[tuple[str, int]] = [
    ("Dashboard",    0),
    ("Lançamentos",  1),
    ("Contas",       2),
    ("Cartões",      3),
    ("Investimentos",4),
    ("Mercado",      5),
    ("Relatórios",   6),
    ("IR e DARF",    7),
    ("Configurações",8),
    ("Simulações",   9),
]


class MainWindow(QMainWindow):
    """
    Janela principal que agrega sidebar, header e área de conteúdo.

    Fluxo de inicialização:
      1. Aplica o tema QSS global
      2. Monta o layout (sidebar | header + stack)
      3. Registra as páginas no QStackedWidget
      4. Exibe o Dashboard como página inicial
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FinanceHub")
        self.setMinimumSize(1200, 750)
        self.showMaximized()

        self._apply_theme()
        self._build_ui()

        # Notificações: dispara 5 s após abertura e depois a cada 30 min
        self._notif_client  = ApiClient()
        self._notif_worker: _NotificationWorker | None = None
        QTimer.singleShot(5_000, self._run_notifications)
        self._notif_timer = QTimer(self)
        self._notif_timer.setInterval(_NOTIFICATION_INTERVAL_MS)
        self._notif_timer.timeout.connect(self._run_notifications)
        self._notif_timer.start()

    # ------------------------------------------------------------------
    # Tema
    # ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        """Carrega e aplica o stylesheet QSS em toda a aplicação."""
        if _THEME_PATH.exists():
            self.setStyleSheet(_THEME_PATH.read_text(encoding="utf-8"))
        else:
            # Fallback mínimo: fundo escuro se o arquivo não for encontrado
            self.setStyleSheet("QWidget { background-color: #0F1117; color: #E8EAED; }")

    # ------------------------------------------------------------------
    # Layout principal
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Sidebar fixa à esquerda
        sidebar = self._build_sidebar()
        root_layout.addWidget(sidebar)

        # Área direita: header empilhado sobre o stack de páginas
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        header = self._build_header()
        right_layout.addWidget(header)

        self.stack = self._build_stack()
        right_layout.addWidget(self.stack)

        root_layout.addWidget(right_panel)

        # Ativa o Dashboard na abertura
        self._navigate(0)

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> QFrame:
        """
        Sidebar com botões de navegação checkable.

        Usar QPushButton checkable em vez de QListWidget permite estilos
        CSS arbitrários (borda lateral colorida no item ativo) sem subclassing.
        """
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Logo / identidade do app no topo da sidebar
        logo_widget = QWidget()
        logo_widget.setObjectName("sidebarLogo")
        logo_layout = QVBoxLayout(logo_widget)
        logo_layout.setContentsMargins(20, 20, 20, 16)
        logo_label = QLabel("FinanceHub")
        logo_label.setObjectName("logoLabel")
        logo_layout.addWidget(logo_label)
        layout.addWidget(logo_widget)

        # Botões de navegação
        self._nav_buttons: list[QPushButton] = []
        for label, index in _NAV_ITEMS:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # evita borda de foco no estilo
            # Captura o índice correto para o closure com argumento default
            btn.clicked.connect(lambda _checked, i=index: self._navigate(i))
            layout.addWidget(btn)
            self._nav_buttons.append(btn)

        layout.addStretch()

        # Rodapé da sidebar: botão de atualização global
        refresh_btn = QPushButton("↻  Atualizar")
        refresh_btn.setObjectName("navRefreshButton")
        refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        refresh_btn.clicked.connect(self._refresh_current_page)
        layout.addWidget(refresh_btn)

        return sidebar

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _build_header(self) -> QFrame:
        """
        Faixa horizontal no topo da área de conteúdo.

        Mostra o título da página atual. Pode receber ícone de usuário,
        barra de busca ou botão de atualização em iterações futuras.
        """
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(56)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 0, 24, 0)

        self._header_title = QLabel("Dashboard")
        self._header_title.setObjectName("headerTitle")
        layout.addWidget(self._header_title)

        layout.addStretch()

        return header

    # ------------------------------------------------------------------
    # Stack de páginas
    # ------------------------------------------------------------------

    def _build_stack(self) -> QStackedWidget:
        """
        Registra todas as páginas da aplicação.

        Ordem deve ser idêntica à de _NAV_ITEMS para que _navigate(index)
        funcione corretamente.
        """
        stack = QStackedWidget()

        # 0 — Dashboard
        self._dashboard_page = DashboardPage()
        stack.addWidget(self._dashboard_page)

        # 1 — Lançamentos
        self._transactions_page = TransactionsPage()
        stack.addWidget(self._transactions_page)

        # 2 — Contas
        self._accounts_page = AccountsPage()
        stack.addWidget(self._accounts_page)

        # 3 — Cartões e Faturas
        self._cards_page = CardsPage()
        stack.addWidget(self._cards_page)

        # 4 — Investimentos
        self._investments_page = InvestmentsPage()
        stack.addWidget(self._investments_page)

        # 5 — Mercado
        self._market_page = MarketPage()
        stack.addWidget(self._market_page)

        # 6 — Relatórios
        self._reports_page = ReportsPage()
        stack.addWidget(self._reports_page)

        # 7 — IR e DARF
        self._tax_page = TaxPage()
        stack.addWidget(self._tax_page)

        # 8 — Configurações e Metas
        self._settings_page = SettingsPage()
        stack.addWidget(self._settings_page)

        # 9 — Simulações financeiras
        self._simulation_page = SimulationPage()
        stack.addWidget(self._simulation_page)

        return stack

    # ------------------------------------------------------------------
    # Navegação
    # ------------------------------------------------------------------

    def _run_notifications(self) -> None:
        if self._notif_worker and self._notif_worker.isRunning():
            return
        self._notif_worker = _NotificationWorker(self._notif_client)
        self._notif_worker.start()

    def _refresh_current_page(self) -> None:
        page = self.stack.currentWidget()
        load = getattr(page, "load_data", None)
        if callable(load):
            load()

    def _navigate(self, index: int) -> None:
        """Troca de página e sincroniza o estado visual da sidebar e do header."""
        self.stack.setCurrentIndex(index)

        # Atualiza título do header
        label, _ = _NAV_ITEMS[index]
        self._header_title.setText(label)

        # Garante que apenas o botão da página atual fique marcado
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
