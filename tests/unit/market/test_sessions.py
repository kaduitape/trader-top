"""Sessoes de negociacao e relevancia por moeda (`app.market.sessions`)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.market.sessions import (
    CLOSE_PROTECTION_MINUTES,
    SessionRating,
    TradingSession,
    active_sessions,
    evaluate_symbol_session,
    is_weekend_protection_window,
    market_is_open,
    minutes_to_week_close,
    opening_sessions,
    prime_sessions_for,
)

# 2026-07-22 e uma quarta-feira; 2026-07-24, uma sexta; 2026-07-25, um
# sabado; 2026-07-26, um domingo.
WEDNESDAY = datetime(2026, 7, 22, tzinfo=UTC)


def at(hour: int, minute: int = 0, *, day: datetime = WEDNESDAY) -> datetime:
    return day.replace(hour=hour, minute=minute)


def test_london_and_new_york_overlap_is_detected() -> None:
    running = active_sessions(at(14))
    assert TradingSession.LONDON in running
    assert TradingSession.NEW_YORK in running


def test_sydney_window_crosses_midnight() -> None:
    assert TradingSession.SYDNEY in active_sessions(at(23))
    assert TradingSession.SYDNEY in active_sessions(at(3))
    assert TradingSession.SYDNEY not in active_sessions(at(12))


def test_market_closed_on_saturday_and_before_sunday_open() -> None:
    saturday = datetime(2026, 7, 25, 12, tzinfo=UTC)
    sunday_morning = datetime(2026, 7, 26, 10, tzinfo=UTC)
    sunday_evening = datetime(2026, 7, 26, 22, tzinfo=UTC)
    friday_late = datetime(2026, 7, 24, 22, tzinfo=UTC)

    assert not market_is_open(saturday)
    assert not market_is_open(sunday_morning)
    assert market_is_open(sunday_evening)
    assert not market_is_open(friday_late)
    assert market_is_open(WEDNESDAY.replace(hour=12))


def test_eurusd_in_london_new_york_overlap_is_prime() -> None:
    state = evaluate_symbol_session("EURUSD", now=at(14))
    assert state.rating == SessionRating.PRIME
    assert set(state.covered_currencies) == {"EUR", "USD"}
    assert state.is_overlap


def test_eurusd_during_tokyo_only_is_quiet() -> None:
    state = evaluate_symbol_session("EURUSD", now=at(2))
    assert state.rating == SessionRating.QUIET
    assert state.covered_currencies == ()


def test_usdjpy_in_tokyo_is_active_not_prime() -> None:
    # 02:00 UTC: so Toquio e Sydney rodam. JPY esta coberto, USD nao.
    state = evaluate_symbol_session("USDJPY", now=at(2))
    assert state.rating == SessionRating.ACTIVE
    assert state.covered_currencies == ("JPY",)


def test_weekend_state_is_closed_regardless_of_pair() -> None:
    saturday = datetime(2026, 7, 25, 12, tzinfo=UTC)
    state = evaluate_symbol_session("EURUSD", now=saturday)
    assert state.rating == SessionRating.CLOSED
    assert state.active_sessions == ()
    assert not state.market_open


def test_broker_suffix_is_tolerated() -> None:
    state = evaluate_symbol_session("EURUSD.a", now=at(14))
    assert (state.base, state.quote) == ("EUR", "USD")
    assert state.rating == SessionRating.PRIME


def test_unknown_symbol_shape_is_reported_not_raised() -> None:
    state = evaluate_symbol_session("US30", now=at(14))
    assert (state.base, state.quote) == ("", "")
    assert state.rating == SessionRating.QUIET
    assert any("6 letras" in reason for reason in state.reasons)


def test_session_opening_window_is_bounded() -> None:
    assert TradingSession.LONDON in opening_sessions(at(7, 30))
    assert TradingSession.LONDON not in opening_sessions(at(9, 30))


def test_minutes_to_week_close_only_on_friday() -> None:
    friday = datetime(2026, 7, 24, 20, 30, tzinfo=UTC)
    assert minutes_to_week_close(friday) == pytest.approx(30.0)
    assert minutes_to_week_close(WEDNESDAY.replace(hour=20)) is None


def test_weekend_protection_window_flags_late_friday() -> None:
    friday = datetime(2026, 7, 24, 20, 30, tzinfo=UTC)
    state = evaluate_symbol_session("EURUSD", now=friday)
    assert is_weekend_protection_window(state)
    assert state.minutes_to_week_close is not None
    assert state.minutes_to_week_close <= CLOSE_PROTECTION_MINUTES


def test_naive_datetime_is_treated_as_utc() -> None:
    naive = datetime(2026, 7, 22, 14, 0)
    assert evaluate_symbol_session("EURUSD", now=naive).rating == SessionRating.PRIME


def test_metals_follow_london_and_new_york() -> None:
    assert prime_sessions_for("XAU") == (TradingSession.LONDON, TradingSession.NEW_YORK)
    assert evaluate_symbol_session("XAUUSD", now=at(14)).rating == SessionRating.PRIME
