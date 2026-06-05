"""
Ponto de entrada do FinanceHub.

Sequência de inicialização:
  1. Configura o logger (loguru → data/logs/finance_hub.log)
  2. Sobe o servidor FastAPI (uvicorn) em uma thread daemon
  3. Aguarda a API responder no endpoint /health (até 15 s)
  4. Inicia a aplicação PyQt6 e abre a MainWindow
  5. Ao fechar a janela, o processo termina (thread daemon morre junto)
"""

import sys
import os
from pathlib import Path

# Detectar se está rodando como executável PyInstaller
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys._MEIPASS)
    DATA_DIR = Path(sys.executable).parent / 'data'
else:
    APP_DIR = Path(__file__).parent
    DATA_DIR = APP_DIR / 'data'

# Garantir diretórios necessários
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / 'logs').mkdir(exist_ok=True)
(DATA_DIR / 'backups').mkdir(exist_ok=True)

# Configurar DATABASE_URL com path absoluto
os.environ.setdefault(
    'DATABASE_URL',
    f'sqlite+aiosqlite:///{DATA_DIR}/financehub.db'
)

# Adicionar APP_DIR ao sys.path para imports
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Path do tema QSS
THEME_PATH = APP_DIR / 'frontend' / 'styles' / 'theme.qss'

import threading
import time

import requests
import uvicorn
from loguru import logger
from PyQt6.QtWidgets import QApplication, QMessageBox

from scripts.backup import create_backup


# ── Logger ───────────────────────────────────────────────────────────────────

def _setup_logger() -> None:
    """Configura loguru: console resumido + arquivo rotativo em data/logs/."""
    logger.remove()  # remove o handler padrão (stderr sem formato)

    # Console — nível INFO, formato compacto
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        colorize=True,
    )

    # Arquivo — nível INFO, rotação semanal, mantém 1 mês
    logger.add(
        DATA_DIR / 'logs' / 'financehub_{time}.log',
        rotation='1 week',
        retention='1 month',
        level='INFO',
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
        encoding="utf-8",
    )


# ── FastAPI / Uvicorn ────────────────────────────────────────────────────────

def _start_api_server(host: str, port: int) -> None:
    """Roda o uvicorn em modo síncrono — chamado dentro de uma thread daemon."""
    config = uvicorn.Config(
        "backend.api.app:app",
        host=host,
        port=port,
        log_level="warning",   # uvicorn silencioso; loguru cuida dos logs do app
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.run()


def _wait_for_api(url: str, timeout: float = 15.0, interval: float = 0.3) -> bool:
    """
    Faz polling no /health até a API responder ou o timeout expirar.
    Retorna True se a API subiu, False caso contrário.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(interval)
    return False


# ── Inicialização principal ──────────────────────────────────────────────────

def _backup_scheduler() -> None:
    """
    Thread daemon que cria um backup do banco a cada 24 horas.

    Daemon=True garante que não impeça o encerramento do processo
    quando o usuário fechar a janela principal.
    """
    while True:
        time.sleep(24 * 60 * 60)   # aguarda 24 horas
        try:
            create_backup()
        except Exception as exc:
            logger.warning("Erro no backup automático: {}", exc)


def main() -> None:
    _setup_logger()
    logger.info("Iniciando FinanceHub…")

    # ── Backup imediato na inicialização ─────────────────────────────────
    try:
        backup_path = create_backup()
        if backup_path:
            logger.info("Backup inicial criado: {}", backup_path)
    except Exception as exc:
        logger.warning("Falha no backup inicial (não crítico): {}", exc)

    # ── Agendador de backup diário ────────────────────────────────────────
    backup_thread = threading.Thread(
        target=_backup_scheduler,
        daemon=True,
        name="backup-scheduler",
    )
    backup_thread.start()

    try:
        from backend.core.config import settings
    except Exception as exc:
        logger.critical("Falha ao carregar configurações: {}", exc)
        _show_fatal_error("Erro de configuração", str(exc))
        sys.exit(1)

    host = settings.API_HOST
    port = settings.API_PORT
    health_url = f"http://{host}:{port}/health"

    # ── Thread do servidor ────────────────────────────────────────────────
    logger.info("Subindo servidor FastAPI em {}:{}", host, port)
    api_thread = threading.Thread(
        target=_start_api_server,
        args=(host, port),
        daemon=True,
        name="uvicorn-server",
    )
    api_thread.start()

    # ── Health check ─────────────────────────────────────────────────────
    logger.info("Aguardando API ficar disponível em {}…", health_url)
    if not _wait_for_api(health_url):
        logger.error("API não respondeu em 15 s — abortando.")
        _show_fatal_error(
            "Falha na inicialização",
            f"O servidor interno não respondeu em {health_url}.\n"
            "Verifique se a porta está livre e tente novamente.",
        )
        sys.exit(1)

    logger.info("API pronta.")

    # ── Agendador de notificações ─────────────────────────────────────────
    try:
        from backend.services.notification_scheduler import NotificationScheduler

        scheduler = NotificationScheduler(
            api_base=f"http://{host}:{port}",
            interval_seconds=3600,
        )
        scheduler.start()
        logger.info("NotificationScheduler iniciado.")
    except Exception as exc:
        logger.warning("Falha ao iniciar NotificationScheduler: {}", exc)

    # ── PyQt6 ─────────────────────────────────────────────────────────────
    try:
        from frontend.windows.main_window import MainWindow
    except Exception as exc:
        logger.critical("Falha ao importar a interface gráfica: {}", exc)
        _show_fatal_error("Erro de interface", str(exc))
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("FinanceHub")
    app.setOrganizationName("FinanceHub")

    # Carregar tema QSS
    if THEME_PATH.exists():
        with open(THEME_PATH, 'r', encoding='utf-8') as f:
            app.setStyleSheet(f.read())
    else:
        logger.warning("Tema não encontrado em {}", THEME_PATH)

    try:
        window = MainWindow()
        window.show()
    except Exception as exc:
        logger.critical("Falha ao abrir a janela principal: {}", exc)
        _show_fatal_error("Erro ao abrir janela", str(exc))
        sys.exit(1)

    logger.info("Interface aberta. Aguardando o usuário.")
    exit_code = app.exec()
    logger.info("Aplicação encerrada (código {}).", exit_code)
    sys.exit(exit_code)


# ── Utilitários ───────────────────────────────────────────────────────────────

def _show_fatal_error(title: str, message: str) -> None:
    """Exibe uma caixa de erro mesmo quando a janela principal ainda não existe."""
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.critical(None, title, message)


if __name__ == "__main__":
    main()
