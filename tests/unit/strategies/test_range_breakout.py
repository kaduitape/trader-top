import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.market.regimes import MarketRegime, Trend, VolatilityLevel
from app.strategies.base import MarketState, SignalDirection
from app.strategies.breakout.range_breakout import RangeBreakoutConfig, RangeBreakoutStrategy

_T0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
_POINT = 0.00001
_REQUIRED_BARS = RangeBreakoutConfig().range_window + RangeBreakoutConfig().compression_window + 1


def _regime(*, spread_adequate: bool = True) -> MarketRegime:
    return MarketRegime(
        trend=Trend.SIDEWAYS,
        volatility=VolatilityLevel.NORMAL,
        spread_adequate=spread_adequate,
        liquidity_adequate=True,
        is_transition=False,
        is_extraordinary_event=False,
    )


def _rows(n: int, **last_overrides: object) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": _T0 + timedelta(minutes=i),
                "close": 1.1000,
                "high": 1.1010,
                "low": 1.0990,
                "atr_14": 0.0005,
                "relative_volume_20": 1.0,
                "bollinger_upper": 1.1005,
                "bollinger_lower": 1.0995,
                "bollinger_middle": 1.1000,
            }
        )
    rows[-1].update(last_overrides)
    return rows


def _state(rows: list[dict], regime: MarketRegime | None) -> MarketState:
    return MarketState(symbol="EURUSD", timeframe="M1", features=pd.DataFrame(rows), regime=regime)


def _strategy(**overrides: object) -> RangeBreakoutStrategy:
    config = RangeBreakoutConfig(**overrides) if overrides else RangeBreakoutConfig()
    return RangeBreakoutStrategy(config, point=_POINT, bar_seconds=60)


def test_no_signal_with_insufficient_history() -> None:
    rows = _rows(_REQUIRED_BARS - 1, close=1.1020, high=1.1025, low=1.1000, relative_volume_20=2.0)
    state = _state(rows, _regime())
    assert _strategy().generate_signal(state) is None


def test_no_signal_without_regime() -> None:
    rows = _rows(_REQUIRED_BARS, close=1.1020, high=1.1025, low=1.1000, relative_volume_20=2.0)
    state = _state(rows, None)
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_spread_inadequate() -> None:
    rows = _rows(_REQUIRED_BARS, close=1.1020, high=1.1025, low=1.1000, relative_volume_20=2.0)
    state = _state(rows, _regime(spread_adequate=False))
    assert _strategy().generate_signal(state) is None


def test_long_signal_on_valid_upward_breakout() -> None:
    rows = _rows(_REQUIRED_BARS, close=1.1020, high=1.1025, low=1.1000, relative_volume_20=2.0)
    state = _state(rows, _regime())
    signal = _strategy().generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.reference_price == pytest.approx(1.1020)
    assert signal.stop_loss < signal.reference_price < signal.take_profit
    assert "rompimento acima" in signal.reason
    assert signal.features_used["range_high"] == pytest.approx(1.1010)


def test_short_signal_on_valid_downward_breakout() -> None:
    rows = _rows(_REQUIRED_BARS, close=1.0980, high=1.1000, low=1.0975, relative_volume_20=2.0)
    state = _state(rows, _regime())
    signal = _strategy().generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.SHORT
    assert signal.take_profit < signal.reference_price < signal.stop_loss
    assert "rompimento abaixo" in signal.reason


def test_no_signal_when_not_compressed() -> None:
    rows = _rows(
        _REQUIRED_BARS,
        close=1.1020,
        high=1.1025,
        low=1.1000,
        relative_volume_20=2.0,
        bollinger_upper=1.1100,
        bollinger_lower=1.0900,
        bollinger_middle=1.1000,
    )
    state = _state(rows, _regime())
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_volume_not_expanding() -> None:
    rows = _rows(_REQUIRED_BARS, close=1.1020, high=1.1025, low=1.1000, relative_volume_20=1.0)
    state = _state(rows, _regime())
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_breakout_distance_too_small() -> None:
    rows = _rows(_REQUIRED_BARS, close=1.10102, high=1.1015, low=1.0995, relative_volume_20=2.0)
    state = _state(rows, _regime())
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_indicator_is_nan() -> None:
    rows = _rows(
        _REQUIRED_BARS,
        close=1.1020,
        high=1.1025,
        low=1.1000,
        relative_volume_20=2.0,
        atr_14=math.nan,
    )
    state = _state(rows, _regime())
    assert _strategy().generate_signal(state) is None
