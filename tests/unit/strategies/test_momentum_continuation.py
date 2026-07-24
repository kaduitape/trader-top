import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.strategies.base import MarketState, SignalDirection
from app.strategies.momentum.momentum_continuation import (
    MomentumContinuationConfig,
    MomentumContinuationStrategy,
)

_T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001


def _row(**overrides: object) -> dict:
    base: dict[str, object] = {
        "open_time": _T0,
        "close": 1.1000,
        "roc_10": 0.0,
        "relative_volume_20": 1.0,
        "zscore_20": 0.0,
        "candle_streak": 0.0,
        "atr_14": 0.0010,
    }
    base.update(overrides)
    return base


def _state(rows: list[dict]) -> MarketState:
    return MarketState(symbol="EURUSD", timeframe="M1", features=pd.DataFrame(rows), regime=None)


def _strategy(**overrides: object) -> MomentumContinuationStrategy:
    config = MomentumContinuationConfig(**overrides) if overrides else MomentumContinuationConfig()
    return MomentumContinuationStrategy(config, point=_POINT, bar_seconds=60)


def test_no_signal_with_single_row() -> None:
    state = _state([_row()])
    assert _strategy().generate_signal(state) is None


def test_long_signal_on_accelerating_uptrend() -> None:
    rows = [
        _row(roc_10=0.10),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            close=1.1010,
            roc_10=0.20,
            relative_volume_20=1.5,
            zscore_20=1.0,
            candle_streak=3.0,
            atr_14=0.0012,
        ),
    ]
    signal = _strategy().generate_signal(_state(rows))

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.reference_price == pytest.approx(1.1010)
    assert signal.stop_loss < signal.reference_price < signal.take_profit
    assert "momentum de alta" in signal.reason


def test_short_signal_on_accelerating_downtrend() -> None:
    rows = [
        _row(roc_10=-0.10),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            close=1.0990,
            roc_10=-0.20,
            relative_volume_20=1.5,
            zscore_20=-1.0,
            candle_streak=-3.0,
            atr_14=0.0012,
        ),
    ]
    signal = _strategy().generate_signal(_state(rows))

    assert signal is not None
    assert signal.direction == SignalDirection.SHORT
    assert signal.take_profit < signal.reference_price < signal.stop_loss
    assert "momentum de baixa" in signal.reason


def test_no_signal_when_move_is_overextended() -> None:
    rows = [
        _row(roc_10=0.10),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            roc_10=0.20,
            relative_volume_20=1.5,
            zscore_20=3.0,  # acima do limite de extensao (2.5)
            candle_streak=3.0,
        ),
    ]
    assert _strategy().generate_signal(_state(rows)) is None


def test_no_signal_when_volume_not_expanding() -> None:
    rows = [
        _row(roc_10=0.10),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            roc_10=0.20,
            relative_volume_20=1.0,  # abaixo do limiar (1.2)
            zscore_20=1.0,
            candle_streak=3.0,
        ),
    ]
    assert _strategy().generate_signal(_state(rows)) is None


def test_no_signal_when_streak_too_short() -> None:
    rows = [
        _row(roc_10=0.10),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            roc_10=0.20,
            relative_volume_20=1.5,
            zscore_20=1.0,
            candle_streak=1.0,  # abaixo do minimo (3)
        ),
    ]
    assert _strategy().generate_signal(_state(rows)) is None


def test_no_signal_when_roc_not_accelerating() -> None:
    rows = [
        _row(roc_10=0.30),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            roc_10=0.20,  # desacelerando, nao acelerando
            relative_volume_20=1.5,
            zscore_20=1.0,
            candle_streak=3.0,
        ),
    ]
    assert _strategy().generate_signal(_state(rows)) is None


def test_no_signal_when_indicator_is_nan() -> None:
    rows = [
        _row(roc_10=0.10),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            roc_10=0.20,
            relative_volume_20=1.5,
            zscore_20=1.0,
            candle_streak=3.0,
            atr_14=math.nan,
        ),
    ]
    assert _strategy().generate_signal(_state(rows)) is None
