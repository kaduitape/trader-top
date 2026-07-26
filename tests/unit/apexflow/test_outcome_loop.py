"""Loop fechado do Learning Engine: decisao -> operacao -> resultado.

O ponto destes testes e que as metricas de desempenho SAEM de zero. Antes
deste ciclo as decisoes ficavam gravadas para sempre sem resultado, e win
rate / profit factor / expectancia nunca deixavam "amostra insuficiente".
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.apexflow.journal import (
    r_multiple_of,
    record_decision,
    record_trade_result,
)
from app.database.repositories.apexflow_decision_repository import (
    MIN_TRADES_FOR_STATISTICS,
    ApexFlowDecisionRepository,
)
from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.execution.order_state import OrderState
from tests.unit.apexflow.conftest import market_context
from tests.unit.apexflow.test_journal import make_decision_and_vector, seed_symbol

ENTRY = 1.1000
STOP = 1.0980  # 20 pontos = 1R


def closed_trade(
    db_session,
    symbol_id: int,
    *,
    exit_price: float,
    net_pnl: float,
    direction: str = "LONG",
    initial_stop: float | None = STOP,
    trailed_stop: float | None = None,
    signal_id: str = "sig",
):
    repository = LiveTradeRepository(db_session)
    trade = repository.create(
        symbol_id=symbol_id,
        timeframe="M5",
        strategy_name="autopilot",
        model_version="test",
        signal_id=signal_id,
        direction=direction,
        order_state=OrderState.POSITION_OPEN,
        signal_time=datetime(2026, 7, 22, 14, 0),
        entry_time=datetime(2026, 7, 22, 14, 0),
        entry_price=Decimal(str(ENTRY)),
        stop_loss=Decimal(str(initial_stop)) if initial_stop is not None else None,
        volume=Decimal("0.10"),
        mt5_position_ticket=abs(hash(signal_id)) % 100_000,
    )
    if trailed_stop is not None:
        # Simula o trailing tendo movido o stop antes do fechamento.
        repository.update_stop_loss(trade, Decimal(str(trailed_stop)))
    repository.close_position(
        trade,
        exit_time=datetime(2026, 7, 22, 15, 0),
        exit_price=Decimal(str(exit_price)),
        net_pnl=Decimal(str(net_pnl)),
    )
    return trade


def link_decision(db_session, symbol_id: int, trade):
    decision, vector = make_decision_and_vector()
    record = record_decision(
        db_session,
        decision,
        vector,
        symbol_id=symbol_id,
        timeframe="M5",
        context=market_context(),
    )
    ApexFlowDecisionRepository(db_session).attach_live_trade(record, trade.id)
    return record


# --- R realizado -----------------------------------------------------------


def test_r_multiple_uses_the_original_stop_not_the_trailed_one(db_session) -> None:
    """O trailing move `stop_loss`; 1R continua sendo o risco ORIGINAL.

    Sem `initial_stop_loss`, um stop movido para o zero a zero daria risco
    quase nulo e um R absurdamente inflado."""
    symbol_id = seed_symbol(db_session, "EURUSD_R_1")
    trade = closed_trade(
        db_session,
        symbol_id,
        exit_price=1.1040,
        net_pnl=40.0,
        trailed_stop=1.1010,
        signal_id="sig-trailed",
    )
    # 40 pontos de lucro sobre 20 pontos de risco original = 2R.
    assert r_multiple_of(trade) == pytest.approx(2.0)


def test_r_multiple_is_negative_on_a_loss(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_R_2")
    trade = closed_trade(
        db_session, symbol_id, exit_price=STOP, net_pnl=-20.0, signal_id="sig-loss"
    )
    assert r_multiple_of(trade) == pytest.approx(-1.0)


def test_r_multiple_handles_shorts(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_R_3")
    trade = closed_trade(
        db_session,
        symbol_id,
        exit_price=1.0960,
        net_pnl=40.0,
        direction="SHORT",
        initial_stop=1.1020,
        signal_id="sig-short",
    )
    assert r_multiple_of(trade) == pytest.approx(2.0)


def test_r_multiple_is_none_without_the_original_stop(db_session) -> None:
    """Operacao anterior a migration 0010: devolve None em vez de inventar."""
    symbol_id = seed_symbol(db_session, "EURUSD_R_4")
    trade = closed_trade(
        db_session, symbol_id, exit_price=1.1040, net_pnl=40.0, signal_id="sig-nostop"
    )
    trade.initial_stop_loss = None
    db_session.flush()
    assert r_multiple_of(trade) is None


# --- Loop fechado ----------------------------------------------------------


def test_result_reaches_the_decision(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_LOOP_1")
    trade = closed_trade(
        db_session, symbol_id, exit_price=1.1040, net_pnl=40.0, signal_id="sig-loop-1"
    )
    record = link_decision(db_session, symbol_id, trade)

    assert record_trade_result(db_session, trade)
    assert float(record.result_net_pnl) == pytest.approx(40.0)
    assert float(record.result_r_multiple) == pytest.approx(2.0)
    assert record.closed_at is not None


def test_trade_without_a_linked_decision_is_not_an_error(db_session) -> None:
    """Operacao aberta pelo seletor de operacional nao tem decisao ApexFlow."""
    symbol_id = seed_symbol(db_session, "EURUSD_LOOP_2")
    trade = closed_trade(
        db_session, symbol_id, exit_price=1.1040, net_pnl=40.0, signal_id="sig-loop-2"
    )
    assert record_trade_result(db_session, trade) is False


def test_open_trade_has_no_result_to_record(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_LOOP_3")
    trade = LiveTradeRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe="M5",
        strategy_name="autopilot",
        model_version="test",
        signal_id="sig-open",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=datetime(2026, 7, 22, 14, 0),
        entry_price=Decimal(str(ENTRY)),
        stop_loss=Decimal(str(STOP)),
    )
    assert record_trade_result(db_session, trade) is False


def test_statistics_come_alive_after_enough_closed_trades(db_session) -> None:
    """O teste que prova o loop: metricas saem de 'amostra insuficiente'."""
    symbol_id = seed_symbol(db_session, "EURUSD_LOOP_4")
    repository = ApexFlowDecisionRepository(db_session)

    assert not repository.performance(symbol_id=symbol_id).has_statistics

    for index in range(MIN_TRADES_FOR_STATISTICS):
        win = index % 2 == 0
        trade = closed_trade(
            db_session,
            symbol_id,
            exit_price=1.1040 if win else STOP,
            net_pnl=40.0 if win else -20.0,
            signal_id=f"sig-loop-4-{index}",
        )
        link_decision(db_session, symbol_id, trade)
        assert record_trade_result(db_session, trade)

    summary = repository.performance(symbol_id=symbol_id)
    assert summary.has_statistics
    assert summary.closed_trades == MIN_TRADES_FOR_STATISTICS
    assert summary.win_rate == pytest.approx(3 / 5)
    assert summary.profit_factor == pytest.approx(120 / 40)
    assert summary.expectancy == pytest.approx(80 / 5)
    assert summary.net_pnl == pytest.approx(80.0)


def test_recording_twice_is_idempotent_on_the_value(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_LOOP_5")
    trade = closed_trade(
        db_session, symbol_id, exit_price=1.1040, net_pnl=40.0, signal_id="sig-loop-5"
    )
    record = link_decision(db_session, symbol_id, trade)
    record_trade_result(db_session, trade)
    record_trade_result(db_session, trade)
    assert float(record.result_net_pnl) == pytest.approx(40.0)
    assert (
        ApexFlowDecisionRepository(db_session)
        .performance(symbol_id=symbol_id)
        .closed_trades
        == 1
    )
