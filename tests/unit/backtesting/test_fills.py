from datetime import UTC, datetime, timedelta

import pytest

from app.backtesting.fills import TickCostModel, simulate_entry_fill, simulate_exit_fill
from app.mt5.market_data import RawTick
from app.strategies.base import SignalDirection

_T0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
_POINT = 0.0001


def _tick(seconds: float, bid: float, ask: float) -> RawTick:
    return RawTick(
        timestamp=_T0 + timedelta(seconds=seconds),
        bid=bid,
        ask=ask,
        last=0.0,
        volume=0.0,
        flags=6,
    )


def test_entry_fill_long_uses_ask_plus_slippage() -> None:
    ticks = [_tick(0, 1.1000, 1.1002)]
    model = TickCostModel(slippage_points=1.0)

    result = simulate_entry_fill(
        ticks, SignalDirection.LONG, signal_time=_T0, cost_model=model, point=_POINT
    )

    assert result.filled is True
    assert result.fill_price == pytest.approx(1.1002 + 1.0 * _POINT)
    assert result.spread_points == pytest.approx(2.0)
    assert result.fill_time == ticks[0].timestamp


def test_entry_fill_short_uses_bid_minus_slippage() -> None:
    ticks = [_tick(0, 1.1000, 1.1002)]
    model = TickCostModel(slippage_points=1.0)

    result = simulate_entry_fill(
        ticks, SignalDirection.SHORT, signal_time=_T0, cost_model=model, point=_POINT
    )

    assert result.filled is True
    assert result.fill_price == pytest.approx(1.1000 - 1.0 * _POINT)


def test_entry_fill_waits_for_latency() -> None:
    ticks = [_tick(0, 1.1000, 1.1002), _tick(1, 1.1010, 1.1012)]
    model = TickCostModel(latency_ms=1000)

    result = simulate_entry_fill(
        ticks, SignalDirection.LONG, signal_time=_T0, cost_model=model, point=_POINT
    )

    # Com 1000ms de latencia, o primeiro tick elegivel e o de t=1s, nao o de t=0.
    assert result.fill_time == ticks[1].timestamp
    assert result.fill_price == pytest.approx(1.1012)


def test_entry_fill_rejected_when_spread_too_wide() -> None:
    ticks = [_tick(0, 1.1000, 1.1020)]  # spread de 20 pontos
    model = TickCostModel(max_spread_points=5.0)

    result = simulate_entry_fill(
        ticks, SignalDirection.LONG, signal_time=_T0, cost_model=model, point=_POINT
    )

    assert result.filled is False
    assert result.fill_price is None
    assert "spread" in (result.rejection_reason or "")


def test_entry_fill_not_filled_when_no_ticks_after_latency() -> None:
    ticks = [_tick(0, 1.1000, 1.1002)]
    model = TickCostModel(latency_ms=5000)

    result = simulate_entry_fill(
        ticks, SignalDirection.LONG, signal_time=_T0, cost_model=model, point=_POINT
    )

    assert result.filled is False
    assert result.rejection_reason is not None


def test_entry_fill_not_filled_with_empty_ticks() -> None:
    result = simulate_entry_fill(
        [], SignalDirection.LONG, signal_time=_T0, cost_model=TickCostModel(), point=_POINT
    )
    assert result.filled is False


def test_exit_fill_long_uses_bid_minus_slippage() -> None:
    ticks = [_tick(0, 1.1050, 1.1052)]
    model = TickCostModel(slippage_points=1.0)

    result = simulate_exit_fill(
        ticks, SignalDirection.LONG, trigger_time=_T0, cost_model=model, point=_POINT
    )

    assert result.filled is True
    assert result.fill_price == pytest.approx(1.1050 - 1.0 * _POINT)


def test_exit_fill_short_uses_ask_plus_slippage() -> None:
    ticks = [_tick(0, 1.1050, 1.1052)]
    model = TickCostModel(slippage_points=1.0)

    result = simulate_exit_fill(
        ticks, SignalDirection.SHORT, trigger_time=_T0, cost_model=model, point=_POINT
    )

    assert result.filled is True
    assert result.fill_price == pytest.approx(1.1052 + 1.0 * _POINT)


def test_exit_fill_never_rejected_for_wide_spread() -> None:
    ticks = [_tick(0, 1.1000, 1.1100)]  # spread enorme
    model = TickCostModel(max_spread_points=1.0)

    result = simulate_exit_fill(
        ticks, SignalDirection.LONG, trigger_time=_T0, cost_model=model, point=_POINT
    )

    assert result.filled is True
    assert result.rejection_reason is None


def test_exit_fill_falls_back_to_last_tick_when_no_tick_after_latency() -> None:
    ticks = [_tick(0, 1.1000, 1.1002), _tick(1, 1.1010, 1.1012)]
    model = TickCostModel(latency_ms=5000)

    result = simulate_exit_fill(
        ticks, SignalDirection.LONG, trigger_time=_T0, cost_model=model, point=_POINT
    )

    assert result.filled is True
    assert result.fill_time == ticks[-1].timestamp


def test_exit_fill_not_filled_with_empty_ticks() -> None:
    result = simulate_exit_fill(
        [], SignalDirection.LONG, trigger_time=_T0, cost_model=TickCostModel(), point=_POINT
    )
    assert result.filled is False
    assert result.rejection_reason is not None
