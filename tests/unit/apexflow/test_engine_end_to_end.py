"""Orquestrador ApexFlow (`app.apexflow.engine`) contra um banco real.

Usa o SQLite em memoria da suite: candles e ticks sao gravados como o
worker gravaria, e o motor os le pelo mesmo caminho que usaria em
producao. Sem terminal MetaTrader e sem rede.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.apexflow.config import ApexFlowConfig
from app.apexflow.decision import DecisionAction
from app.apexflow.engine import analyze
from app.apexflow.mtf import UnsupportedEntryTimeframeError
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.tick_repository import TickRepository
from app.market.multi_timeframe import SymbolNotFoundError
from app.mt5.market_data import RawCandle, RawTick, Timeframe
from app.mt5.symbol_mapper import SymbolSpecification

NOW = datetime(2026, 7, 22, 14, 10, tzinfo=UTC)
POINT = 0.0001
CONFIG = ApexFlowConfig(min_confidence=0.80, min_atr_points=5.0)


def seed_symbol(db_session, name: str) -> int:
    symbol = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=name,
            description="Test",
            digits=5,
            point=POINT,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100_000.0,
            spread=2,
            trade_mode=4,
            visible=True,
        )
    )
    db_session.flush()
    return symbol.id


def seed_candles(
    db_session,
    symbol_id: int,
    timeframe: Timeframe,
    *,
    minutes: int,
    count: int = 260,
    step: float = 0.0004,
    amplitude: float = 0.0010,
) -> None:
    candles: list[RawCandle] = []
    price = 1.1000
    start = NOW - timedelta(minutes=minutes * count)
    for index in range(count):
        price += step
        candles.append(
            RawCandle(
                open_time=(start + timedelta(minutes=minutes * index)).replace(tzinfo=None),
                open=price,
                high=price + amplitude,
                low=price - amplitude,
                close=price + amplitude / 2,
                tick_volume=150,
                spread=2,
                real_volume=0,
            )
        )
    CandleRepository(db_session).bulk_upsert(symbol_id, timeframe.value, candles)
    db_session.flush()


def seed_all_timeframes(db_session, symbol_id: int) -> None:
    for timeframe, minutes in (
        (Timeframe.H1, 60),
        (Timeframe.M15, 15),
        (Timeframe.M5, 5),
        (Timeframe.M1, 1),
    ):
        seed_candles(db_session, symbol_id, timeframe, minutes=minutes)


def seed_ticks(db_session, symbol_id: int, *, count: int = 120, step: float = 0.00001) -> None:
    ticks: list[RawTick] = []
    price = 1.1000
    for index in range(count):
        price += step
        ticks.append(
            RawTick(
                timestamp=(NOW - timedelta(seconds=count - index)).replace(tzinfo=None),
                bid=price,
                ask=price + 0.0002,
                last=price,
                volume=1.0,
                flags=0,
            )
        )
    TickRepository(db_session).bulk_upsert(symbol_id, ticks)
    db_session.flush()


# --- Erros reais vs. condicoes de mercado ---------------------------------


def test_unknown_symbol_raises(db_session) -> None:
    with pytest.raises(SymbolNotFoundError):
        analyze(
            db_session,
            symbol="NAO_EXISTE_XYZ",
            timeframe=Timeframe.M5,
            config=CONFIG,
            now=NOW,
        )


def test_h1_entry_is_refused_by_architecture(db_session) -> None:
    seed_symbol(db_session, "EURUSD_E2E_1")
    with pytest.raises(UnsupportedEntryTimeframeError):
        analyze(
            db_session,
            symbol="EURUSD_E2E_1",
            timeframe=Timeframe.H1,
            config=CONFIG,
            now=NOW,
        )


def test_symbol_without_data_abstains_instead_of_raising(db_session) -> None:
    """Falta de dado de mercado nunca e excecao — vira abstencao explicada."""
    seed_symbol(db_session, "EURUSD_E2E_2")
    analysis = analyze(
        db_session, symbol="EURUSD_E2E_2", timeframe=Timeframe.M5, config=CONFIG, now=NOW
    )
    assert analysis.decision.action == DecisionAction.NO_TRADE
    assert analysis.decision.vetoes
    assert analysis.warnings


# --- Ciclo completo --------------------------------------------------------


def test_full_cycle_produces_a_complete_analysis(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_E2E_3")
    seed_all_timeframes(db_session, symbol_id)
    seed_ticks(db_session, symbol_id)

    analysis = analyze(
        db_session, symbol="EURUSD_E2E_3", timeframe=Timeframe.M5, config=CONFIG, now=NOW
    )

    assert analysis.symbol == "EURUSD_E2E_3"
    assert analysis.timeframe == Timeframe.M5
    # Todas as leituras intermediarias viajam junto, para o painel poder
    # explicar o "porque" sem recalcular nada.
    assert analysis.flow.tick_count > 0
    assert analysis.spread.spread_points is not None
    assert analysis.volatility.atr_points is not None
    assert analysis.momentum.state is not None
    assert analysis.mtf.coverage > 0
    assert analysis.session.symbol == "EURUSD_E2E_3"
    assert analysis.vector.completeness > 0
    assert analysis.decision.action in (
        DecisionAction.BUY,
        DecisionAction.SELL,
        DecisionAction.NO_TRADE,
    )


def test_probabilities_are_recorded_even_when_not_trading(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_E2E_4")
    seed_all_timeframes(db_session, symbol_id)
    seed_ticks(db_session, symbol_id)

    analysis = analyze(
        db_session,
        symbol="EURUSD_E2E_4",
        timeframe=Timeframe.M5,
        config=ApexFlowConfig(min_confidence=0.99, min_atr_points=5.0),
        now=NOW,
    )
    assert analysis.decision.action == DecisionAction.NO_TRADE
    total = (
        analysis.decision.probability_buy
        + analysis.decision.probability_sell
        + analysis.decision.probability_abstain
    )
    assert total == pytest.approx(1.0, abs=0.001)
    assert analysis.decision.reasons


def test_missing_ticks_are_flagged_and_block_entry(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_E2E_5")
    seed_all_timeframes(db_session, symbol_id)

    analysis = analyze(
        db_session, symbol="EURUSD_E2E_5", timeframe=Timeframe.M5, config=CONFIG, now=NOW
    )
    assert any("tick" in warning.lower() for warning in analysis.warnings)
    assert analysis.decision.action == DecisionAction.NO_TRADE
    assert any("fluxo" in veto.lower() for veto in analysis.decision.vetoes)


def test_volatility_floor_blocks_a_flat_market(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_E2E_6")
    for timeframe, minutes in (
        (Timeframe.H1, 60),
        (Timeframe.M15, 15),
        (Timeframe.M5, 5),
        (Timeframe.M1, 1),
    ):
        seed_candles(
            db_session, symbol_id, timeframe, minutes=minutes, step=0.0, amplitude=0.00002
        )
    seed_ticks(db_session, symbol_id, step=0.0)

    analysis = analyze(
        db_session,
        symbol="EURUSD_E2E_6",
        timeframe=Timeframe.M5,
        config=ApexFlowConfig(min_confidence=0.6, min_atr_points=50.0),
        now=NOW,
    )
    assert analysis.decision.action == DecisionAction.NO_TRADE
    assert analysis.decision.vetoes


def test_analysis_is_deterministic_for_the_same_inputs(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_E2E_7")
    seed_all_timeframes(db_session, symbol_id)
    seed_ticks(db_session, symbol_id)

    first = analyze(
        db_session, symbol="EURUSD_E2E_7", timeframe=Timeframe.M5, config=CONFIG, now=NOW
    )
    second = analyze(
        db_session, symbol="EURUSD_E2E_7", timeframe=Timeframe.M5, config=CONFIG, now=NOW
    )
    assert first.decision.action == second.decision.action
    assert first.decision.probability_buy == second.decision.probability_buy
    assert first.vector.as_list() == second.vector.as_list()
