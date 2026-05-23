"""
FinanceHub — TickerLogoWidget
==============================

Widget circular que exibe o logo de uma empresa, ETF, FII ou ativo.

Estratégia de carga (prioridade):
  1. Cache em memória — instantâneo se já carregado anteriormente
  2. Download async do logo via Logo.dev API (sem bloquear a UI)
  3. Fallback imediato: círculo colorido com as 2 primeiras letras do ticker

Uso:
    logo = TickerLogoWidget("PETR4", "acao", size=36)
    table.setCellWidget(row, 0, logo_and_text_widget)
"""

from __future__ import annotations

import urllib.request
from typing import ClassVar

from PyQt6.QtCore import (
    Qt, QThread, QTimer, pyqtSignal, QByteArray,
)
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QPixmap,
)
from PyQt6.QtWidgets import QLabel, QWidget

from frontend.components.colors import (
    COLOR_STOCK, COLOR_FII, COLOR_ETF, COLOR_TREASURY,
    COLOR_FIXED_INCOME, COLOR_CRYPTO, COLOR_INTERNATIONAL,
    COLOR_PENSION, COLOR_OTHER, CATEGORY_COLOR,
)


# ======================================================================
# Mapeamento ticker B3 → domínio oficial para Logo.dev
# ======================================================================

TICKER_DOMAINS: dict[str, str] = {
    # Petróleo & Gás
    "PETR4": "petrobras.com.br", "PETR3": "petrobras.com.br",
    "PRIO3": "priooil.com.br",   "RECV3": "petreconcavo.com.br",
    "UGPA3": "ultrapar.com.br",  "VBBR3": "vibra.com.br",
    # Mineração
    "VALE3": "vale.com",         "CMIN3": "csn.com.br",
    # Bancos
    "ITUB4": "itau.com.br",      "ITUB3": "itau.com.br",
    "BBDC4": "bradesco.com.br",  "BBDC3": "bradesco.com.br",
    "BBAS3": "bb.com.br",        "SANB11": "santander.com.br",
    "SANB4": "santander.com.br", "BRSR6": "banrisul.com.br",
    "BMGB4": "bancobbmg.com.br", "BPAN4": "bancopan.com.br",
    "ABCB4": "abcbrasil.com.br", "PINE4": "pine.com",
    # Seguros / financeiras
    "BBSE3": "bb.com.br",        "IRBR3": "irbbrasil.com.br",
    "WIZS3": "wizsolucoes.com.br",
    # Consumo / varejo
    "ABEV3": "ambev.com.br",     "MDIA3": "mdiabrasil.com.br",
    "MGLU3": "magazineluiza.com.br", "VIVA3": "animasaude.com.br",
    "LREN3": "lojasrenner.com.br",   "ARZZ3": "arezzo.com.br",
    "SBSP3": "sabesp.com.br",    "RADL3": "raia.com.br",
    "PETZ3": "petlove.com.br",   "SOMA3": "somagrupo.com.br",
    "MYPK3": "iochp-maxion.com.br",  "ALPA4": "alpargatas.com.br",
    "TFCO4": "teka.com.br",      "CEAB3": "cea.com.br",
    "AMAR3": "amar.com.br",
    # Construção civil
    "MRVE3": "mrv.com.br",       "CYRE3": "cyrela.com.br",
    "DIRR3": "direcional.com.br","EZTC3": "eztec.com.br",
    "JHSF3": "jhsf.com.br",      "EVEN3": "even.com.br",
    "PLPL3": "palaplast.com.br",
    # Energia
    "ELET3": "eletrobras.com.br","ELET6": "eletrobras.com.br",
    "CMIG4": "cemig.com.br",     "CPFE3": "cpfl.com.br",
    "ENGI11": "energisaon.com.br","TAEE11": "taesa.com.br",
    "EGIE3": "engie.com.br",     "AURE3": "auren.com.br",
    "TRPL4": "isatesauro.com.br",
    # Telecomunicações
    "VIVT3": "vivo.com.br",      "TIMS3": "tim.com.br",
    "OIBR3": "oi.com.br",
    # Saúde
    "HAPV3": "hapvida.com.br",   "RDOR3": "rededorsirio.com.br",
    "QUAL3": "qualicorp.com.br", "HYPE3": "hypera.com.br",
    "DASA3": "dasa.com.br",      "FLRY3": "fleury.com.br",
    "GNDI3": "gndi.com.br",
    # Logística / Transporte
    "RAIL3": "rumo.com.br",      "CCRO3": "ccr.com.br",
    "ECOR3": "ecorodovias.com.br","AZUL4": "azul.com.br",
    "GOLL4": "voegol.com.br",    "CIEL3": "cielo.com.br",
    # Papel & Celulose
    "SUZB3": "suzano.com.br",    "KLBN11": "klabin.com.br",
    # Agronegócio
    "SLCE3": "slc.com.br",       "TTEN3": "trestentos.com.br",
    "SMTO3": "santamaria.com.br",
    # Tecnologia
    "TOTVS3": "totvs.com.br",    "LWSA3": "locaweb.com.br",
    "INTB3": "intelbras.com.br", "POSI3": "positivo.com.br",
    "CASH3": "meliuz.com.br",    "SEER3": "seer.com.br",
    # ETFs populares B3
    "BOVA11": "blackrock.com",   "IVVB11": "blackrock.com",
    "SMAL11": "blackrock.com",   "XFIX11": "xpasset.com.br",
    "HASH11": "hashdex.com",     "GOLD11": "blackrock.com",
    # Tesouro Direto (sem logo)
    "LTN":    None, "NTNB": None, "NTNF": None,
    "LFT":    None, "NTNB-P": None,
    # Cripto (usa domínio da exchange se quiser)
    "BTC":    "bitcoin.org",     "ETH": "ethereum.org",
    "BNB":    "bnbchain.org",    "SOL": "solana.com",
}

