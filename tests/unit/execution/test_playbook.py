"""Seletor de operacional (`app.execution.playbook`).

Alem das regras de escolha, dois invariantes de SEGURANCA sao cobertos
explicitamente porque protegem dinheiro real: o score minimo nunca cai
abaixo do configurado e o risco nunca passa de 1x o configurado.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.execution.playbook import (
    MAX_THRESHOLD,
    PlaybookKind,
    select_playbook,
)
from app.market.regimes import MarketRegime, Trend, VolatilityLevel
from app.market.sessions import SessionRating, evaluate_symbol_session
from app.market.volume_profile import VolumeLevel, VolumeReading

WEDNESDAY = datetime(2026, 7, 22, tzinfo=UTC)
OVERLAP = WEDNESDAY.replace(hour=14)  # Londres + Nova York
TOKYO_ONLY = WEDNESDAY.replace(hour=2)
LATE_FRIDAY = datetime(2026, 7, 24, 20, 30, tzinfo=UTC)
SATURDAY = datetime(2026, 7, 25, 12, tzinfo=UTC)


def volume(level: VolumeLevel, ratio: float = 1.0) -> VolumeReading:
    return VolumeReading(
        level=level,
        current_volume=1_000.0,
        hour=14,
        hour_median=1_000.0,
        overall_median=1_000.0,
        ratio_vs_hour=ratio,
        ratio_vs_overall=ratio,
        baseline_used="hora",
        reasons=(),
    )


def regime(
    trend: Trend = Trend.UP,
    volatility: VolatilityLevel = VolatilityLevel.NORMAL,
    *,
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


def select(*, now=OVERLAP, symbol="EURUSD", vol=None, reg=None, threshold=90.0, **kwargs):
    return select_playbook(
        session=evaluate_symbol_session(symbol, now=now),
        volume=vol if vol is not None else volume(VolumeLevel.NORMAL),
        regime=reg if reg is not None else regime(),
        base_threshold=threshold,
        **kwargs,
    )


# --- Bloqueios: ficar de fora e uma decisao valida ------------------------


def test_closed_market_stands_aside() -> None:
    decision = select(now=SATURDAY)
    assert decision.kind == PlaybookKind.STAND_ASIDE
    assert not decision.tradeable
    assert decision.strategy_name is None
    assert any("fechado" in blocker.lower() for blocker in decision.blockers)


def test_weekend_protection_window_stands_aside() -> None:
    decision = select(now=LATE_FRIDAY)
    assert not decision.tradeable
    assert any("fechamento semanal" in blocker for blocker in decision.blockers)


def test_dead_volume_stands_aside() -> None:
    decision = select(vol=volume(VolumeLevel.DEAD, 0.1))
    assert not decision.tradeable
    assert any("Volume praticamente nulo" in blocker for blocker in decision.blockers)


def test_extreme_volume_spike_stands_aside() -> None:
    decision = select(vol=volume(VolumeLevel.EXTREME, 4.0))
    assert not decision.tradeable
    assert any("Pico atipico" in blocker for blocker in decision.blockers)


def test_unknown_volume_stands_aside_instead_of_guessing() -> None:
    decision = select(vol=volume(VolumeLevel.UNKNOWN))
    assert not decision.tradeable


def test_missing_regime_stands_aside() -> None:
    decision = select_playbook(
        session=evaluate_symbol_session("EURUSD", now=OVERLAP),
        volume=volume(VolumeLevel.NORMAL),
        regime=None,
        base_threshold=90.0,
    )
    assert not decision.tradeable
    assert any("Regime" in blocker for blocker in decision.blockers)


def test_extraordinary_event_stands_aside() -> None:
    decision = select(reg=regime(is_extraordinary_event=True))
    assert not decision.tradeable


def test_wide_spread_stands_aside() -> None:
    decision = select(reg=regime(spread_adequate=False))
    assert not decision.tradeable
    assert any("Spread" in blocker for blocker in decision.blockers)


def test_quiet_session_without_compensating_volume_stands_aside() -> None:
    decision = select(now=TOKYO_ONLY, vol=volume(VolumeLevel.NORMAL))
    assert decision.session_rating == SessionRating.QUIET
    assert not decision.tradeable


def test_quiet_session_with_strong_volume_still_operates() -> None:
    """Evidencia (volume real) vence a hipotese (relogio)."""
    decision = select(now=TOKYO_ONLY, vol=volume(VolumeLevel.HIGH, 1.9))
    assert decision.tradeable


# --- Escolha do operacional ----------------------------------------------


def test_trend_with_strong_volume_picks_momentum() -> None:
    decision = select(reg=regime(Trend.UP), vol=volume(VolumeLevel.HIGH, 1.8))
    assert decision.kind == PlaybookKind.MOMENTUM
    assert decision.strategy_name == "momentum_continuation"


def test_calm_trend_picks_pullback() -> None:
    decision = select(reg=regime(Trend.DOWN), vol=volume(VolumeLevel.NORMAL))
    assert decision.kind == PlaybookKind.TREND_PULLBACK
    assert decision.strategy_name == "trend_pullback"


def test_fresh_trend_change_picks_crossover() -> None:
    decision = select(reg=regime(Trend.UP, is_transition=True))
    assert decision.kind == PlaybookKind.TREND_CROSSOVER


def test_sideways_calm_market_picks_mean_reversion() -> None:
    decision = select(reg=regime(Trend.SIDEWAYS, VolatilityLevel.NORMAL))
    assert decision.kind == PlaybookKind.MEAN_REVERSION


def test_sideways_volatile_market_picks_breakout() -> None:
    decision = select(reg=regime(Trend.SIDEWAYS, VolatilityLevel.HIGH))
    assert decision.kind == PlaybookKind.BREAKOUT


def test_session_opening_picks_breakout() -> None:
    # 07:15 UTC: Londres acabou de abrir.
    decision = select(now=WEDNESDAY.replace(hour=7, minute=15))
    assert decision.kind == PlaybookKind.BREAKOUT


def test_every_tradeable_kind_maps_to_a_registered_strategy() -> None:
    from app.execution.playbook import PLAYBOOK_PROFILES
    from app.strategies.registry import STRATEGY_NAMES

    for kind, profile in PLAYBOOK_PROFILES.items():
        if kind == PlaybookKind.STAND_ASIDE:
            assert profile.strategy_name is None
        else:
            assert profile.strategy_name in STRATEGY_NAMES


# --- Timeframe -------------------------------------------------------------


def test_prime_session_with_strong_volume_uses_fast_timeframe() -> None:
    decision = select(vol=volume(VolumeLevel.HIGH, 1.8))
    assert decision.timeframe == "M5"


def test_low_volume_uses_slow_timeframe() -> None:
    decision = select(vol=volume(VolumeLevel.LOW, 0.5))
    assert decision.timeframe == "M30"


def test_timeframe_falls_back_to_what_is_available() -> None:
    decision = select(vol=volume(VolumeLevel.HIGH, 1.8), available_timeframes=("M15",))
    assert decision.timeframe == "M15"


# --- Invariantes de seguranca ---------------------------------------------


@pytest.mark.parametrize("rating_now", [OVERLAP, TOKYO_ONLY, LATE_FRIDAY, SATURDAY])
@pytest.mark.parametrize("level", list(VolumeLevel))
@pytest.mark.parametrize("trend", list(Trend))
def test_threshold_never_drops_below_configured(rating_now, level, trend) -> None:
    base = 88.0
    decision = select(
        now=rating_now, vol=volume(level), reg=regime(trend), threshold=base
    )
    assert decision.analysis_threshold >= base
    assert decision.analysis_threshold <= MAX_THRESHOLD


@pytest.mark.parametrize("rating_now", [OVERLAP, TOKYO_ONLY, LATE_FRIDAY, SATURDAY])
@pytest.mark.parametrize("level", list(VolumeLevel))
def test_risk_factor_never_exceeds_configured_risk(rating_now, level) -> None:
    decision = select(now=rating_now, vol=volume(level))
    assert 0.0 <= decision.risk_factor <= 1.0


def test_less_favourable_context_raises_the_bar() -> None:
    prime = select(vol=volume(VolumeLevel.HIGH, 1.8), threshold=80.0)
    quiet = select(now=TOKYO_ONLY, vol=volume(VolumeLevel.HIGH, 1.6), threshold=80.0)
    assert quiet.analysis_threshold > prime.analysis_threshold
    assert quiet.risk_factor < prime.risk_factor


def test_stand_aside_carries_zero_risk() -> None:
    assert select(now=SATURDAY).risk_factor == 0.0


def test_fit_score_is_bounded_and_higher_in_prime_conditions() -> None:
    prime = select(vol=volume(VolumeLevel.HIGH, 1.8))
    weaker = select(now=TOKYO_ONLY, vol=volume(VolumeLevel.HIGH, 1.6))
    assert 0.0 <= weaker.fit_score <= prime.fit_score <= 100.0


def test_decision_always_explains_itself() -> None:
    for decision in (select(), select(now=SATURDAY), select(vol=volume(VolumeLevel.DEAD))):
        assert decision.reasons or decision.blockers
        assert decision.headline
