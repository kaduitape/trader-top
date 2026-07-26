"""Fixtures compartilhadas dos testes do ApexFlow AI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.apexflow.context import MarketContext, MarketContextState
from app.apexflow.liquidity import LiquidityReading, LiquidityState
from app.apexflow.momentum import MomentumReading, MomentumState
from app.apexflow.mtf import MultiTimeframeView, TimeframeRole, TimeframeView
from app.apexflow.spread import SpreadReading, SpreadVerdict
from app.apexflow.tick_flow import TickDirection, TickFlowMetrics
from app.apexflow.volatility import VolatilityReading, VolatilityState
from app.market.features import build_candle_features
from app.market.regimes import Trend
from app.market.sessions import evaluate_symbol_session
from app.market.volume_profile import VolumeLevel, VolumeReading
from app.mt5.market_data import Timeframe

POINT = 0.0001
NOW = datetime(2026, 7, 22, 14, 10, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FakeTick:
    timestamp: datetime
    bid: float
    ask: float


@dataclass(frozen=True, slots=True)
class FakeCandle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 100
    spread: int = 2


def make_ticks(
    *,
    count: int = 60,
    start: datetime = NOW - timedelta(seconds=60),
    interval_ms: int = 1_000,
    step: float = 0.0,
    spread: float = 0.0002,
    base: float = 1.1000,
) -> list[FakeTick]:
    """Serie de ticks sintetica e totalmente determinista."""
    ticks: list[FakeTick] = []
    price = base
    for index in range(count):
        price += step
        ticks.append(
            FakeTick(
                timestamp=start + timedelta(milliseconds=interval_ms * index),
                bid=price,
                ask=price + spread,
            )
        )
    return ticks


def make_candles(
    *,
    count: int = 260,
    minutes: int = 5,
    end: datetime = NOW,
    step: float = 0.0,
    base: float = 1.1000,
    amplitude: float = 0.0008,
    volume: int = 100,
    spread: int = 2,
) -> list[FakeCandle]:
    candles: list[FakeCandle] = []
    price = base
    start = end - timedelta(minutes=minutes * count)
    for index in range(count):
        price += step
        candles.append(
            FakeCandle(
                open_time=start + timedelta(minutes=minutes * index),
                open=price,
                high=price + amplitude,
                low=price - amplitude,
                close=price + amplitude / 2,
                tick_volume=volume,
                spread=spread,
            )
        )
    return candles


def make_features(**kwargs) -> pd.DataFrame:
    return build_candle_features(make_candles(**kwargs), point=POINT)


# --- Leituras sinteticas, para testar cada motor em isolamento ------------


def flow_metrics(
    *,
    tick_count: int = 120,
    ticks_per_second: float | None = 2.0,
    acceleration: float | None = 1.0,
    direction: int = TickDirection.UP,
    efficiency: float | None = 0.7,
    spread_now: float | None = 2.0,
    spread_trend: float | None = 1.0,
) -> TickFlowMetrics:
    return TickFlowMetrics(
        tick_count=tick_count,
        window_seconds=60.0,
        ticks_per_second=ticks_per_second,
        tick_acceleration=acceleration,
        mean_interval_ms=500.0,
        max_interval_ms=1_200.0,
        uptick_ratio=0.7 if direction == TickDirection.UP else 0.3,
        direction_bias=direction,
        price_velocity_points=1.0 * direction,
        price_path_points=30.0,
        efficiency=efficiency,
        spread_now_points=spread_now,
        spread_mean_points=spread_now,
        spread_max_points=spread_now,
        spread_trend=spread_trend,
        latency_seconds=0.5,
    )


def spread_reading(verdict: SpreadVerdict = SpreadVerdict.OK) -> SpreadReading:
    return SpreadReading(
        verdict=verdict,
        spread_points=2.0,
        mean_points=2.0,
        max_points=3.0,
        trend=1.0,
        target_points=40.0,
        spread_to_target=0.05,
        reasons=("spread sintetico de teste",),
    )


def volatility_reading(
    state: VolatilityState = VolatilityState.STABLE, *, atr_ratio: float | None = 1.0
) -> VolatilityReading:
    return VolatilityReading(
        state=state,
        atr_points=40.0,
        atr_ratio=atr_ratio,
        true_range_points=35.0,
        realized_volatility=0.001,
        second_volatility_points=5.0,
        min_required_points=20.0,
        reasons=("volatilidade sintetica de teste",),
    )


def momentum_reading(
    state: MomentumState = MomentumState.ACCELERATING,
    *,
    direction: int = TickDirection.UP,
) -> MomentumReading:
    return MomentumReading(
        state=state,
        direction=direction,
        strength_atr=1.0,
        impulse_points=0.0010,
        acceleration=1.4,
        efficiency=0.7,
        persistence=0.8,
        speed_change=1.1,
        direction_change=False,
        reasons=("momentum sintetico de teste",),
    )


def liquidity_reading(state: LiquidityState = LiquidityState.CLEAN) -> LiquidityReading:
    return LiquidityReading(
        state=state,
        direction=None,
        recent_sweeps=(),
        unmitigated_order_blocks=(),
        open_fair_value_gaps=(),
        structure_events=(),
        institutional_zones=0,
        reasons=("liquidez sintetica de teste",),
    )


def mtf_view(alignment: float = 0.6, *, coverage_full: bool = True) -> MultiTimeframeView:
    trend = Trend.UP if alignment > 0 else Trend.DOWN if alignment < 0 else Trend.SIDEWAYS
    views = tuple(
        TimeframeView(
            timeframe=timeframe,
            role=role,
            trend=trend if coverage_full else None,
            available=coverage_full,
            note=f"{timeframe.value}: {trend.value}",
        )
        for timeframe, role in (
            (Timeframe.H1, TimeframeRole.MACRO),
            (Timeframe.M15, TimeframeRole.CONTEXT),
            (Timeframe.M5, TimeframeRole.CONFIRMATION),
            (Timeframe.M1, TimeframeRole.TIMING),
        )
    )
    return MultiTimeframeView(
        views=views,
        macro_trend=trend if coverage_full else None,
        alignment_score=alignment,
        dominant_direction=trend,
        reasons=("alinhamento sintetico de teste",),
    )


def market_context(
    state: MarketContextState = MarketContextState.TRENDING,
    *,
    trend: Trend = Trend.UP,
) -> MarketContext:
    tradeable = state in (MarketContextState.TRENDING, MarketContextState.RANGING)
    return MarketContext(
        state=state,
        trend=trend,
        is_tradeable=tradeable,
        confidence=0.8,
        reasons=("contexto sintetico de teste",),
        blockers=() if tradeable else ("bloqueio sintetico de teste",),
    )


def volume_reading(level: VolumeLevel = VolumeLevel.NORMAL) -> VolumeReading:
    return VolumeReading(
        level=level,
        current_volume=1_000.0,
        hour=14,
        hour_median=1_000.0,
        overall_median=1_000.0,
        ratio_vs_hour=1.0,
        ratio_vs_overall=1.0,
        baseline_used="hora",
        reasons=(),
    )


@pytest.fixture
def session_state():
    return evaluate_symbol_session("EURUSD", now=NOW)
