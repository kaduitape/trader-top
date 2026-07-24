"""Indicadores tecnicos, implementados internamente (nao delegados a uma
biblioteca de terceiros) para que sejam auditaveis e testaveis linha a
linha, conforme pedido pelo prompt mestre (secao 9: "implementacao propria
dos indicadores criticos para permitir testes").

Todas as funcoes sao vetorizadas com pandas e usam apenas `rolling`/`ewm`/
`shift` (nunca `shift(-n)` ou qualquer operacao que olhe para a frente) —
por construcao, nenhum valor em uma posicao `i` depende de dados em
posicoes `> i`. Isso e verificado explicitamente pelos testes de vazamento
em `tests/unit/market/test_indicators.py`.

O periodo de aquecimento de cada indicador (`window`/`span` barras
iniciais) retorna `NaN` — nunca um valor inventado.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def log_returns(close: pd.Series) -> pd.Series:
    """Retorno logaritmico de 1 barra: ``ln(close[t] / close[t-1])``."""
    return np.log(close / close.shift(1))


def returns_over_window(close: pd.Series, window: int) -> pd.Series:
    """Retorno simples acumulado em `window` barras: ``close[t]/close[t-window] - 1``."""
    return close.pct_change(periods=window)


def sma(series: pd.Series, window: int) -> pd.Series:
    """Media movel simples."""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Media movel exponencial (``adjust=False``, forma recursiva padrao de
    plataformas de trading)."""
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def zscore(series: pd.Series, window: int) -> pd.Series:
    """Distancia da media (em desvios-padrao) usando janela movel:
    ``(x - media_movel) / desvio_padrao_movel``."""
    rolling_mean = series.rolling(window=window, min_periods=window).mean()
    rolling_std = series.rolling(window=window, min_periods=window).std()
    return (series - rolling_mean) / rolling_std


def roc(close: pd.Series, window: int) -> pd.Series:
    """Rate of Change em percentual: ``(close[t] - close[t-window]) / close[t-window] * 100``."""
    shifted = close.shift(window)
    return (close - shifted) / shifted * 100


def momentum(close: pd.Series, window: int) -> pd.Series:
    """Momentum absoluto: ``close[t] - close[t-window]``."""
    return close - close.shift(window)


def _rolling_slope(values: np.ndarray) -> float:
    if np.isnan(values).any():
        return np.nan
    x = np.arange(len(values), dtype=float)
    # Regressao linear simples (grau 1); retorna apenas o coeficiente angular.
    slope_coefficient, _intercept = np.polyfit(x, values, 1)
    return float(slope_coefficient)


def slope(series: pd.Series, window: int) -> pd.Series:
    """Inclinacao (coeficiente angular) de uma regressao linear simples
    sobre as ultimas `window` barras da serie — usada para medir a
    inclinacao de medias moveis."""
    return series.rolling(window=window, min_periods=window).apply(_rolling_slope, raw=True)


def realized_volatility(returns: pd.Series, window: int) -> pd.Series:
    """Volatilidade realizada: desvio-padrao movel dos retornos log."""
    return returns.rolling(window=window, min_periods=window).std()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """RSI de Wilder. `avg_gain`/`avg_loss` usam suavizacao exponencial de
    Wilder (``alpha = 1/window``), a forma classica do indicador."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()

    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))
    # Quando avg_loss == 0 (serie so de altas), RS diverge para infinito e
    # o RSI deve saturar em 100, nao virar NaN.
    result = result.where(avg_loss != 0, 100.0)
    return result


@dataclass(frozen=True, slots=True)
class MacdResult:
    macd_line: pd.Series
    signal_line: pd.Series
    histogram: pd.Series


def macd(close: pd.Series, *, fast: int = 12, slow: int = 26, signal: int = 9) -> MacdResult:
    """MACD classico: EMA rapida menos EMA lenta, com linha de sinal (EMA
    do MACD) e histograma (MACD menos sinal)."""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return MacdResult(macd_line=macd_line, signal_line=signal_line, histogram=histogram)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range: ``max(high-low, |high-close_anterior|, |low-close_anterior|)``."""
    previous_close = close.shift(1)
    return pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range, com suavizacao de Wilder (``alpha = 1/window``)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


