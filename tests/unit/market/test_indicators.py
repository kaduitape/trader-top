import numpy as np
import pandas as pd
import pytest

from app.market import indicators


def test_log_returns_matches_formula() -> None:
    close = pd.Series([100.0, 110.0, 99.0])
    result = indicators.log_returns(close)

    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(np.log(110.0 / 100.0))
    assert result.iloc[2] == pytest.approx(np.log(99.0 / 110.0))


def test_returns_over_window() -> None:
    close = pd.Series([100.0, 101.0, 102.0, 110.0])
    result = indicators.returns_over_window(close, window=3)

    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[2])
    assert result.iloc[3] == pytest.approx(110.0 / 100.0 - 1)


def test_sma_matches_manual_average() -> None:
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = indicators.sma(series, window=3)

    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[3] == pytest.approx(3.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_ema_matches_recursive_formula() -> None:
    series = pd.Series([1.0, 2.0, 3.0])
    result = indicators.ema(series, span=2)
    alpha = 2 / (2 + 1)

    expected_1 = alpha * 2.0 + (1 - alpha) * 1.0
    expected_2 = alpha * 3.0 + (1 - alpha) * expected_1

    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(expected_1)
    assert result.iloc[2] == pytest.approx(expected_2)


def test_ema_does_not_leak_future_values() -> None:
    series = pd.Series([1.0, 2.0, 3.0, 4.0])
    baseline = indicators.ema(series, span=2)

    mutated = series.copy()
    mutated.iloc[-1] = 999.0
    mutated_result = indicators.ema(mutated, span=2)

    assert mutated_result.iloc[:-1].equals(baseline.iloc[:-1])


def test_zscore_is_nan_when_series_is_constant() -> None:
    series = pd.Series([5.0] * 25)
    result = indicators.zscore(series, window=20)

    assert np.isnan(result.iloc[-1])


def test_zscore_matches_manual_computation() -> None:
    rng = np.random.default_rng(42)
    values = rng.normal(loc=100, scale=2, size=30)
    series = pd.Series(values)
    window = 20

    result = indicators.zscore(series, window=window)

    tail_window = values[-window:]
    expected = (values[-1] - tail_window.mean()) / tail_window.std(ddof=1)
    assert result.iloc[-1] == pytest.approx(expected)


def test_roc_matches_formula() -> None:
    close = pd.Series([100.0, 105.0, 90.0, 120.0])
    result = indicators.roc(close, window=2)

    assert result.iloc[2] == pytest.approx((90.0 - 100.0) / 100.0 * 100)
    assert result.iloc[3] == pytest.approx((120.0 - 105.0) / 105.0 * 100)


def test_momentum_matches_formula() -> None:
    close = pd.Series([100.0, 105.0, 90.0, 120.0])
    result = indicators.momentum(close, window=2)

    assert result.iloc[2] == pytest.approx(90.0 - 100.0)
    assert result.iloc[3] == pytest.approx(120.0 - 105.0)


def test_slope_of_perfectly_linear_series_is_one() -> None:
    series = pd.Series(np.arange(30, dtype=float))
    result = indicators.slope(series, window=20)

    assert result.iloc[19] == pytest.approx(1.0)
    assert result.iloc[29] == pytest.approx(1.0)
    assert result.iloc[:19].isna().all()


def test_slope_does_not_leak_future_values() -> None:
    series = pd.Series(np.concatenate([np.arange(25, dtype=float), [1000.0]]))
    baseline = indicators.slope(series, window=20)

    mutated = series.copy()
    mutated.iloc[-1] = -1000.0
    mutated_result = indicators.slope(mutated, window=20)

    assert mutated_result.iloc[:-1].equals(baseline.iloc[:-1])


def test_realized_volatility_is_zero_for_constant_returns() -> None:
    close = pd.Series([100.0 * (1.01**i) for i in range(25)])
    returns = indicators.log_returns(close)
    result = indicators.realized_volatility(returns, window=20)

    assert result.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_is_100_for_strictly_increasing_series() -> None:
    close = pd.Series(np.arange(1, 30, dtype=float))
    result = indicators.rsi(close, window=14)

    assert result.iloc[20:].apply(lambda v: v == pytest.approx(100.0)).all()


def test_rsi_is_zero_for_strictly_decreasing_series() -> None:
    close = pd.Series(np.arange(30, 1, -1, dtype=float))
    result = indicators.rsi(close, window=14)

    assert result.iloc[20:].apply(lambda v: v == pytest.approx(0.0)).all()


def test_macd_is_flat_for_constant_series() -> None:
    close = pd.Series([50.0] * 60)
    result = indicators.macd(close)

    assert result.macd_line.iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert result.signal_line.iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert result.histogram.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_atr_converges_to_constant_true_range() -> None:
    n = 30
    high = pd.Series([5.0] * n)
    low = pd.Series([4.0] * n)
    close = pd.Series([4.5] * n)

    result = indicators.atr(high, low, close, window=14)

    assert result.iloc[:13].isna().all()
    assert result.iloc[13:].apply(lambda v: v == pytest.approx(1.0)).all()


def test_bollinger_bands_collapse_for_constant_series() -> None:
    close = pd.Series([10.0] * 25)
    bands = indicators.bollinger_bands(close, window=20, num_std=2.0)

    assert bands.middle.iloc[-1] == pytest.approx(10.0)
    assert bands.upper.iloc[-1] == pytest.approx(10.0)
    assert bands.lower.iloc[-1] == pytest.approx(10.0)


def test_adx_is_high_for_sustained_uptrend_and_low_for_sideways_market() -> None:
    n = 60
    up_low = pd.Series(np.arange(n, dtype=float) + 10)
    up_high = up_low + 1
    up_close = up_low + 0.5
    uptrend_adx = indicators.adx(up_high, up_low, up_close, window=14)

    assert uptrend_adx.adx.iloc[-1] > 25
    assert uptrend_adx.plus_di.iloc[-1] > uptrend_adx.minus_di.iloc[-1]

    oscillation = np.tile([0.0, 1.0], n // 2)
    side_low = pd.Series(oscillation + 10)
    side_high = side_low + 1
    side_close = side_low + 0.5
    sideways_adx = indicators.adx(side_high, side_low, side_close, window=14)

    assert sideways_adx.adx.iloc[-1] < uptrend_adx.adx.iloc[-1]


def _flat_series(n: int, price: float = 100.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    close = pd.Series([price] * n)
    return close, close, close


def test_supertrend_flat_market_never_flips() -> None:
    high, low, close = _flat_series(30)
    result = indicators.supertrend(high, low, close, atr_window=10, multiplier=3.0)

    valid_trend = result.trend.dropna().to_numpy()
    assert len(valid_trend) > 0
    assert (valid_trend == valid_trend[0]).all()


def test_supertrend_flips_exactly_once_on_a_real_reversal() -> None:
    up = np.arange(40, dtype=float) + 100.0
    down = up[-1] - np.arange(1, 41, dtype=float)
    close = pd.Series(np.concatenate([up, down]))
    high = close + 1.0
    low = close - 1.0

    result = indicators.supertrend(high, low, close, atr_window=10, multiplier=3.0)
    valid_trend = result.trend.dropna().to_numpy()

    flips = int((np.diff(valid_trend) != 0).sum())
    assert flips == 1
    assert valid_trend[0] == 1.0
    assert valid_trend[-1] == -1.0


def test_supertrend_does_not_leak_future_values() -> None:
    up = np.arange(40, dtype=float) + 100.0
    down = up[-1] - np.arange(1, 41, dtype=float)
    close = pd.Series(np.concatenate([up, down]))
    high = close + 1.0
    low = close - 1.0

    full = indicators.supertrend(high, low, close, atr_window=10, multiplier=3.0)
    truncated = indicators.supertrend(
        high.iloc[:50], low.iloc[:50], close.iloc[:50], atr_window=10, multiplier=3.0
    )

    pd.testing.assert_series_equal(truncated.line, full.line.iloc[:50])
    pd.testing.assert_series_equal(truncated.trend, full.trend.iloc[:50])


def test_average_daily_range_hand_computed() -> None:
    high = pd.Series([110.0, 112.0, 108.0, 114.0, 109.0])
    low = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0])

    result = indicators.average_daily_range(high, low, window=3)

    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == pytest.approx((10.0 + 12.0 + 8.0) / 3)
    assert result.iloc[3] == pytest.approx((12.0 + 8.0 + 14.0) / 3)
    assert result.iloc[4] == pytest.approx((8.0 + 14.0 + 9.0) / 3)
