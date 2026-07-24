"""Matriz de features derivadas de candles.

Cada feature e documentada em `FEATURE_CATALOG` (formula, janela, atraso,
risco de vazamento, tratamento de nulo, custo computacional) — exigencia
explicita do prompt mestre (secao 9): "Nao crie centenas de features sem
controle", por isso o conjunto abaixo e deliberadamente restrito ao que tem
justificativa clara. Ver `docs/features.md` para a versao em prosa deste
catalogo.

Processamento incremental: nenhuma feature aqui precisa da historia
completa do simbolo — `required_lookback_bars()` informa quantas barras
anteriores bastam para que a ultima linha da matriz seja valida (o maior
`window`/`span` usado). Uma coleta incremental (Fase 3) so precisa manter
esse numero de barras mais as novas para recalcular a matriz corretamente;
nao ha necessidade de reprocessar todo o historico a cada nova barra. Um
motor verdadeiramente streaming (atualizacao O(1) por barra sem reprocessar
a janela) fica para quando a Fase 10+ (paper trading) exigir latencia
sub-barra — nao ha essa exigencia ainda.

Deliberadamente fora do escopo desta fase (justificativas individuais):
- Features de microestrutura de tick (order flow imbalance, microprice,
  velocidade/direcao de tick, desequilibrio bid/ask): pertencem a Estrategia
  H (microestrutura, Fase 6) e exigem o modulo de livro de ofertas.
- Proximidade de noticia: depende do calendario economico (Fase 19).
- Correlacao com ativos relacionados: exige alinhamento multi-simbolo e
  configuracao de quais ativos correlacionar; sem uso concreto ainda.
- Tendencia do timeframe superior: exige juntar duas series de timeframes
  diferentes; sera adicionada quando uma estrategia multi-timeframe
  (Estrategia I, Fase 6) precisar dela.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import numpy as np
import pandas as pd

from app.market import indicators

EMA_PERIODS = (9, 21, 50, 200)
_RSI_WINDOW = 14
_ATR_WINDOW = 14
_ADX_WINDOW = 14
_BOLLINGER_WINDOW = 20
_ZSCORE_WINDOW = 20
_ROC_WINDOW = 10
_MOMENTUM_WINDOW = 10
_SLOPE_WINDOW = 20
_VOLATILITY_WINDOW = 20
_VOLUME_WINDOW = 20
_SPREAD_WINDOW = 20
_VWAP_WINDOW = 20


class CandleFeatureLike(Protocol):
    """Somente leitura — ver `app.market.data_quality.CandleLike` para a
    explicacao de por que as propriedades (nao atributos simples) sao
    necessarias para aceitar tanto `RawCandle` (float) quanto `Candle`
    (Decimal) covariantemente."""

    @property
    def open_time(self) -> datetime: ...
    @property
    def open(self) -> float | Decimal: ...
    @property
    def high(self) -> float | Decimal: ...
    @property
    def low(self) -> float | Decimal: ...
    @property
    def close(self) -> float | Decimal: ...
    @property
    def tick_volume(self) -> int: ...
    @property
    def spread(self) -> int: ...


class Session(enum.StrEnum):
    """Heuristica aproximada por horario UTC — nao usa calendario de
    feriados nem horario de verao. Suficiente para uma feature de contexto
    macro; refinar quando uma estrategia especifica (Estrategia G, abertura
    de sessao) precisar de precisao maior."""

    ASIA = "ASIA"
    LONDON = "LONDON"
    LONDON_NY_OVERLAP = "LONDON_NY_OVERLAP"
    NEW_YORK = "NEW_YORK"


def _session_for_hour(hour_utc: int) -> Session:
    if 7 <= hour_utc < 12:
        return Session.LONDON
    if 12 <= hour_utc < 16:
        return Session.LONDON_NY_OVERLAP
    if 16 <= hour_utc < 22:
        return Session.NEW_YORK
    return Session.ASIA


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    formula: str
    window: int | None
    delay_bars: int
    leakage_risk: str
    null_handling: str
    computational_cost: str
    observed_importance: str = "Nao avaliada (nenhum modelo treinado ainda — Fase 8)."


FEATURE_CATALOG: list[FeatureSpec] = [
    FeatureSpec(
        name="log_return",
        formula="ln(close[t] / close[t-1])",
        window=1,
        delay_bars=1,
        leakage_risk="Nenhum — usa apenas close[t] e close[t-1].",
        null_handling="NaN na primeira barra.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="return_10",
        formula="close[t]/close[t-10] - 1",
        window=10,
        delay_bars=10,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras 10 barras.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="ema_9, ema_21, ema_50, ema_200",
        formula="EMA recursiva (adjust=False), span=9/21/50/200",
        window=None,
        delay_bars=200,
        leakage_risk="Nenhum — recursiva, so depende do passado.",
        null_handling="NaN ate `span` barras (min_periods=span).",
        computational_cost="O(n) por EMA.",
    ),
    FeatureSpec(
        name="dist_ema_9, dist_ema_21, dist_ema_50, dist_ema_200",
        formula="(close - ema) / close * 100",
        window=None,
        delay_bars=200,
        leakage_risk="Nenhum (deriva das EMAs acima).",
        null_handling="NaN enquanto a EMA correspondente for NaN.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="ema_21_slope",
        formula="coeficiente angular de regressao linear sobre as ultimas 20 barras da EMA21",
        window=_SLOPE_WINDOW,
        delay_bars=21 + _SLOPE_WINDOW,
        leakage_risk="Nenhum — janela estritamente historica (rolling.apply sem shift negativo).",
        null_handling="NaN ate a janela completa (EMA21 valida + 20 barras).",
        computational_cost="O(n*window) — regressao por janela; aceitavel para uso nao tempo-real.",
    ),
    FeatureSpec(
        name="rsi_14",
        formula="RSI de Wilder, alpha=1/14",
        window=_RSI_WINDOW,
        delay_bars=_RSI_WINDOW,
        leakage_risk="Nenhum — EWM recursiva, so passado.",
        null_handling="NaN nas primeiras 14 barras.",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="macd_line, macd_signal, macd_histogram",
        formula="EMA12 - EMA26; sinal = EMA9 do MACD; histograma = MACD - sinal",
        window=None,
        delay_bars=26 + 9,
        leakage_risk="Nenhum.",
        null_handling="NaN ate a EMA26 (e depois EMA9 do MACD) ficarem validas.",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="atr_14",
        formula="EWM(True Range, alpha=1/14)",
        window=_ATR_WINDOW,
        delay_bars=_ATR_WINDOW + 1,
        leakage_risk="Nenhum — True Range usa close[t-1], nunca close[t+1].",
        null_handling="NaN nas primeiras 14 barras.",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="adx_14, plus_di_14, minus_di_14",
        formula="ADX de Wilder sobre +DM/-DM suavizados pelo True Range",
        window=_ADX_WINDOW,
        delay_bars=2 * _ADX_WINDOW,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras ~28 barras (dupla suavizacao).",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="bollinger_upper, bollinger_middle, bollinger_lower",
        formula="SMA(20) +/- 2 * desvio-padrao movel(20)",
        window=_BOLLINGER_WINDOW,
        delay_bars=_BOLLINGER_WINDOW,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras 20 barras.",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="zscore_20",
        formula="(close - media_movel_20) / desvio_padrao_movel_20",
        window=_ZSCORE_WINDOW,
        delay_bars=_ZSCORE_WINDOW,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras 20 barras; NaN/inf se desvio-padrao for 0 (mercado sem movimento).",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="roc_10",
        formula="(close[t] - close[t-10]) / close[t-10] * 100",
        window=_ROC_WINDOW,
        delay_bars=_ROC_WINDOW,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras 10 barras.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="momentum_10",
        formula="close[t] - close[t-10]",
        window=_MOMENTUM_WINDOW,
        delay_bars=_MOMENTUM_WINDOW,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras 10 barras.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="realized_volatility_20",
        formula="desvio-padrao movel(20) do log_return",
        window=_VOLATILITY_WINDOW,
        delay_bars=_VOLATILITY_WINDOW + 1,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras ~21 barras.",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="vwap_20, dist_vwap_20",
        formula=(
            "VWAP aproximado por janela movel (nao e VWAP de sessao real): "
            "soma(preco_tipico*volume, 20) / soma(volume, 20); "
            "dist = (close - vwap)/vwap*100"
        ),
        window=_VWAP_WINDOW,
        delay_bars=_VWAP_WINDOW,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras 20 barras; NaN se volume acumulado for 0.",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="candle_amplitude, candle_body, candle_upper_wick, candle_lower_wick",
        formula="high-low; |close-open|; high-max(open,close); min(open,close)-low",
        window=1,
        delay_bars=0,
        leakage_risk="Nenhum — usa apenas dados da propria barra (ja fechada quando processada).",
        null_handling="Nunca nulo (candle sempre tem OHLC).",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="candle_streak",
        formula="contagem de candles consecutivas na mesma direcao (positivo=alta, negativo=baixa)",
        window=None,
        delay_bars=0,
        leakage_risk="Nenhum — depende apenas de barras passadas e da atual.",
        null_handling="Nunca nulo.",
        computational_cost="O(n).",
    ),
    FeatureSpec(
        name="relative_volume_20",
        formula="tick_volume[t] / media_movel_20(tick_volume)",
        window=_VOLUME_WINDOW,
        delay_bars=_VOLUME_WINDOW,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras 20 barras.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="volume_acceleration",
        formula="(tick_volume[t] - tick_volume[t-1]) / tick_volume[t-1]",
        window=1,
        delay_bars=1,
        leakage_risk="Nenhum.",
        null_handling="NaN na primeira barra; NaN/inf se volume anterior for 0.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="avg_spread_20, spread_variation_20",
        formula="media/desvio-padrao movel(20) do campo `spread` (pontos) da candle",
        window=_SPREAD_WINDOW,
        delay_bars=_SPREAD_WINDOW,
        leakage_risk="Nenhum.",
        null_handling="NaN nas primeiras 20 barras.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="relative_spread_bps",
        formula="spread_pontos * point / close * 10000 (basis points)",
        window=1,
        delay_bars=0,
        leakage_risk="Nenhum.",
        null_handling="NaN se `point` nao for fornecido a `build_candle_features`.",
        computational_cost="O(n), trivial.",
    ),
    FeatureSpec(
        name="hour_utc, minute_of_day, day_of_week, session",
        formula="derivados diretos de open_time (UTC)",
        window=None,
        delay_bars=0,
        leakage_risk="Nenhum — sao metadados do proprio timestamp da barra.",
        null_handling="Nunca nulo.",
        computational_cost="O(n), trivial.",
    ),
]


def required_lookback_bars() -> int:
    """Numero minimo de barras anteriores necessarias para que a ultima
    linha da matriz de features seja valida (nao-NaN). Usado para decidir
    quantas candles buscar do banco ao processar incrementalmente."""
    return max(spec.delay_bars for spec in FEATURE_CATALOG)


def _candles_to_frame(candles: Sequence[CandleFeatureLike]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [c.open_time for c in candles],
            "open": [float(c.open) for c in candles],
            "high": [float(c.high) for c in candles],
            "low": [float(c.low) for c in candles],
            "close": [float(c.close) for c in candles],
            "tick_volume": [int(c.tick_volume) for c in candles],
            "spread": [int(c.spread) for c in candles],
        }
    )


def _candle_streak(close: pd.Series, open_: pd.Series) -> pd.Series:
    direction = np.sign(close.to_numpy() - open_.to_numpy())
    streak = np.zeros(len(direction))
    current = 0.0
    for i, d in enumerate(direction):
        if d == 0:
            current = 0.0
        elif current != 0 and np.sign(current) == d:
            current += d
        else:
            current = d
        streak[i] = current
    return pd.Series(streak, index=close.index)


def build_candle_features(
    candles: Sequence[CandleFeatureLike], *, point: float | None = None
) -> pd.DataFrame:
    """Constroi a matriz de features documentada em `FEATURE_CATALOG` a
    partir de uma serie de candles ordenada por `open_time` crescente.

    `point` (tamanho do menor incremento de preco do simbolo) e opcional;
    sem ele, `relative_spread_bps` fica `NaN` (documentado no catalogo)."""
    frame = _candles_to_frame(candles)
    close, open_, high, low = frame["close"], frame["open"], frame["high"], frame["low"]
    tick_volume, spread = frame["tick_volume"], frame["spread"]

    out = pd.DataFrame(index=frame.index)
    out["open_time"] = frame["open_time"]
    out["open"] = open_
    out["high"] = high
    out["low"] = low
    out["close"] = close

    out["log_return"] = indicators.log_returns(close)
    out["return_10"] = indicators.returns_over_window(close, _ROC_WINDOW)

    ema_series: dict[int, pd.Series] = {}
    for period in EMA_PERIODS:
        ema_series[period] = indicators.ema(close, period)
        out[f"ema_{period}"] = ema_series[period]
        out[f"dist_ema_{period}"] = (close - ema_series[period]) / close * 100

    out["ema_21_slope"] = indicators.slope(ema_series[21], _SLOPE_WINDOW)

    out["rsi_14"] = indicators.rsi(close, _RSI_WINDOW)

    macd_result = indicators.macd(close)
    out["macd_line"] = macd_result.macd_line
    out["macd_signal"] = macd_result.signal_line
    out["macd_histogram"] = macd_result.histogram

    out["atr_14"] = indicators.atr(high, low, close, _ATR_WINDOW)

    adx_result = indicators.adx(high, low, close, _ADX_WINDOW)
    out["adx_14"] = adx_result.adx
    out["plus_di_14"] = adx_result.plus_di
    out["minus_di_14"] = adx_result.minus_di

    bands = indicators.bollinger_bands(close, window=_BOLLINGER_WINDOW)
    out["bollinger_upper"] = bands.upper
    out["bollinger_middle"] = bands.middle
    out["bollinger_lower"] = bands.lower

    out["zscore_20"] = indicators.zscore(close, _ZSCORE_WINDOW)
    out["roc_10"] = indicators.roc(close, _ROC_WINDOW)
    out["momentum_10"] = indicators.momentum(close, _MOMENTUM_WINDOW)
    out["realized_volatility_20"] = indicators.realized_volatility(
        out["log_return"], _VOLATILITY_WINDOW
    )

    typical_price = (high + low + close) / 3
    rolling_pv = (
        (typical_price * tick_volume).rolling(window=_VWAP_WINDOW, min_periods=_VWAP_WINDOW).sum()
    )
    rolling_volume = tick_volume.rolling(window=_VWAP_WINDOW, min_periods=_VWAP_WINDOW).sum()
    vwap = rolling_pv / rolling_volume.replace(0, np.nan)
    out["vwap_20"] = vwap
    out["dist_vwap_20"] = (close - vwap) / vwap * 100

    out["candle_amplitude"] = high - low
    out["candle_body"] = (close - open_).abs()
    out["candle_upper_wick"] = high - pd.concat([open_, close], axis=1).max(axis=1)
    out["candle_lower_wick"] = pd.concat([open_, close], axis=1).min(axis=1) - low
    out["candle_streak"] = _candle_streak(close, open_)

    rolling_volume_mean = tick_volume.rolling(
        window=_VOLUME_WINDOW, min_periods=_VOLUME_WINDOW
    ).mean()
    out["relative_volume_20"] = tick_volume / rolling_volume_mean
    out["volume_acceleration"] = tick_volume.pct_change(periods=1)

    out["avg_spread_20"] = spread.rolling(window=_SPREAD_WINDOW, min_periods=_SPREAD_WINDOW).mean()
    out["spread_variation_20"] = spread.rolling(
        window=_SPREAD_WINDOW, min_periods=_SPREAD_WINDOW
    ).std()
    if point is not None and point > 0:
        out["relative_spread_bps"] = spread * point / close * 10_000
    else:
        out["relative_spread_bps"] = np.nan

    out["hour_utc"] = frame["open_time"].dt.hour
    out["minute_of_day"] = frame["open_time"].dt.hour * 60 + frame["open_time"].dt.minute
    out["day_of_week"] = frame["open_time"].dt.weekday
    out["session"] = out["hour_utc"].apply(_session_for_hour).astype(str)

    return out
