"""
Serviço de dados de mercado do FinanceHub.

Responsável por buscar cotações e histórico de preços via yfinance,
e indicadores macroeconômicos (CDI, IPCA) via python-bcb / SGS do Banco Central.

Cache em memória:
  Cotações são cacheadas por MARKET_DATA_REFRESH_INTERVAL segundos (padrão 5 min)
  para evitar rate limiting das APIs externas e reduzir latência nas telas.
  O cache é por instância de serviço — em produção multi-worker, substitua
  por Redis ou Memcached para compartilhar entre processos.

Tickers B3 no yfinance:
  yfinance exige o sufixo ".SA" para ativos da B3:
    PETR4 → "PETR4.SA"    (Petrobras PN)
    HGLG11 → "HGLG11.SA"  (FII CSHG Logística)
  Índices e ativos internacionais usam o formato padrão:
    "^BVSP"   → Ibovespa
    "^GSPC"   → S&P 500
    "BTC-USD" → Bitcoin em dólares

Nota sobre async:
  yfinance e python-bcb não têm interface nativa async/await —
  internamente fazem chamadas HTTP síncronas. Os métodos deste serviço
  são declarados async por consistência com o restante da aplicação.
  Em produção com alta carga, envolva as chamadas em asyncio.to_thread()
  para não bloquear o event loop do FastAPI.
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import yfinance as yf

from backend.core.config import settings


# Tipo que enumera os benchmarks disponíveis para comparação
BenchmarkType = Literal["CDI", "IBOV", "IPCA", "SP500"]

# Mapeamento de benchmark → ticker do yfinance (para os de renda variável)
_BENCHMARK_TICKERS: dict[str, str] = {
    "IBOV": "^BVSP",
    "SP500": "^GSPC",
}

# Mapeamento de benchmark → código SGS do Banco Central (para macro)
# SGS 11: CDI acumulado (taxa diária) | SGS 433: IPCA mensal
_BCB_SERIES_CODES: dict[str, int] = {
    "CDI": 11,
    "IPCA": 433,
}


# -----------------------------------------------------------------
# DTOs de saída
# -----------------------------------------------------------------

@dataclass
class Quote:
    """Cotação atual de um ativo com metadados de horário e variação."""
    ticker: str
    price: Decimal
    currency: str
    fetched_at: datetime     # exibir "atualizado às HH:MM" na interface
    change_pct: Decimal | None = None  # variação % no dia vs fechamento anterior
    volume: int | None = None          # volume médio 3 meses (yfinance fast_info)


@dataclass
class PriceHistory:
    """Série histórica de preços de fechamento ajustados de um ativo."""
    ticker: str
    dates: list[date]
    prices: list[Decimal]
    currency: str


@dataclass
class ReturnComparison:
    """Comparação de rentabilidade percentual entre um ativo e um benchmark."""
    ticker: str
    period_start: date
    period_end: date
    asset_return_pct: Decimal       # retorno do ativo no período
    benchmark_return_pct: Decimal   # retorno do benchmark no período
    benchmark_name: BenchmarkType
    alpha: Decimal                  # asset_return − benchmark_return (excesso de retorno)


@dataclass
class _CacheEntry:
    """Entrada interna do cache com o valor e o timestamp de inserção."""
    value: object
    saved_at: float  # time.monotonic() — relógio de tempo corrido, imune a ajustes de SO


# -----------------------------------------------------------------
# Serviço
# -----------------------------------------------------------------

class MarketDataService:
    """
    Serviço de dados de mercado com cache em memória.

    Instanciar sem parâmetros — o TTL do cache é lido de settings.
    O mesmo objeto pode ser reutilizado por toda a vida da aplicação
    (singleton recomendado) para maximizar os hits de cache.
    """

    def __init__(self) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        # TTL em segundos — padrão 300 s = 5 minutos (ver core/config.py)
        self._ttl_seconds = settings.MARKET_DATA_REFRESH_INTERVAL

    # -----------------------------------------------------------------
    # Cotação atual
    # -----------------------------------------------------------------

    async def get_quote(self, ticker: str) -> Quote:
        """
        Busca a cotação atual de um ativo.

        O ticker deve estar no formato yfinance — use to_yfinance_ticker()
        para converter automaticamente tickers da B3:
            PETR4    → PETR4.SA
            HGLG11   → HGLG11.SA
            ^BVSP    → ^BVSP (sem alteração)
            BTC-USD  → BTC-USD (sem alteração)

        fast_info é a API mais leve do yfinance — retorna apenas os campos
        essenciais (preço, variação, volume) sem baixar o prospecto completo
        do ativo (que inclui dados de RI, balanços, etc.).
        """
        cache_key = f"quote:{ticker}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        info = yf.Ticker(ticker)
        fast = info.fast_info

        price = Decimal(str(fast.last_price or 0)).quantize(Decimal("0.01"))
        currency = getattr(fast, "currency", "BRL") or "BRL"

        # Variação do dia: (preço_atual − fechamento_anterior) / fechamento_anterior
        change_pct: Decimal | None = None
        prev_close = getattr(fast, "previous_close", None)
        if prev_close and prev_close > 0 and float(price) > 0:
            raw_change = (float(price) - prev_close) / prev_close * 100
            change_pct = Decimal(str(raw_change)).quantize(Decimal("0.01"))

        raw_volume = getattr(fast, "three_month_average_volume", None)
        volume = int(raw_volume) if raw_volume else None

        quote = Quote(
            ticker=ticker,
            price=price,
            currency=currency,
            fetched_at=datetime.now(),
            change_pct=change_pct,
            volume=volume,
        )
        self._save_to_cache(cache_key, quote)
        return quote

    # -----------------------------------------------------------------
    # Histórico de preços
    # -----------------------------------------------------------------

    async def get_price_history(
        self,
        ticker: str,
        start_date: date,
        end_date: date | None = None,
        interval: str = "1d",
    ) -> PriceHistory:
        """
        Busca o histórico de preços de fechamento ajustados de um ativo.

        auto_adjust=True aplica ajustes retroativos por:
          • Desdobramentos / grupamentos (split/reverse split)
          • Dividendos reinvestidos
          Isso garante que o gráfico de retorno seja comparável ao longo
          do tempo sem distorções por eventos corporativos.

        interval aceita: "1d" (diário), "1wk" (semanal), "1mo" (mensal).
        Intervalos intraday (1h, 5m) são suportados pelo yfinance mas têm
        janela máxima de 60 dias — usar só para análise técnica de curto prazo.

        O resultado é cacheado pela combinação (ticker + datas + intervalo)
        para evitar re-download durante a mesma sessão de análise.
        """
        end = end_date or date.today()
        cache_key = f"history:{ticker}:{start_date}:{end}:{interval}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        df = yf.download(
            ticker,
            start=start_date.isoformat(),
            end=end.isoformat(),
            interval=interval,
            progress=False,   # suprime a barra de progresso no terminal
            auto_adjust=True, # ajusta por splits e dividendos automaticamente
        )

        if df.empty:
            return PriceHistory(ticker=ticker, dates=[], prices=[], currency="BRL")

        # yfinance retorna MultiIndex quando há apenas um ticker — normaliza
        close = df["Close"]
        if hasattr(close, "squeeze"):
            close = close.squeeze()

        dates = [idx.date() for idx in close.index]
        prices = [Decimal(str(p)).quantize(Decimal("0.01")) for p in close.values]

        # Heurística de moeda: B3 (.SA) e índices brasileiros → BRL, demais → USD
        currency = "BRL" if (".SA" in ticker or ticker.startswith("^BVS")) else "USD"

        history = PriceHistory(ticker=ticker, dates=dates, prices=prices, currency=currency)
        self._save_to_cache(cache_key, history)
        return history

    # -----------------------------------------------------------------
    # Comparação com benchmark
    # -----------------------------------------------------------------

    async def compare_with_benchmark(
        self,
        ticker: str,
        benchmark: BenchmarkType,
        start_date: date,
        end_date: date | None = None,
    ) -> ReturnComparison:
        """
        Calcula o retorno total simples do ativo vs o benchmark no período.

        Retorno total simples:
            retorno = (preço_final / preço_inicial − 1) × 100

        Alpha (excesso de retorno):
            alpha = retorno_ativo − retorno_benchmark
            alpha > 0 → ativo superou o benchmark ("bateu o índice")
            alpha < 0 → ativo ficou abaixo do benchmark

        CDI e IPCA usam dados do SGS/BCB e composição de taxas mensais.
        IBOV e S&P 500 usam histórico de preços do yfinance.

        Lança ValueError se não houver dados de preço para o período.
        """
        end = end_date or date.today()

        asset_history = await self.get_price_history(ticker, start_date, end)
        if not asset_history.prices:
            raise ValueError(
                f"Sem dados de preço para '{ticker}' entre {start_date} e {end}. "
                f"Verifique se o ticker está correto e se o ativo existia neste período."
            )
        asset_return = self._calculate_return(asset_history.prices)

        if benchmark in _BCB_SERIES_CODES:
            benchmark_return = await self._get_macro_benchmark_return(benchmark, start_date, end)
        else:
            bm_ticker = _BENCHMARK_TICKERS[benchmark]
            bm_history = await self.get_price_history(bm_ticker, start_date, end)
            if not bm_history.prices:
                raise ValueError(f"Sem dados para o benchmark '{benchmark}' no período.")
            benchmark_return = self._calculate_return(bm_history.prices)

        return ReturnComparison(
            ticker=ticker,
            period_start=start_date,
            period_end=end,
            asset_return_pct=asset_return,
            benchmark_return_pct=benchmark_return,
            benchmark_name=benchmark,
            alpha=(asset_return - benchmark_return).quantize(Decimal("0.01")),
        )

    # -----------------------------------------------------------------
    # Cache — gerenciamento
    # -----------------------------------------------------------------

    def invalidate_cache(self, ticker: str | None = None) -> None:
        """
        Invalida entradas do cache.

        ticker=None  → limpa todo o cache (útil ao iniciar/reiniciar o serviço).
        ticker="X"   → remove apenas as entradas relacionadas ao ticker X.
                       Útil após receber um evento corporativo (split, etc.)
                       que tornaria os dados em cache incorretos.
        """
        if ticker is None:
            self._cache.clear()
        else:
            stale_keys = [k for k in self._cache if ticker.upper() in k.upper()]
            for k in stale_keys:
                del self._cache[k]

    # -----------------------------------------------------------------
    # Helpers públicos de conversão
    # -----------------------------------------------------------------

    def to_yfinance_ticker(self, ticker: str) -> str:
        """
        Converte um ticker brasileiro para o formato aceito pelo yfinance.

        Regras:
          • Índices (começa com "^") → sem alteração
          • Já tem "." ou "-" no nome → formato completo, sem alteração
          • 4–6 letras maiúsculas + dígitos finais → ativo B3, adiciona ".SA"
          • Demais → retorna sem alteração (ativo internacional)
        """
        if ticker.startswith("^") or "." in ticker or "-" in ticker:
            return ticker
        # Heurística B3: ticker começa com 4 letras maiúsculas (ex: PETR, HGLG)
        if len(ticker) >= 4 and ticker[:4].isalpha() and ticker[:4].isupper():
            return f"{ticker}.SA"
        return ticker

    # -----------------------------------------------------------------
    # Helpers privados
    # -----------------------------------------------------------------

    def _calculate_return(self, prices: list[Decimal]) -> Decimal:
        """
        Calcula o retorno percentual simples da série de preços.

        Usa primeiro e último elemento — adequado para análise de período
        completo sem considerar o caminho intermediário (retorno ponto a ponto).
        Para retorno ajustado ao risco ou drawdown máximo, use a série completa.
        """
        if len(prices) < 2 or prices[0] == 0:
            return Decimal("0.00")
        return ((prices[-1] / prices[0] - 1) * 100).quantize(Decimal("0.01"))

    async def _get_macro_benchmark_return(
        self,
        benchmark: Literal["CDI", "IPCA"],
        start_date: date,
        end_date: date,
    ) -> Decimal:
        """
        Busca o retorno acumulado de CDI ou IPCA via SGS do Banco Central.

        SGS (Sistema Gerenciador de Séries Temporais) é a API oficial do BCB.
        Séries utilizadas:
          CDI  → código 11: taxa DI diária (% ao dia)
          IPCA → código 433: IPCA mensal (% ao mês)

        Acumulação de taxas periódicas (capitalização composta):
            acumulado = (1 + r₁) × (1 + r₂) × … × (1 + rₙ) − 1

        Se a API do BCB estiver indisponível (timeout, manutenção),
        retorna 0,00 e segue sem crash — o usuário verá "N/D" no benchmark.
        """
        cache_key = f"macro:{benchmark}:{start_date}:{end_date}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        result = Decimal("0.00")
        try:
            from bcb import sgs  # python-bcb — importado aqui para não quebrar se ausente

            series_code = _BCB_SERIES_CODES[benchmark]
            df = sgs.get({benchmark: series_code}, start=start_date, end=end_date)

            if not df.empty:
                rates = df[benchmark].dropna()
                accumulated = 1.0
                for rate in rates:
                    # rates do SGS já vêm em % — divide por 100 para fator decimal
                    accumulated *= 1 + float(rate) / 100
                result = Decimal(str((accumulated - 1) * 100)).quantize(Decimal("0.01"))
        except Exception:
            # API indisponível ou dados ausentes — falha silenciosa com valor neutro
            result = Decimal("0.00")

        self._save_to_cache(cache_key, result)
        return result

    def _get_from_cache(self, key: str) -> object | None:
        """
        Retorna o valor cacheado se ainda estiver dentro do TTL, ou None.

        time.monotonic() é imune a ajustes de horário do sistema (NTP, DST)
        e mais eficiente que datetime.now() para medir intervalos de tempo.
        Entradas expiradas são removidas do dict para não acumular memória.
        """
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.saved_at > self._ttl_seconds:
            del self._cache[key]
            return None
        return entry.value

    def _save_to_cache(self, key: str, value: object) -> None:
        """Armazena um valor no cache com o timestamp atual."""
        self._cache[key] = _CacheEntry(value=value, saved_at=time.monotonic())
