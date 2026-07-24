from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.market.multi_timeframe import (
    ANALYSIS_TIMEFRAMES,
    SymbolNotFoundError,
    build_multi_timeframe_snapshot,
)
from app.mt5.market_data import RawCandle, Timeframe
from app.mt5.symbol_mapper import SymbolSpecification

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _spec(name: str) -> SymbolSpecification:
    return SymbolSpecification(
        name=name,
        description="Test symbol",
        digits=5,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=2,
        trade_mode=4,
        visible=True,
    )


def _make_candles(n: int, *, start: datetime, step: timedelta, seed: int = 7) -> list[RawCandle]:
    rng = np.random.default_rng(seed)
    price = 100.0
    candles: list[RawCandle] = []
    for i in range(n):
        move = rng.normal(0, 0.05)
        open_ = price
        close = price + move
        high = max(open_, close) + abs(rng.normal(0, 0.02))
        low = min(open_, close) - abs(rng.normal(0, 0.02))
        candles.append(
            RawCandle(
                open_time=start + step * i,
                open=open_,
                high=high,
                low=low,
                close=close,
                tick_volume=int(100 + rng.integers(0, 50)),
                spread=int(2 + rng.integers(0, 3)),
                real_volume=0,
            )
        )
        price = close
    return candles


def _seed_symbol_with_timeframes(db_session, name: str, coverage: dict[Timeframe, int]) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec(name))
    repo = CandleRepository(db_session)
    for timeframe, count in coverage.items():
        candles = _make_candles(count, start=_NOW - timedelta(days=1), step=timedelta(minutes=1))
        repo.bulk_upsert(symbol.id, timeframe.value, candles)
    db_session.flush()


def test_unknown_symbol_raises_not_found(db_session) -> None:
    with pytest.raises(SymbolNotFoundError):
        build_multi_timeframe_snapshot(db_session, symbol="DOES_NOT_EXIST", now=_NOW)


def test_all_timeframes_sufficient_when_fully_collected(db_session) -> None:
    coverage = dict.fromkeys(ANALYSIS_TIMEFRAMES, 260)
    _seed_symbol_with_timeframes(db_session, "MTF_FULL", coverage)

    snapshot = build_multi_timeframe_snapshot(db_session, symbol="MTF_FULL", now=_NOW)

    assert snapshot.symbol == "MTF_FULL"
    assert snapshot.generated_at == _NOW
    assert set(snapshot.sufficient_timeframes()) == set(ANALYSIS_TIMEFRAMES)
    for tf in ANALYSIS_TIMEFRAMES:
        tf_snapshot = snapshot.get(tf)
        assert tf_snapshot is not None
        assert tf_snapshot.is_sufficient
        assert tf_snapshot.features is not None
        assert not tf_snapshot.warnings


def test_partial_coverage_becomes_warning_not_exception(db_session) -> None:
    coverage = {
        Timeframe.M1: 260,
        Timeframe.M5: 260,
        Timeframe.M15: 260,
        Timeframe.H1: 260,
    }
    _seed_symbol_with_timeframes(db_session, "MTF_PARTIAL", coverage)

    snapshot = build_multi_timeframe_snapshot(db_session, symbol="MTF_PARTIAL", now=_NOW)

    sufficient = snapshot.sufficient_timeframes()
    assert set(sufficient) == {Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1}

    for tf in (Timeframe.MN1, Timeframe.W1, Timeframe.D1, Timeframe.H4, Timeframe.M30):
        tf_snapshot = snapshot.get(tf)
        assert tf_snapshot is not None
        assert not tf_snapshot.is_sufficient
        assert tf_snapshot.bars_available == 0
        assert tf_snapshot.features is None
        assert tf_snapshot.warnings


def test_single_candle_timeframe_is_insufficient_and_has_no_features(db_session) -> None:
    _seed_symbol_with_timeframes(db_session, "MTF_ONE_BAR", {Timeframe.M1: 1})

    snapshot = build_multi_timeframe_snapshot(
        db_session, symbol="MTF_ONE_BAR", timeframes=(Timeframe.M1,), now=_NOW
    )

    tf_snapshot = snapshot.get(Timeframe.M1)
    assert tf_snapshot is not None
    assert tf_snapshot.bars_available == 1
    assert not tf_snapshot.is_sufficient
    assert tf_snapshot.features is None


def test_zero_candles_timeframe_is_insufficient(db_session) -> None:
    SymbolRepository(db_session).upsert_from_specification(_spec("MTF_EMPTY"))
    db_session.flush()

    snapshot = build_multi_timeframe_snapshot(
        db_session, symbol="MTF_EMPTY", timeframes=(Timeframe.M1,), now=_NOW
    )

    tf_snapshot = snapshot.get(Timeframe.M1)
    assert tf_snapshot is not None
    assert tf_snapshot.bars_available == 0
    assert not tf_snapshot.is_sufficient
    assert tf_snapshot.features is None


def test_result_is_deterministic_given_explicit_now(db_session) -> None:
    _seed_symbol_with_timeframes(db_session, "MTF_DETERMINISTIC", {Timeframe.M1: 260})

    first = build_multi_timeframe_snapshot(
        db_session, symbol="MTF_DETERMINISTIC", timeframes=(Timeframe.M1,), now=_NOW
    )
    second = build_multi_timeframe_snapshot(
        db_session, symbol="MTF_DETERMINISTIC", timeframes=(Timeframe.M1,), now=_NOW
    )

    assert first.generated_at == second.generated_at == _NOW
    assert first.get(Timeframe.M1).bars_available == second.get(Timeframe.M1).bars_available
