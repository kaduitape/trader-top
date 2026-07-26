"""Aplicacao real do trailing stop / break-even (`app.execution.position_manager`).

Cobre o que separa "calculado" de "aplicado": a ordem de modificacao chega
ao MetaTrader, o nivel novo e persistido SO quando a corretora aceita, e a
conta real continua bloqueada.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.apexflow.config import ApexFlowConfig
from app.core.exceptions import MT5RealAccountError
from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.execution.order_state import OrderState
from app.execution.position_manager import (
    StopMoveOutcome,
    manage_open_position,
)
from app.mt5.orders import modify_position
from tests.fixtures.fake_mt5_client import (
    FakeMT5Client,
    make_order_send_result,
    make_position,
)
from tests.unit.execution.test_autopilot import (
    DEMO_ACCOUNT,
    REAL_ACCOUNT,
    SPEC,
    seed_symbol,
)

CONFIG = ApexFlowConfig(break_even_r=0.8, trailing_start_r=1.0, trailing_step_r=0.5)
SYMBOL = SPEC.name
ENTRY = 1.1000
STOP = 1.0980  # 20 pontos de risco
TICKET = 555


def open_trade(db_session, *, entry=ENTRY, stop=STOP, direction="LONG"):
    symbol_id = seed_symbol(db_session)
    trade = LiveTradeRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe="M5",
        strategy_name="autopilot",
        model_version="test",
        signal_id=f"sig-{direction}-{stop}",
        direction=direction,
        order_state=OrderState.POSITION_OPEN,
        signal_time=datetime(2026, 7, 22, 14, 0),
        entry_time=datetime(2026, 7, 22, 14, 0),
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(stop)),
        take_profit=Decimal("1.1060"),
        volume=Decimal("0.10"),
        mt5_position_ticket=TICKET,
    )
    db_session.flush()
    return trade


def client_with_position(current_price: float, *, accepted: bool = True) -> FakeMT5Client:
    client = FakeMT5Client()
    client.positions_get_result = (
        make_position(ticket=TICKET, symbol=SYMBOL, price_current=current_price),
    )
    client.order_send_result = make_order_send_result(
        retcode=FakeMT5Client.TRADE_RETCODE_DONE if accepted else 10016,
        comment="ok" if accepted else "invalid stops",
    )
    return client


# --- modify_position -------------------------------------------------------


def test_modify_position_refuses_a_real_account() -> None:
    with pytest.raises(MT5RealAccountError):
        modify_position(
            FakeMT5Client(),
            account=REAL_ACCOUNT,
            symbol=SYMBOL,
            position_ticket=TICKET,
            stop_loss=ENTRY,
            take_profit=1.1060,
        )


def test_modify_position_sends_only_protection_levels() -> None:
    client = client_with_position(1.1020)
    result = modify_position(
        client,
        account=DEMO_ACCOUNT,
        symbol=SYMBOL,
        position_ticket=TICKET,
        stop_loss=ENTRY,
        take_profit=1.1060,
    )
    assert result.success
    request = client.order_send_calls[-1]
    assert request["position"] == TICKET
    assert request["sl"] == ENTRY
    # Nunca volume/type/price: nao existe caminho para abrir ou fechar aqui.
    assert "volume" not in request
    assert "type" not in request


def test_modify_position_reports_broker_rejection() -> None:
    client = client_with_position(1.1020, accepted=False)
    result = modify_position(
        client,
        account=DEMO_ACCOUNT,
        symbol=SYMBOL,
        position_ticket=TICKET,
        stop_loss=ENTRY,
        take_profit=1.1060,
    )
    assert not result.success
    assert "invalid stops" in result.comment


# --- manage_open_position --------------------------------------------------


def manage(db_session, trade, client, *, account=DEMO_ACCOUNT):
    return manage_open_position(
        db_session, client, trade, account=account, symbol=SYMBOL, config=CONFIG
    )


def test_no_move_before_the_break_even_threshold(db_session) -> None:
    trade = open_trade(db_session)
    client = client_with_position(1.1005)  # 0.25R
    report = manage(db_session, trade, client)
    assert report.outcome == StopMoveOutcome.NOT_NEEDED
    assert client.order_send_calls == []
    assert float(trade.stop_loss) == pytest.approx(STOP)


def test_break_even_is_applied_and_persisted(db_session) -> None:
    trade = open_trade(db_session)
    client = client_with_position(1.1018)  # 0.9R
    report = manage(db_session, trade, client)
    assert report.moved
    assert client.order_send_calls
    assert float(trade.stop_loss) == pytest.approx(ENTRY)
    # O risco ORIGINAL fica preservado para o calculo de R.
    assert float(trade.initial_stop_loss) == pytest.approx(STOP)


def test_trailing_locks_profit_and_persists(db_session) -> None:
    trade = open_trade(db_session)
    client = client_with_position(1.1040)  # 2R
    report = manage(db_session, trade, client)
    assert report.moved
    assert float(trade.stop_loss) > ENTRY


def test_rejected_modification_keeps_the_old_stop(db_session) -> None:
    """Gravar um nivel recusado faria o sistema acreditar em uma protecao
    que nao existe."""
    trade = open_trade(db_session)
    client = client_with_position(1.1040, accepted=False)
    report = manage(db_session, trade, client)
    assert report.outcome == StopMoveOutcome.REJECTED
    assert float(trade.stop_loss) == pytest.approx(STOP)


def test_real_account_is_refused_even_with_a_valid_intent(db_session) -> None:
    trade = open_trade(db_session)
    client = client_with_position(1.1040)
    with pytest.raises(MT5RealAccountError):
        manage(db_session, trade, client, account=REAL_ACCOUNT)
    assert float(trade.stop_loss) == pytest.approx(STOP)


def test_position_absent_from_broker_is_reported_not_modified(db_session) -> None:
    trade = open_trade(db_session)
    client = FakeMT5Client()
    client.positions_get_result = ()
    report = manage(db_session, trade, client)
    assert report.outcome == StopMoveOutcome.UNAVAILABLE
    assert client.order_send_calls == []


def test_trade_without_entry_price_is_reported_unavailable(db_session) -> None:
    trade = open_trade(db_session)
    trade.entry_price = None
    db_session.flush()
    report = manage(db_session, trade, client_with_position(1.1040))
    assert report.outcome == StopMoveOutcome.UNAVAILABLE


def test_short_position_trails_downwards(db_session) -> None:
    trade = open_trade(db_session, entry=1.1000, stop=1.1020, direction="SHORT")
    client = client_with_position(1.0960)  # 2R para uma venda
    report = manage(db_session, trade, client)
    assert report.moved
    assert float(trade.stop_loss) < ENTRY


def test_stop_never_moves_backwards(db_session) -> None:
    """Preco recuou depois de um trailing anterior: a proposta seria pior
    que o stop atual e precisa ser recusada."""
    trade = open_trade(db_session, stop=1.1030)
    client = client_with_position(1.1022)
    report = manage(db_session, trade, client)
    assert report.outcome == StopMoveOutcome.NOT_NEEDED
    assert client.order_send_calls == []


def test_every_report_explains_itself(db_session) -> None:
    trade = open_trade(db_session)
    for price in (1.1005, 1.1018, 1.1040):
        report = manage(db_session, trade, client_with_position(price))
        assert report.message
