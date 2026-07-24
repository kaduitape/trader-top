import pandas as pd
import pytest

from app.market.regimes import (
    RegimeThresholds,
    Trend,
    VolatilityLevel,
    classify_latest_regime,
    classify_regime_series,
)

_DEFAULT_ROW = {
    "adx_14": 10.0,
    "plus_di_14": 15.0,
    "minus_di_14": 20.0,
    "realized_volatility_20": 1.0,
    "atr_14": 1.0,
    "relative_volume_20": 1.0,
    "avg_spread_20": 10.0,
}


def _make_frame(n: int, **last_row_overrides: float) -> pd.DataFrame:
    rows = [dict(_DEFAULT_ROW) for _ in range(n)]
    rows[-1].update(last_row_overrides)
    return pd.DataFrame(rows)


def test_trend_up_when_adx_strong_and_plus_di_dominant() -> None:
    frame = _make_frame(50, adx_14=40.0, plus_di_14=30.0, minus_di_14=10.0)
    regime = classify_latest_regime(frame)
    assert regime.trend == Trend.UP


def test_trend_down_when_adx_strong_and_minus_di_dominant() -> None:
    frame = _make_frame(50, adx_14=40.0, plus_di_14=10.0, minus_di_14=30.0)
    regime = classify_latest_regime(frame)
    assert regime.trend == Trend.DOWN


def test_trend_sideways_when_adx_below_threshold() -> None:
    frame = _make_frame(50, adx_14=5.0, plus_di_14=50.0, minus_di_14=1.0)
    regime = classify_latest_regime(frame)
    assert regime.trend == Trend.SIDEWAYS


def test_volatility_low_relative_to_baseline() -> None:
    frame = _make_frame(100, realized_volatility_20=0.3)
    regime = classify_latest_regime(frame)
    assert regime.volatility == VolatilityLevel.LOW


def test_volatility_high_relative_to_baseline() -> None:
    frame = _make_frame(100, realized_volatility_20=5.0)
    regime = classify_latest_regime(frame)
    assert regime.volatility == VolatilityLevel.HIGH


def test_volatility_normal_when_close_to_baseline() -> None:
    frame = _make_frame(100)
    regime = classify_latest_regime(frame)
    assert regime.volatility == VolatilityLevel.NORMAL


def test_spread_inadequate_above_threshold() -> None:
    frame = _make_frame(50, avg_spread_20=100.0)
    regime = classify_latest_regime(frame, thresholds=RegimeThresholds(max_spread_points=50.0))
    assert regime.spread_adequate is False


def test_spread_adequate_below_threshold() -> None:
    frame = _make_frame(50, avg_spread_20=10.0)
    regime = classify_latest_regime(frame, thresholds=RegimeThresholds(max_spread_points=50.0))
    assert regime.spread_adequate is True


def test_liquidity_inadequate_below_threshold() -> None:
    frame = _make_frame(50, relative_volume_20=0.1)
    regime = classify_latest_regime(frame, thresholds=RegimeThresholds(min_relative_volume=0.3))
    assert regime.liquidity_adequate is False


def test_liquidity_adequate_above_threshold() -> None:
    frame = _make_frame(50, relative_volume_20=1.0)
    regime = classify_latest_regime(frame, thresholds=RegimeThresholds(min_relative_volume=0.3))
    assert regime.liquidity_adequate is True


def test_transition_detected_when_trend_changes() -> None:
    frame = _make_frame(50, adx_14=40.0, plus_di_14=30.0, minus_di_14=10.0)
    regime = classify_latest_regime(frame)
    assert regime.is_transition is True


def test_no_transition_when_trend_is_stable() -> None:
    frame = _make_frame(50)
    regime = classify_latest_regime(frame)
    assert regime.is_transition is False


def test_extraordinary_event_when_atr_spikes() -> None:
    frame = _make_frame(100, atr_14=10.0)
    regime = classify_latest_regime(frame)
    assert regime.is_extraordinary_event is True


def test_no_extraordinary_event_under_normal_conditions() -> None:
    frame = _make_frame(100)
    regime = classify_latest_regime(frame)
    assert regime.is_extraordinary_event is False


def test_classify_regime_series_returns_one_row_per_input_row() -> None:
    frame = _make_frame(30)
    series = classify_regime_series(frame)
    assert len(series) == 30
    assert list(series.columns) == [
        "trend",
        "volatility",
        "spread_adequate",
        "liquidity_adequate",
        "is_transition",
        "is_extraordinary_event",
    ]


def test_classify_regime_series_raises_when_columns_missing() -> None:
    frame = pd.DataFrame({"adx_14": [10.0]})
    with pytest.raises(ValueError, match="Colunas ausentes"):
        classify_regime_series(frame)


def test_classify_latest_regime_raises_on_empty_frame() -> None:
    frame = _make_frame(1).iloc[0:0]
    with pytest.raises(ValueError, match="vazio"):
        classify_latest_regime(frame)
