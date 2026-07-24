import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.market.regimes import MarketRegime, Trend, VolatilityLevel
from app.strategies.base import MarketState, SignalDirection
from app.strategies.trend.pullback import TrendPullbackConfig, TrendPullbackStrategy

_T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001


def _regime(
    trend: Trend = Trend.UP,
    *,
    spread_adequate: bool = True,
    liquidity_adequate: bool = True,
) -> MarketRegime:
    return MarketRegime(
        trend=trend,
        volatility=VolatilityLevel.NORMAL,
        spread_adequate=spread_adequate,
        liquidity_adequate=liquidity_adequate,
        is_transition=False,
        is_extraordinary_event=False,
    )


def _state(rows: list[dict], regime: MarketRegime | None) -> MarketState:
    return MarketState(symbol="EURUSD", timeframe="M1", features=pd.DataFrame(rows), regime=regime)


def _row(**overrides: object) -> dict:
    base: dict[str, object] = {
        "open_time": _T0,
        "close": 1.1000,
        "ema_21_slope": 0.0001,
        "zscore_20": 0.0,
        "rsi_14": 50.0,
        "atr_14": 0.0010,
    }
    base.update(overrides)
    return base


def _strategy(**overrides: object) -> TrendPullbackStrategy:
    config = TrendPullbackConfig(**overrides) if overrides else TrendPullbackConfig()
    return TrendPullbackStrategy(config, point=_POINT, bar_seconds=60)


def test_no_signal_with_single_row() -> None:
    state = _state([_row()], _regime())
    assert _strategy().generate_signal(state) is None


def test_no_signal_without_regime() -> None:
    rows = [_row(), _row(open_time=_T0 + timedelta(minutes=1))]
    state = _state(rows, None)
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_sideways() -> None:
    rows = [
        _row(zscore_20=-1.5),
        _row(open_time=_T0 + timedelta(minutes=1), zscore_20=-0.5, rsi_14=50.0),
    ]
    state = _state(rows, _regime(Trend.SIDEWAYS))
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_spread_inadequate() -> None:
    rows = [
        _row(zscore_20=-1.5),
        _row(open_time=_T0 + timedelta(minutes=1), zscore_20=-0.5, rsi_14=50.0),
    ]
    state = _state(rows, _regime(Trend.UP, spread_adequate=False))
    assert _strategy().generate_signal(state) is None


def test_long_signal_on_valid_pullback_recovery() -> None:
    rows = [
        _row(zscore_20=-1.5, ema_21_slope=0.0002, rsi_14=35.0),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-0.8,
            ema_21_slope=0.0002,
            rsi_14=46.0,
            atr_14=0.0012,
            close=1.1005,
        ),
    ]
    state = _state(rows, _regime(Trend.UP))
    signal = _strategy().generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.reference_price == pytest.approx(1.1005)
    assert signal.stop_loss < signal.reference_price < signal.take_profit
    assert signal.generated_at == rows[1]["open_time"]
    assert "tendencia de alta" in signal.reason
    assert signal.features_used["rsi_14"] == pytest.approx(46.0)


def test_short_signal_on_valid_pullback_recovery() -> None:
    rows = [
        _row(zscore_20=1.5, ema_21_slope=-0.0002, rsi_14=65.0),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=0.8,
            ema_21_slope=-0.0002,
            rsi_14=54.0,
            atr_14=0.0012,
            close=1.0995,
        ),
    ]
    state = _state(rows, _regime(Trend.DOWN))
    signal = _strategy().generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.SHORT
    assert signal.take_profit < signal.reference_price < signal.stop_loss
    assert "tendencia de baixa" in signal.reason


def test_no_signal_when_rsi_not_recovering_enough() -> None:
    rows = [
        _row(zscore_20=-1.5, ema_21_slope=0.0002, rsi_14=35.0),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-0.8,
            ema_21_slope=0.0002,
            rsi_14=40.0,  # abaixo do limiar de recuperacao (45)
        ),
    ]
    state = _state(rows, _regime(Trend.UP))
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_no_pullback_occurred() -> None:
    rows = [
        _row(zscore_20=0.2, ema_21_slope=0.0002, rsi_14=55.0),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=0.3,
            ema_21_slope=0.0002,
            rsi_14=56.0,
        ),
    ]
    state = _state(rows, _regime(Trend.UP))
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_indicator_is_nan() -> None:
    rows = [
        _row(zscore_20=-1.5, ema_21_slope=0.0002, rsi_14=35.0),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-0.8,
            ema_21_slope=math.nan,
            rsi_14=46.0,
        ),
    ]
    state = _state(rows, _regime(Trend.UP))
    assert _strategy().generate_signal(state) is None
