"""Perfil de volume por hora e leitura corrente (`app.market.volume_profile`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.market.volume_profile import (
    MIN_SAMPLES_PER_HOUR,
    VolumeLevel,
    build_volume_profile,
    read_current_volume,
)


@dataclass(frozen=True, slots=True)
class FakeCandle:
    open_time: datetime
    open: float = 1.0
    high: float = 1.0
    low: float = 1.0
    close: float = 1.0
    tick_volume: int = 100
    spread: int = 10


def series(volumes_by_hour: dict[int, list[int]]) -> list[FakeCandle]:
    """Uma candle por volume, distribuidas em dias distintos para que cada
    hora acumule varias amostras independentes."""
    candles: list[FakeCandle] = []
    base = datetime(2026, 6, 1, tzinfo=UTC)
    for hour, volumes in sorted(volumes_by_hour.items()):
        for day, volume in enumerate(volumes):
            candles.append(
                FakeCandle(
                    open_time=base + timedelta(days=day, hours=hour),
                    tick_volume=volume,
                )
            )
    return sorted(candles, key=lambda candle: candle.open_time)


def test_profile_uses_median_per_hour() -> None:
    profile = build_volume_profile(series({3: [10, 12, 11, 200], 14: [900, 1000, 1100]}))
    # A mediana ignora o outlier de 200 que arruinaria a media da hora 3.
    assert profile.median_for_hour(3) == pytest.approx(11.5)
    assert profile.median_for_hour(14) == pytest.approx(1000.0)


def test_hour_without_enough_samples_has_no_baseline() -> None:
    profile = build_volume_profile(series({5: [100] * (MIN_SAMPLES_PER_HOUR - 1)}))
    assert profile.median_for_hour(5) is None


def test_quiet_hour_with_typical_volume_reads_normal() -> None:
    """O ponto central do modulo: 10 de volume as 03:00 e NORMAL para
    aquela hora, mesmo sendo minusculo perto do pico das 14:00."""
    candles = series({3: [10, 10, 10, 10], 14: [1000, 1000, 1000, 1000]})
    quiet_hour_now = [*candles[:4]]
    reading = read_current_volume(quiet_hour_now, profile=build_volume_profile(candles))
    assert reading.level == VolumeLevel.NORMAL
    assert reading.baseline_used == "hora"


def test_volume_far_below_the_hour_median_reads_dead() -> None:
    candles = series({14: [1000, 1000, 1000, 1000]})
    profile = build_volume_profile(candles)
    now = [*candles[:3], FakeCandle(open_time=candles[-1].open_time, tick_volume=50)]
    reading = read_current_volume(
        [FakeCandle(open_time=c.open_time, tick_volume=50) for c in now], profile=profile
    )
    assert reading.level == VolumeLevel.DEAD
    assert not reading.is_tradeable


def test_volume_spike_reads_extreme_and_is_not_tradeable() -> None:
    candles = series({14: [1000] * 5})
    profile = build_volume_profile(candles)
    spike = [
        FakeCandle(open_time=candles[-1].open_time + timedelta(days=9), tick_volume=5_000)
        for _ in range(3)
    ]
    reading = read_current_volume(spike, profile=profile)
    assert reading.level == VolumeLevel.EXTREME
    assert not reading.is_tradeable


def test_strong_but_not_extreme_volume_is_tradeable() -> None:
    candles = series({14: [1000] * 5})
    profile = build_volume_profile(candles)
    strong = [
        FakeCandle(open_time=candles[-1].open_time + timedelta(days=9), tick_volume=1_800)
        for _ in range(3)
    ]
    reading = read_current_volume(strong, profile=profile)
    assert reading.level == VolumeLevel.HIGH
    assert reading.is_tradeable


def test_falls_back_to_global_median_when_hour_has_no_samples() -> None:
    candles = series({14: [1000] * 5})
    profile = build_volume_profile(candles)
    other_hour = [
        FakeCandle(open_time=datetime(2026, 6, 9, 3, tzinfo=UTC), tick_volume=1_000)
        for _ in range(3)
    ]
    reading = read_current_volume(other_hour, profile=profile)
    assert reading.baseline_used == "global"
    assert reading.level == VolumeLevel.NORMAL


def test_no_history_reports_unknown_instead_of_guessing() -> None:
    reading = read_current_volume([])
    assert reading.level == VolumeLevel.UNKNOWN
    assert reading.ratio is None
    assert not reading.is_tradeable


def test_reading_uses_last_candle_hour_not_wall_clock() -> None:
    candles = series({3: [10] * 4, 14: [1000] * 4})
    reading = read_current_volume(
        [c for c in candles if c.open_time.hour == 3],
        profile=build_volume_profile(candles),
        now=datetime(2026, 6, 9, 14, tzinfo=UTC),
    )
    assert reading.hour == 3
