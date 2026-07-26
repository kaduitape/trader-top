"""Spread, volatilidade, momentum, contexto e multi-timeframe.

Um arquivo por camada seria mais granular, mas estes cinco motores sao
lidos juntos (a saida de um alimenta o outro) e testa-los lado a lado
deixa visivel a interacao — que e onde os erros de verdade aparecem.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from app.apexflow.context import (
    MarketContextState,
    classify_market_context,
)
from app.apexflow.liquidity import LiquidityState, read_liquidity
from app.apexflow.momentum import MomentumState, read_momentum
from app.apexflow.mtf import (
    ENTRY_TIMEFRAMES,
    UnsupportedEntryTimeframeError,
    analyze_timeframes,
    ensure_entry_timeframe,
)
from app.apexflow.spread import SpreadVerdict, read_spread
from app.apexflow.tick_flow import TickDirection, compute_tick_flow
from app.apexflow.volatility import (
    VolatilityState,
    read_volatility,
    second_volatility_points,
)
from app.market.regimes import MarketRegime, Trend, VolatilityLevel
from app.mt5.market_data import Timeframe
from app.news.provider import NewsAssessment, NewsItem, ProviderStatus
from tests.unit.apexflow.conftest import (
    NOW,
    POINT,
    flow_metrics,
    liquidity_reading,
    make_candles,
    make_features,
    make_ticks,
    spread_reading,
    volatility_reading,
)


def regime(
    trend: Trend = Trend.UP,
    *,
    volatility: VolatilityLevel = VolatilityLevel.NORMAL,
    spread_adequate: bool = True,
    liquidity_adequate: bool = True,
    is_transition: bool = False,
    is_extraordinary_event: bool = False,
) -> MarketRegime:
    return MarketRegime(
        trend=trend,
        volatility=volatility,
        spread_adequate=spread_adequate,
        liquidity_adequate=liquidity_adequate,
        is_transition=is_transition,
        is_extraordinary_event=is_extraordinary_event,
    )


# --- Spread Engine ---------------------------------------------------------


def test_spread_within_limits_allows_entry() -> None:
    reading = read_spread(flow_metrics(spread_now=2.0), target_points=40.0)
    assert reading.verdict == SpreadVerdict.OK
    assert reading.allows_entry


def test_spread_above_absolute_limit_is_vetoed() -> None:
    reading = read_spread(
        flow_metrics(spread_now=45.0), target_points=400.0, max_spread_points=30.0
    )
    assert reading.verdict == SpreadVerdict.TOO_WIDE
    assert not reading.allows_entry


def test_rapidly_widening_spread_is_vetoed() -> None:
    reading = read_spread(
        flow_metrics(spread_now=5.0, spread_trend=2.5), target_points=100.0
    )
    assert reading.verdict == SpreadVerdict.WIDENING


def test_spread_incompatible_with_target_is_vetoed() -> None:
    """O veto mais esquecido: spread pequeno em termos absolutos, mas
    grande demais para um alvo curto."""
    reading = read_spread(flow_metrics(spread_now=8.0), target_points=20.0)
    assert reading.verdict == SpreadVerdict.INCOMPATIBLE_WITH_TARGET
    assert reading.spread_to_target == pytest.approx(0.4)


def test_missing_target_disables_only_the_third_veto() -> None:
    ok = read_spread(flow_metrics(spread_now=8.0), target_points=None)
    assert ok.verdict == SpreadVerdict.OK
    too_wide = read_spread(
        flow_metrics(spread_now=99.0), target_points=None, max_spread_points=30.0
    )
    assert too_wide.verdict == SpreadVerdict.TOO_WIDE


def test_candle_spread_is_used_only_when_ticks_are_absent() -> None:
    no_ticks = compute_tick_flow([], point=POINT, now=NOW)
    reading = read_spread(no_ticks, target_points=100.0, fallback_spread_points=4.0)
    assert reading.spread_points == 4.0
    assert reading.verdict == SpreadVerdict.OK


def test_no_measurement_at_all_blocks_instead_of_assuming() -> None:
    no_ticks = compute_tick_flow([], point=POINT, now=NOW)
    reading = read_spread(no_ticks, target_points=100.0)
    assert reading.verdict == SpreadVerdict.UNKNOWN
    assert not reading.allows_entry


# --- Volatility Engine -----------------------------------------------------


def test_volatility_below_minimum_blocks_entry() -> None:
    features = make_features(amplitude=0.00002, count=200)
    reading = read_volatility(
        features, make_ticks(count=40), point=POINT, min_atr_points=20.0
    )
    assert reading.state == VolatilityState.INSUFFICIENT
    assert not reading.allows_entry


def test_sufficient_volatility_allows_entry() -> None:
    features = make_features(amplitude=0.0030, count=200)
    reading = read_volatility(
        features, make_ticks(count=40), point=POINT, min_atr_points=20.0
    )
    assert reading.allows_entry
    assert reading.atr_points is not None
    assert reading.atr_points >= 20.0


def test_second_volatility_needs_enough_ticks() -> None:
    assert second_volatility_points(make_ticks(count=5), point=POINT) is None
    assert second_volatility_points(make_ticks(count=40, step=0.00005), point=POINT) is not None


def test_volatility_without_features_is_unknown_not_zero() -> None:
    reading = read_volatility(
        pd.DataFrame(), make_ticks(count=40), point=POINT, min_atr_points=20.0
    )
    assert reading.state == VolatilityState.UNKNOWN
    assert reading.atr_points is None
    assert not reading.allows_entry


# --- Momentum Engine -------------------------------------------------------


def test_momentum_without_features_is_unknown() -> None:
    reading = read_momentum(pd.DataFrame(), flow_metrics())
    assert reading.state == MomentumState.UNKNOWN
    assert not reading.favours_continuation


def test_flat_market_reports_flat_momentum() -> None:
    features = make_features(step=0.0, amplitude=0.0020, count=200)
    reading = read_momentum(features, flow_metrics())
    assert reading.state == MomentumState.FLAT


def test_accelerating_flow_with_movement_reports_acceleration() -> None:
    features = make_features(step=0.0006, amplitude=0.0008, count=200)
    reading = read_momentum(features, flow_metrics(acceleration=1.6))
    assert reading.state == MomentumState.ACCELERATING
    assert reading.favours_continuation
    assert reading.direction == TickDirection.UP


def test_wide_move_with_dying_flow_reports_exhaustion() -> None:
    """A distincao que mais importa: movimento grande NAO e continuidade
    quando o fluxo esta morrendo e o trajeto e ineficiente."""
    features = make_features(step=0.0030, amplitude=0.0008, count=200)
    reading = read_momentum(features, flow_metrics(acceleration=0.5, efficiency=0.1))
    assert reading.state == MomentumState.EXHAUSTED
    assert not reading.favours_continuation


def test_persistence_counts_same_direction_bars() -> None:
    features = make_features(step=0.0006, amplitude=0.0008, count=200)
    reading = read_momentum(features, flow_metrics())
    assert reading.persistence == pytest.approx(1.0)


# --- Multi-Timeframe -------------------------------------------------------


def test_h1_is_never_an_entry_timeframe() -> None:
    assert Timeframe.H1 not in ENTRY_TIMEFRAMES
    with pytest.raises(UnsupportedEntryTimeframeError):
        ensure_entry_timeframe(Timeframe.H1)
    for timeframe in (Timeframe.M1, Timeframe.M5, Timeframe.M15):
        ensure_entry_timeframe(timeframe)


def test_aligned_timeframes_produce_a_strong_score() -> None:
    up = make_features(step=0.0006, amplitude=0.0004, count=200)
    view = analyze_timeframes(dict.fromkeys(
        (Timeframe.H1, Timeframe.M15, Timeframe.M5, Timeframe.M1), up
    ))
    assert view.alignment_score > 0.5
    assert view.dominant_direction == Trend.UP
    assert view.agrees_with(Trend.UP)
    assert not view.agrees_with(Trend.DOWN)
    assert view.coverage == pytest.approx(1.0)


def test_missing_timeframe_reduces_alignment_instead_of_being_ignored() -> None:
    up = make_features(step=0.0006, amplitude=0.0004, count=200)
    full = analyze_timeframes(dict.fromkeys(
        (Timeframe.H1, Timeframe.M15, Timeframe.M5, Timeframe.M1), up
    ))
    without_macro = analyze_timeframes(
        {Timeframe.M15: up, Timeframe.M5: up, Timeframe.M1: up}
    )
    assert abs(without_macro.alignment_score) < abs(full.alignment_score)
    assert without_macro.coverage < 1.0
    assert without_macro.macro_trend is None


def test_conflicting_timeframes_produce_a_weak_score() -> None:
    up = make_features(step=0.0006, amplitude=0.0004, count=200)
    down = make_features(step=-0.0006, amplitude=0.0004, count=200)
    view = analyze_timeframes(
        {Timeframe.H1: down, Timeframe.M15: down, Timeframe.M5: up, Timeframe.M1: up}
    )
    assert abs(view.alignment_score) < 0.5


# --- Liquidity Engine ------------------------------------------------------


def test_short_history_reports_unknown_liquidity() -> None:
    reading = read_liquidity(make_candles(count=5))
    assert reading.state == LiquidityState.UNKNOWN
    assert not reading.blocks_entry


def test_calm_series_reports_clean_liquidity() -> None:
    reading = read_liquidity(make_candles(count=80, amplitude=0.0004))
    assert reading.state in (LiquidityState.CLEAN, LiquidityState.SWEEP_REVERSED)
    assert not reading.blocks_entry


# --- Market Context Engine -------------------------------------------------


def context(**kwargs):
    base = {
        "regime": regime(),
        "flow": flow_metrics(),
        "volatility": volatility_reading(),
        "spread": spread_reading(),
        "liquidity": liquidity_reading(),
        "news": None,
        "now": NOW,
    }
    base.update(kwargs)
    return classify_market_context(**base)


def test_missing_regime_is_unknown_context() -> None:
    result = context(regime=None)
    assert result.state == MarketContextState.UNKNOWN
    assert not result.is_tradeable


def test_wide_spread_wins_over_a_perfect_trend() -> None:
    """Ordem de prioridade: o que bloqueia e reportado antes do que parece
    oportunidade."""
    result = context(spread=spread_reading(SpreadVerdict.TOO_WIDE))
    assert result.state == MarketContextState.WIDE_SPREAD
    assert not result.is_tradeable


def test_extraordinary_event_is_explosive() -> None:
    result = context(regime=regime(is_extraordinary_event=True))
    assert result.state == MarketContextState.EXPLOSIVE
    assert not result.is_tradeable


def test_recent_high_impact_news_is_post_news() -> None:
    news = NewsAssessment(
        status=ProviderStatus.OK,
        score_contribution=0.0,
        items=[
            NewsItem(
                headline="Payroll",
                published_at=NOW - timedelta(minutes=5),
                impact="HIGH",
                currency="USD",
                sentiment=None,
            )
        ],
    )
    result = context(news=news)
    assert result.state == MarketContextState.POST_NEWS
    assert not result.is_tradeable


def test_old_news_does_not_block() -> None:
    news = NewsAssessment(
        status=ProviderStatus.OK,
        score_contribution=0.0,
        items=[
            NewsItem(
                headline="Payroll",
                published_at=NOW - timedelta(hours=6),
                impact="HIGH",
                currency="USD",
                sentiment=None,
            )
        ],
    )
    assert context(news=news).is_tradeable


def test_active_stop_hunt_is_liquidity_hunt() -> None:
    result = context(liquidity=liquidity_reading(LiquidityState.STOP_HUNT_ACTIVE))
    assert result.state == MarketContextState.LIQUIDITY_HUNT
    assert not result.is_tradeable


def test_extreme_volatility_blocks() -> None:
    result = context(volatility=volatility_reading(atr_ratio=3.0))
    assert result.state == MarketContextState.HIGH_VOLATILITY
    assert not result.is_tradeable


def test_insufficient_volatility_is_a_dead_market() -> None:
    result = context(volatility=volatility_reading(VolatilityState.INSUFFICIENT))
    assert result.state == MarketContextState.DEAD
    assert not result.is_tradeable


def test_rare_ticks_mean_illiquid() -> None:
    result = context(flow=flow_metrics(ticks_per_second=0.05))
    assert result.state == MarketContextState.ILLIQUID
    assert not result.is_tradeable


def test_healthy_trend_is_tradeable() -> None:
    result = context()
    assert result.state == MarketContextState.TRENDING
    assert result.is_tradeable
    assert result.trend == Trend.UP


def test_healthy_range_is_tradeable() -> None:
    result = context(regime=regime(Trend.SIDEWAYS))
    assert result.state == MarketContextState.RANGING
    assert result.is_tradeable
