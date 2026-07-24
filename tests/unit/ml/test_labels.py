from datetime import UTC, datetime, timedelta

from app.ml.labels import BarrierOutcome, apply_triple_barrier
from app.mt5.market_data import RawCandle
from app.strategies.base import SignalDirection

_START = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def _candle(minute_offset: int, o: float, h: float, low: float, c: float) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=minute_offset),
        open=o,
        high=h,
        low=low,
        close=c,
        tick_volume=100,
        spread=10,
        real_volume=0,
    )


def test_long_target_hit_first_returns_target_first_with_label_one() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),  # sinal
        _candle(1, 1.1000, 1.1030, 1.0995, 1.1000),  # alvo atingido, stop nao
    ]
    result = apply_triple_barrier(
        candles,
        signal_index=0,
        direction=SignalDirection.LONG,
        entry_price=1.1000,
        stop_loss=1.0990,
        take_profit=1.1020,
        max_horizon_bars=10,
    )
    assert result is not None
    assert result.outcome == BarrierOutcome.TARGET_FIRST
    assert result.label == 1
    assert result.exit_index == 1
    assert result.bars_held == 1


def test_long_stop_hit_first_returns_stop_first_with_label_zero() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1005, 1.0980, 1.1000),  # so o stop
    ]
    result = apply_triple_barrier(
        candles,
        signal_index=0,
        direction=SignalDirection.LONG,
        entry_price=1.1000,
        stop_loss=1.0990,
        take_profit=1.1020,
        max_horizon_bars=10,
    )
    assert result is not None
    assert result.outcome == BarrierOutcome.STOP_FIRST
    assert result.label == 0


def test_long_conservative_when_both_barriers_touched_same_candle() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1030, 1.0980, 1.1000),  # ambas as barreiras cabem aqui
    ]
    result = apply_triple_barrier(
        candles,
        signal_index=0,
        direction=SignalDirection.LONG,
        entry_price=1.1000,
        stop_loss=1.0990,
        take_profit=1.1020,
        max_horizon_bars=10,
    )
    assert result is not None
    # Nunca assume o resultado favoravel quando ambas cabem na mesma candle.
    assert result.outcome == BarrierOutcome.STOP_FIRST
    assert result.label == 0


def test_short_conservative_when_both_barriers_touched_same_candle() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1020, 1.0970, 1.1000),
    ]
    result = apply_triple_barrier(
        candles,
        signal_index=0,
        direction=SignalDirection.SHORT,
        entry_price=1.1000,
        stop_loss=1.1010,
        take_profit=1.0980,
        max_horizon_bars=10,
    )
    assert result is not None
    assert result.outcome == BarrierOutcome.STOP_FIRST
    assert result.label == 0


def test_short_target_hit_first_returns_label_one() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1005, 1.0975, 1.1000),  # alvo (low<=0.0980), stop nao (high<1.1010)
    ]
    result = apply_triple_barrier(
        candles,
        signal_index=0,
        direction=SignalDirection.SHORT,
        entry_price=1.1000,
        stop_loss=1.1010,
        take_profit=1.0980,
        max_horizon_bars=10,
    )
    assert result is not None
    assert result.outcome == BarrierOutcome.TARGET_FIRST
    assert result.label == 1


def test_time_barrier_when_neither_touched_within_horizon() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1005, 1.0995, 1.1002),
        _candle(2, 1.1002, 1.1006, 1.0996, 1.1001),
    ]
    result = apply_triple_barrier(
        candles,
        signal_index=0,
        direction=SignalDirection.LONG,
        entry_price=1.1000,
        stop_loss=1.0500,
        take_profit=1.2000,
        max_horizon_bars=2,
    )
    assert result is not None
    assert result.outcome == BarrierOutcome.TIME_BARRIER
    assert result.label == 0
    assert result.exit_index == 2
    assert result.bars_held == 2
    assert result.exit_price == candles[2].close


def test_returns_none_when_no_bars_after_signal() -> None:
    candles = [_candle(0, 1.1000, 1.1000, 1.1000, 1.1000)]
    result = apply_triple_barrier(
        candles,
        signal_index=0,
        direction=SignalDirection.LONG,
        entry_price=1.1000,
        stop_loss=1.0990,
        take_profit=1.1020,
        max_horizon_bars=10,
    )
    assert result is None


def test_horizon_is_capped_by_available_data_not_just_max_horizon_bars() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1005, 1.0995, 1.1001),
    ]
    # max_horizon_bars pede 100 barras, mas so ha 1 disponivel apos o sinal.
    result = apply_triple_barrier(
        candles,
        signal_index=0,
        direction=SignalDirection.LONG,
        entry_price=1.1000,
        stop_loss=1.0500,
        take_profit=1.2000,
        max_horizon_bars=100,
    )
    assert result is not None
    assert result.outcome == BarrierOutcome.TIME_BARRIER
    assert result.exit_index == 1
    assert result.bars_held == 1
