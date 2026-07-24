import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.market.regimes import MarketRegime, Trend, VolatilityLevel
from app.strategies.base import MarketState, SignalDirection
from app.strategies.mean_reversion.zscore_reversion import (
    ZScoreMeanReversionConfig,
    ZScoreMeanReversionStrategy,
)

_T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001


def _regime(trend: Trend = Trend.SIDEWAYS, *, spread_adequate: bool = True) -> MarketRegime:
    return MarketRegime(
        trend=trend,
        volatility=VolatilityLevel.NORMAL,
        spread_adequate=spread_adequate,
        liquidity_adequate=True,
        is_transition=False,
        is_extraordinary_event=False,
    )


def _row(**overrides: object) -> dict:
    base: dict[str, object] = {
        "open_time": _T0,
        "close": 1.1000,
        "zscore_20": 0.0,
        "rsi_14": 50.0,
        "candle_body": 0.0005,
        "atr_14": 0.0010,
        "bollinger_middle": 1.1000,
    }
    base.update(overrides)
    return base


def _state(rows: list[dict], regime: MarketRegime | None) -> MarketState:
    return MarketState(symbol="EURUSD", timeframe="M1", features=pd.DataFrame(rows), regime=regime)


def _strategy(**overrides: object) -> ZScoreMeanReversionStrategy:
    config = ZScoreMeanReversionConfig(**overrides) if overrides else ZScoreMeanReversionConfig()
    return ZScoreMeanReversionStrategy(config, point=_POINT, bar_seconds=60)


def test_no_signal_with_single_row() -> None:
    state = _state([_row()], _regime())
    assert _strategy().generate_signal(state) is None


def test_no_signal_without_regime() -> None:
    rows = [_row(), _row(open_time=_T0 + timedelta(minutes=1))]
    state = _state(rows, None)
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_trend_is_not_sideways() -> None:
    rows = [
        _row(rsi_14=25.0, candle_body=0.0010),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-2.5,
            rsi_14=28.0,
            candle_body=0.0004,
        ),
    ]
    state = _state(rows, _regime(Trend.UP))
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_spread_inadequate() -> None:
    rows = [
        _row(rsi_14=25.0, candle_body=0.0010),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-2.5,
            rsi_14=28.0,
            candle_body=0.0004,
        ),
    ]
    state = _state(rows, _regime(Trend.SIDEWAYS, spread_adequate=False))
    assert _strategy().generate_signal(state) is None


def test_long_signal_on_valid_reversion_setup() -> None:
    rows = [
        _row(rsi_14=25.0, candle_body=0.0010),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            close=1.0950,
            zscore_20=-2.5,
            rsi_14=28.0,
            candle_body=0.0004,
            atr_14=0.0012,
            bollinger_middle=1.1000,
        ),
    ]
    state = _state(rows, _regime(Trend.SIDEWAYS))
    signal = _strategy().generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.reference_price == pytest.approx(1.0950)
    assert signal.take_profit == pytest.approx(1.1000)
    assert signal.stop_loss < signal.reference_price
    assert "sobrevendido" in signal.reason


def test_short_signal_on_valid_reversion_setup() -> None:
    rows = [
        _row(rsi_14=75.0, candle_body=0.0010),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            close=1.1050,
            zscore_20=2.5,
            rsi_14=72.0,
            candle_body=0.0004,
            atr_14=0.0012,
            bollinger_middle=1.1000,
        ),
    ]
    state = _state(rows, _regime(Trend.SIDEWAYS))
    signal = _strategy().generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.SHORT
    assert signal.take_profit == pytest.approx(1.1000)
    assert signal.stop_loss > signal.reference_price
    assert "sobrecomprado" in signal.reason


def test_no_signal_when_not_decelerating() -> None:
    rows = [
        _row(rsi_14=25.0, candle_body=0.0003),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-2.5,
            rsi_14=28.0,
            candle_body=0.0010,  # corpo MAIOR que o anterior: sem desaceleracao
        ),
    ]
    state = _state(rows, _regime(Trend.SIDEWAYS))
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_rsi_not_recovering() -> None:
    rows = [
        _row(rsi_14=28.0, candle_body=0.0010),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-2.5,
            rsi_14=25.0,  # caiu, nao recuperou
            candle_body=0.0004,
        ),
    ]
    state = _state(rows, _regime(Trend.SIDEWAYS))
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_zscore_not_extreme_enough() -> None:
    rows = [
        _row(rsi_14=25.0, candle_body=0.0010),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-1.0,  # nao atinge o limiar de -2.0
            rsi_14=28.0,
            candle_body=0.0004,
        ),
    ]
    state = _state(rows, _regime(Trend.SIDEWAYS))
    assert _strategy().generate_signal(state) is None


def test_no_signal_when_indicator_is_nan() -> None:
    rows = [
        _row(rsi_14=25.0, candle_body=0.0010),
        _row(
            open_time=_T0 + timedelta(minutes=1),
            zscore_20=-2.5,
            rsi_14=28.0,
            candle_body=0.0004,
            atr_14=math.nan,
        ),
    ]
    state = _state(rows, _regime(Trend.SIDEWAYS))
    assert _strategy().generate_signal(state) is None
