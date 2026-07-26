"""Tick Collector e metricas de fluxo (`app.apexflow.tick_flow`)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.apexflow.tick_flow import (
    MIN_TICKS_FOR_FLOW,
    TickBuffer,
    TickDirection,
    compute_tick_flow,
)
from tests.unit.apexflow.conftest import NOW, POINT, FakeTick, make_ticks

# --- Buffer circular -------------------------------------------------------


def test_buffer_discards_oldest_beyond_maxlen() -> None:
    buffer = TickBuffer(maxlen=10)
    buffer.extend(make_ticks(count=25))
    assert len(buffer) == 10
    snapshot = buffer.snapshot()
    # Os 10 mais RECENTES sobrevivem, na ordem.
    assert snapshot[-1].timestamp > snapshot[0].timestamp


def test_buffer_snapshot_is_detached_from_further_pushes() -> None:
    buffer = TickBuffer(maxlen=100)
    buffer.extend(make_ticks(count=5))
    snapshot = buffer.snapshot()
    buffer.push(FakeTick(timestamp=NOW, bid=1.2, ask=1.2002))
    assert len(snapshot) == 5
    assert len(buffer) == 6


def test_empty_buffer_reports_itself() -> None:
    buffer = TickBuffer()
    assert buffer.is_empty
    buffer.push(FakeTick(timestamp=NOW, bid=1.0, ask=1.0002))
    assert not buffer.is_empty
    buffer.clear()
    assert buffer.is_empty


# --- Metricas --------------------------------------------------------------


def test_no_ticks_reports_unavailable_not_zero() -> None:
    metrics = compute_tick_flow([], point=POINT, now=NOW)
    assert metrics.tick_count == 0
    assert metrics.ticks_per_second is None
    assert not metrics.is_reliable
    assert metrics.warnings


def test_too_few_ticks_is_reported_as_unreliable() -> None:
    metrics = compute_tick_flow(
        make_ticks(count=MIN_TICKS_FOR_FLOW - 1), point=POINT, now=NOW
    )
    assert not metrics.is_reliable
    assert metrics.ticks_per_second is None
    assert any("minimo" in warning for warning in metrics.warnings)


def test_tick_rate_matches_the_window() -> None:
    # 61 ticks a cada 1000 ms => 60 s de janela.
    metrics = compute_tick_flow(
        make_ticks(count=61, interval_ms=1_000), point=POINT, now=NOW
    )
    assert metrics.window_seconds == pytest.approx(60.0)
    assert metrics.ticks_per_second == pytest.approx(61 / 60)
    assert metrics.mean_interval_ms == pytest.approx(1_000.0)


def test_rising_price_yields_upward_bias_and_high_efficiency() -> None:
    metrics = compute_tick_flow(
        make_ticks(count=60, step=0.00002), point=POINT, now=NOW
    )
    assert metrics.direction_bias == TickDirection.UP
    assert metrics.uptick_ratio == pytest.approx(1.0)
    # Movimento monotonico: deslocamento liquido == caminho percorrido.
    assert metrics.efficiency == pytest.approx(1.0)
    assert metrics.price_velocity_points is not None
    assert metrics.price_velocity_points > 0


def test_falling_price_yields_downward_bias() -> None:
    metrics = compute_tick_flow(
        make_ticks(count=60, step=-0.00002), point=POINT, now=NOW
    )
    assert metrics.direction_bias == TickDirection.DOWN
    assert metrics.price_velocity_points is not None
    assert metrics.price_velocity_points < 0


def test_choppy_market_has_low_efficiency_and_no_bias() -> None:
    """Vaivem: muito caminho percorrido, quase nenhum deslocamento."""
    ticks = []
    for index in range(60):
        offset = 0.0001 if index % 2 == 0 else -0.0001
        ticks.append(
            FakeTick(
                timestamp=NOW - timedelta(seconds=60 - index),
                bid=1.1000 + offset,
                ask=1.1002 + offset,
            )
        )
    metrics = compute_tick_flow(ticks, point=POINT, now=NOW)
    assert metrics.direction_bias == TickDirection.FLAT
    assert metrics.efficiency is not None
    assert metrics.efficiency < 0.2


def test_acceleration_detects_a_faster_second_half() -> None:
    slow = make_ticks(count=20, interval_ms=2_000, start=NOW - timedelta(seconds=80))
    fast_start = slow[-1].timestamp
    fast = [
        FakeTick(
            timestamp=fast_start + timedelta(milliseconds=250 * (index + 1)),
            bid=1.1000,
            ask=1.1002,
        )
        for index in range(20)
    ]
    metrics = compute_tick_flow([*slow, *fast], point=POINT, now=NOW)
    assert metrics.tick_acceleration is not None
    assert metrics.tick_acceleration > 1.5


def test_spread_metrics_are_in_points() -> None:
    metrics = compute_tick_flow(
        make_ticks(count=40, spread=0.0003), point=POINT, now=NOW
    )
    assert metrics.spread_now_points == pytest.approx(3.0)
    assert metrics.spread_mean_points == pytest.approx(3.0)


def test_widening_spread_shows_in_the_trend() -> None:
    tight = make_ticks(count=20, spread=0.0002, start=NOW - timedelta(seconds=40))
    wide_start = tight[-1].timestamp
    wide = [
        FakeTick(
            timestamp=wide_start + timedelta(seconds=index + 1),
            bid=1.1000,
            ask=1.1008,
        )
        for index in range(20)
    ]
    metrics = compute_tick_flow([*tight, *wide], point=POINT, now=NOW)
    assert metrics.spread_trend is not None
    assert metrics.spread_trend > 2.0


def test_latency_is_measured_against_now_not_the_window() -> None:
    ticks = make_ticks(count=40, start=NOW - timedelta(seconds=300))
    metrics = compute_tick_flow(ticks, point=POINT, now=NOW)
    assert metrics.latency_seconds is not None
    assert metrics.latency_seconds > 200


def test_naive_timestamps_are_treated_as_utc() -> None:
    ticks = [
        FakeTick(timestamp=(NOW - timedelta(seconds=60 - i)).replace(tzinfo=None),
                 bid=1.1, ask=1.1002)
        for i in range(30)
    ]
    metrics = compute_tick_flow(ticks, point=POINT, now=NOW)
    assert metrics.is_reliable
    assert metrics.latency_seconds is not None
