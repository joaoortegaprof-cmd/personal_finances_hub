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
import threading
import time
from pathlib import Path

import requests
import uvicorn
from loguru import logger
from PyQt6.QtWidgets import QApplication, QMessageBox

# ── Diretórios ──────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

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

    # Arquivo — nível DEBUG, rotação diária, mantém 7 dias
    logger.add(
        LOG_DIR / "finance_hub.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
        rotation="00:00",   # novo arquivo à meia-noite
        retention="7 days",
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

def main() -> None:
    _setup_logger()
    logger.info("Iniciando FinanceHub…")

    # Importa as configurações depois de _setup_logger para que erros de
    # importação também sejam capturados pelo loguru.
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
        daemon=True,           # morre quando o processo principal termina
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
    # QApplication precisa existir para mostrar diálogos
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.critical(None, title, message)


if __name__ == "__main__":
    main()
