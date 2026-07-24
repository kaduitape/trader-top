from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.market.features import (
    FEATURE_CATALOG,
    Session,
    build_candle_features,
    required_lookback_bars,
)
from app.mt5.market_data import RawCandle

_START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # segunda-feira, meia-noite UTC


def _make_candles(n: int, *, seed: int = 7, start_hour: int = 0) -> list[RawCandle]:
    rng = np.random.default_rng(seed)
    start = _START.replace(hour=start_hour)
    price = 100.0
    candles: list[RawCandle] = []
    for i in range(n):
        step = rng.normal(0, 0.05)
        open_ = price
        close = price + step
        high = max(open_, close) + abs(rng.normal(0, 0.02))
        low = min(open_, close) - abs(rng.normal(0, 0.02))
        volume = int(100 + rng.integers(0, 50))
        spread = int(2 + rng.integers(0, 3))
        candles.append(
            RawCandle(
                open_time=start + timedelta(minutes=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                tick_volume=volume,
                spread=spread,
                real_volume=0,
            )
        )
        price = close
    return candles


def test_required_lookback_bars_is_max_catalog_delay() -> None:
    assert required_lookback_bars() == max(spec.delay_bars for spec in FEATURE_CATALOG)
    assert required_lookback_bars() == 200


def test_build_candle_features_has_expected_columns() -> None:
    candles = _make_candles(30)
    frame = build_candle_features(candles, point=0.00001)

    expected_columns = {
        "open_time",
        "log_return",
        "return_10",
        "ema_9",
        "ema_21",
        "ema_50",
        "ema_200",
        "dist_ema_9",
        "dist_ema_21",
        "dist_ema_50",
        "dist_ema_200",
        "ema_21_slope",
        "rsi_14",
        "macd_line",
        "macd_signal",
        "macd_histogram",
        "atr_14",
        "adx_14",
        "plus_di_14",
        "minus_di_14",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
        "zscore_20",
        "roc_10",
        "momentum_10",
        "realized_volatility_20",
        "vwap_20",
        "dist_vwap_20",
        "candle_amplitude",
        "candle_body",
        "candle_upper_wick",
        "candle_lower_wick",
        "candle_streak",
        "relative_volume_20",
        "volume_acceleration",
        "avg_spread_20",
        "spread_variation_20",
        "relative_spread_bps",
        "hour_utc",
        "minute_of_day",
        "day_of_week",
        "session",
    }
    assert expected_columns <= set(frame.columns)
    assert len(frame) == 30


def test_build_candle_features_is_reproducible() -> None:
    candles = _make_candles(220)
    first = build_candle_features(candles, point=0.00001)
    second = build_candle_features(candles, point=0.00001)

    pd.testing.assert_frame_equal(first, second)


def test_build_candle_features_does_not_leak_future_values() -> None:
    candles = _make_candles(220)
    baseline = build_candle_features(candles, point=0.00001)

    mutated = list(candles)
    mutated[-1] = RawCandle(
        open_time=mutated[-1].open_time,
        open=mutated[-1].open,
        high=9999.0,
        low=9999.0,
        close=9999.0,
        tick_volume=999_999,
        spread=999,
        real_volume=0,
    )
    mutated_frame = build_candle_features(mutated, point=0.00001)

    pd.testing.assert_frame_equal(baseline.iloc[:-1], mutated_frame.iloc[:-1])


def test_never_null_candle_shape_features_even_on_first_row() -> None:
    candles = _make_candles(5)
    frame = build_candle_features(candles, point=0.00001)

    for column in ("candle_amplitude", "candle_body", "candle_upper_wick", "candle_lower_wick"):
        assert frame[column].notna().all()


def test_ema_200_is_nan_when_history_is_too_short() -> None:
    candles = _make_candles(5)
    frame = build_candle_features(candles, point=0.00001)

    assert frame["ema_200"].isna().all()
    assert frame["dist_ema_200"].isna().all()


def test_relative_spread_requires_point() -> None:
    candles = _make_candles(10)

    without_point = build_candle_features(candles, point=None)
    assert without_point["relative_spread_bps"].isna().all()

    with_point = build_candle_features(candles, point=0.00001)
    assert with_point["relative_spread_bps"].notna().all()


@pytest.mark.parametrize(
    ("hour", "expected_session"),
    [
        (2, Session.ASIA),
        (9, Session.LONDON),
        (14, Session.LONDON_NY_OVERLAP),
        (18, Session.NEW_YORK),
        (23, Session.ASIA),
    ],
)
def test_session_bucket_by_hour(hour: int, expected_session: Session) -> None:
    candles = _make_candles(3, start_hour=hour)
    frame = build_candle_features(candles, point=0.00001)

    assert frame["hour_utc"].iloc[0] == hour
    assert frame["session"].iloc[0] == expected_session.value