@dataclass(frozen=True, slots=True)
class AdxResult:
    plus_di: pd.Series
    minus_di: pd.Series
    adx: pd.Series


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> AdxResult:
    """ADX (Average Directional Index) de Wilder, com +DI/-DI.

    Mede a forca de uma tendencia (nao a direcao — a direcao vem do sinal
    de `plus_di - minus_di`). Usado por `app.market.regimes` para
    classificar tendencia forte vs mercado lateral."""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr = true_range(high, low, close)
    smoothed_tr = tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    smoothed_plus_dm = plus_dm.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()

    plus_di = 100 * (smoothed_plus_dm / smoothed_tr)
    minus_di = 100 * (smoothed_minus_dm / smoothed_tr)

    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    dx = dx.where(di_sum != 0, 0.0)
    adx_line = dx.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()

    return AdxResult(plus_di=plus_di, minus_di=minus_di, adx=adx_line)


@dataclass(frozen=True, slots=True)
class BollingerBands:
    middle: pd.Series
    upper: pd.Series
    lower: pd.Series


def bollinger_bands(close: pd.Series, *, window: int = 20, num_std: float = 2.0) -> BollingerBands:
    """Bandas de Bollinger: media movel simples +/- `num_std` desvios-padrao
    moveis."""
    middle = sma(close, window)
    std = close.rolling(window=window, min_periods=window).std()
    return BollingerBands(middle=middle, upper=middle + num_std * std, lower=middle - num_std * std)


@dataclass(frozen=True, slots=True)
class SuperTrendResult:
    line: pd.Series
    trend: pd.Series


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    *,
    atr_window: int = 10,
    multiplier: float = 3.0,
) -> SuperTrendResult:
    """SuperTrend classico (ATR + bandas com "trava" de banda final).

    Path-dependent por construcao (a banda final e a tendencia de uma barra
    dependem da barra anterior) — por isso, diferente das demais funcoes
    deste modulo, NAO usa `rolling().apply` (que trata cada janela como
    independente): e um loop explicito sobre arrays ja calculados
    (`atr`/bandas basicas), mesmo cuidado ja usado em
    `app.market.features._candle_streak`. `trend` vale `+1.0` (alta,
    `line` = banda inferior) ou `-1.0` (baixa, `line` = banda superior);
    ambos ficam `NaN` durante o aquecimento do ATR."""
    atr_series = atr(high, low, close, atr_window)
    hl2 = (high + low) / 2
    basic_upper = (hl2 + multiplier * atr_series).to_numpy()
    basic_lower = (hl2 - multiplier * atr_series).to_numpy()
    atr_arr = atr_series.to_numpy()
    close_arr = close.to_numpy()

    n = len(close)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    line = np.full(n, np.nan)
    trend = np.full(n, np.nan)

    started = False
    for i in range(n):
        if np.isnan(atr_arr[i]):
            continue
        if not started:
            final_upper[i] = basic_upper[i]
            final_lower[i] = basic_lower[i]
            trend[i] = 1.0 if close_arr[i] <= final_upper[i] else -1.0
            line[i] = final_lower[i] if trend[i] == 1.0 else final_upper[i]
            started = True
            continue

        prev = i - 1
        final_upper[i] = (
            basic_upper[i]
            if (basic_upper[i] < final_upper[prev] or close_arr[prev] > final_upper[prev])
            else final_upper[prev]
        )
        final_lower[i] = (
            basic_lower[i]
            if (basic_lower[i] > final_lower[prev] or close_arr[prev] < final_lower[prev])
            else final_lower[prev]
        )

        if close_arr[i] > final_upper[prev]:
            trend[i] = 1.0
        elif close_arr[i] < final_lower[prev]:
            trend[i] = -1.0
        else:
            trend[i] = trend[prev]
        line[i] = final_lower[i] if trend[i] == 1.0 else final_upper[i]

    return SuperTrendResult(
        line=pd.Series(line, index=close.index), trend=pd.Series(trend, index=close.index)
    )


def average_daily_range(
    daily_high: pd.Series, daily_low: pd.Series, *, window: int = 14
) -> pd.Series:
    """ADR (Average Daily Range): media movel do range (high-low) de candles
    DIARIAS (D1). `daily_high`/`daily_low` devem vir de uma serie D1 — usar
    com candles intradiarias produz um numero sem sentido (nao e "range
    medio do dia", vira "range medio da barra", uma metrica diferente)."""
    daily_range = daily_high - daily_low
    return daily_range.rolling(window=window, min_periods=window).mean()