# Cor do círculo fallback por tipo de ativo
_TYPE_CIRCLE_COLOR: dict[str, str] = {
    "acao":               COLOR_STOCK,
    "fii":                COLOR_FII,
    "etf":                COLOR_ETF,
    "tesouro_direto":     COLOR_TREASURY,
    "renda_fixa":         COLOR_FIXED_INCOME,
    "criptomoeda":        COLOR_CRYPTO,
    "acao_internacional": COLOR_INTERNATIONAL,
    "previdencia":        COLOR_PENSION,
    "outros":             COLOR_OTHER,
}

# Cache global em memória: ticker → QPixmap já circular
_LOGO_CACHE: dict[str, QPixmap] = {}


# ======================================================================
# Worker de download de logo
# ======================================================================

class _LogoDownloadWorker(QThread):
    """Faz download do logo em background e emite o PNG como bytes."""

    image_ready = pyqtSignal(str, bytes)   # (ticker, raw_bytes)
    failed      = pyqtSignal(str)          # (ticker)

    def __init__(self, ticker: str, url: str) -> None:
        super().__init__()
        self._ticker = ticker
        self._url    = url

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": "FinanceHub/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
            if len(data) > 200:          # PNG/SVG mínimo real
                self.image_ready.emit(self._ticker, data)
            else:
                self.failed.emit(self._ticker)
        except Exception:
            self.failed.emit(self._ticker)


# ======================================================================
# Widget principal
# ======================================================================

class TickerLogoWidget(QLabel):
    """
    QLabel circular com o logo da empresa ou fallback com iniciais.

    Parâmetros
    ----------
    ticker     : símbolo do ativo (ex: "PETR4", "MXRF11", "BOVA11")
    asset_type : tipo do ativo  (ex: "acao", "fii", "etf", ...)
    size       : diâmetro em pixels (default 36)
    """

    # Compartilhado entre todos os widgets para não criar workers duplicados
    _pending: ClassVar[set[str]] = set()

    def __init__(
        self,
        ticker: str,
        asset_type: str = "acao",
        size: int = 36,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ticker     = ticker.upper() if ticker else "??"
        self._asset_type = asset_type
        self._size       = size
        self._worker: _LogoDownloadWorker | None = None

        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Mostra fallback imediatamente
        if self._ticker in _LOGO_CACHE:
            self.setPixmap(_LOGO_CACHE[self._ticker])
        else:
            self._show_fallback()
            self._try_download()

    # ------------------------------------------------------------------
    # Fallback: círculo colorido com iniciais
    # ------------------------------------------------------------------

    def _show_fallback(self) -> None:
        self.setPixmap(self._make_initials_pixmap())

    def _make_initials_pixmap(self) -> QPixmap:
        size  = self._size
        color = _TYPE_CIRCLE_COLOR.get(self._asset_type, COLOR_OTHER)
        initials = self._ticker[:2]

        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Círculo de fundo
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size, size)

        # Iniciais em branco
        font = QFont("Inter", max(8, size // 3), QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, initials)
        painter.end()

        return pix

    # ------------------------------------------------------------------
    # Download assíncrono do logo
    # ------------------------------------------------------------------

    def _try_download(self) -> None:
        domain = TICKER_DOMAINS.get(self._ticker)

        # Tipos que usam apenas fallback (sem logo externo)
        if domain is None and self._asset_type in ("tesouro_direto", "renda_fixa"):
            return

        if not domain:
            # Tenta domínio genérico *.com.br como último recurso apenas para ações
            if self._asset_type != "acao":
                return
            # Não tenta para tickers desconhecidos sem domínio mapeado
            return

        if self._ticker in self._pending:
            return

        self._pending.add(self._ticker)
        url = f"https://img.logo.dev/{domain}?token=pk_free&size={self._size * 2}&format=png"

        self._worker = _LogoDownloadWorker(self._ticker, url)
        self._worker.image_ready.connect(self._on_image_ready)
        self._worker.failed.connect(self._on_download_failed)
        self._worker.start()

    def _on_image_ready(self, ticker: str, data: bytes) -> None:
        self._pending.discard(ticker)
        raw = QByteArray(data)
        pix = QPixmap()
        pix.loadFromData(raw)
        if pix.isNull():
            return

        # Redimensiona e recorta em círculo
        pix = pix.scaled(
            self._size, self._size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        circular = self._make_circular(pix)
        _LOGO_CACHE[ticker] = circular

        # Atualiza todos os widgets ativos que aguardavam este ticker
        if not self.isHidden():
            self.setPixmap(circular)

    def _on_download_failed(self, ticker: str) -> None:
        self._pending.discard(ticker)
        # Fallback já está exibido; sem ação necessária

    def _make_circular(self, pix: QPixmap) -> QPixmap:
        size = self._size
        result = QPixmap(size, size)
        result.fill(Qt.GlobalColor.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Máscara circular
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)

        # Centraliza o pixmap no círculo
        x = (size - pix.width())  // 2
        y = (size - pix.height()) // 2
        painter.drawPixmap(x, y, pix)
        painter.end()

        return result
