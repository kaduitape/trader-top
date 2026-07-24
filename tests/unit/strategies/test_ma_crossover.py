import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.strategies.base import MarketState, SignalDirection
from app.strategies.trend.ma_crossover import EmaCrossoverConfig, EmaCrossoverStrategy

_POINT = 0.0001
_T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def _state(rows: list[dict]) -> MarketState:
    return MarketState(symbol="EURUSD", timeframe="M1", features=pd.DataFrame(rows), regime=None)


def _strategy(**overrides: object) -> EmaCrossoverStrategy:
    config = EmaCrossoverConfig(**overrides) if overrides else EmaCrossoverConfig()
    return EmaCrossoverStrategy(config, point=_POINT, bar_seconds=60)


def test_no_signal_with_only_one_row() -> None:
    state = _state([{"open_time": _T0, "close": 1.1000, "ema_9": 1.1000, "ema_21": 1.1000}])
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_no_crossover_occurs() -> None:
    state = _state(
        [
            {"open_time": _T0, "close": 1.1000, "ema_9": 1.0990, "ema_21": 1.1000},
            {
                "open_time": _T0 + timedelta(minutes=1),
                "close": 1.1005,
                "ema_9": 1.0995,
                "ema_21": 1.1002,
            },
        ]
    )
    assert _strategy().generate_signal(state) is None


def test_long_signal_on_upward_crossover() -> None:
    state = _state(
        [
            {"open_time": _T0, "close": 1.1000, "ema_9": 1.0990, "ema_21": 1.1000},
            {
                "open_time": _T0 + timedelta(minutes=1),
                "close": 1.1010,
                "ema_9": 1.1005,
                "ema_21": 1.1000,
            },
        ]
    )
    signal = _strategy(stop_loss_points=100.0, take_profit_points=200.0).generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.reference_price == pytest.approx(1.1010)
    assert signal.stop_loss == pytest.approx(1.1010 - 100.0 * _POINT)
    assert signal.take_profit == pytest.approx(1.1010 + 200.0 * _POINT)
    assert signal.generated_at == _T0 + timedelta(minutes=1)
    assert signal.valid_until == signal.generated_at + timedelta(minutes=1)
    assert signal.features_used == {"ema_9": 1.1005, "ema_21": 1.1000}
    assert "cruzou acima" in signal.reason


def test_short_signal_on_downward_crossover() -> None:
    state = _state(
        [
            {"open_time": _T0, "close": 1.1010, "ema_9": 1.1005, "ema_21": 1.1000},
            {
                "open_time": _T0 + timedelta(minutes=1),
                "close": 1.0990,
                "ema_9": 1.0995,
                "ema_21": 1.1000,
            },
        ]
    )
    signal = _strategy(stop_loss_points=100.0, take_profit_points=200.0).generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.SHORT
    assert signal.stop_loss == pytest.approx(1.0990 + 100.0 * _POINT)
    assert signal.take_profit == pytest.approx(1.0990 - 200.0 * _POINT)
    assert "cruzou abaixo" in signal.reason


def test_no_signal_when_previous_equal_and_current_equal() -> None:
    state = _state(
        [
            {"open_time": _T0, "close": 1.1000, "ema_9": 1.1000, "ema_21": 1.1000},
            {
                "open_time": _T0 + timedelta(minutes=1),
                "close": 1.1000,
                "ema_9": 1.1000,
                "ema_21": 1.1000,
            },
        ]
    )
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_indicator_is_nan() -> None:
    state = _state(
        [
            {"open_time": _T0, "close": 1.1000, "ema_9": math.nan, "ema_21": 1.1000},
            {
                "open_time": _T0 + timedelta(minutes=1),
                "close": 1.1010,
                "ema_9": 1.1005,
                "ema_21": 1.1000,
            },
        ]
    )
    assert _strategy().generate_signal(state) is None


def test_custom_ema_columns_are_respected() -> None:
    state = _state(
        [
            {"open_time": _T0, "close": 1.1000, "ema_50": 1.0990, "ema_200": 1.1000},
            {
                "open_time": _T0 + timedelta(minutes=1),
                "close": 1.1010,
                "ema_50": 1.1005,
                "ema_200": 1.1000,
            },
        ]
    )
    signal = _strategy(fast_column="ema_50", slow_column="ema_200").generate_signal(state)

    assert signal is not None
    assert signal.features_used == {"ema_50": 1.1005, "ema_200": 1.1000}
