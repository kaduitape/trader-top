from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.exceptions import MT5RealAccountError
from app.mt5.account import AccountSnapshot
from app.mt5.orders import fetch_history_deals, fetch_pending_orders, send_market_order
from app.strategies.base import SignalDirection
from tests.fixtures.fake_mt5_client import FakeMT5Client, make_order_send_result

_DEMO_ACCOUNT = AccountSnapshot(
    login=1,
    server="Test-Demo",
    balance=10_000.0,
    equity=10_000.0,
    margin=0.0,
    margin_free=10_000.0,
    currency="USD",
    leverage=100,
    trade_mode=0,
    is_demo=True,
)

_REAL_ACCOUNT = AccountSnapshot(
    login=2,
    server="Test-Real",
    balance=10_000.0,
    equity=10_000.0,
    margin=0.0,
    margin_free=10_000.0,
    currency="USD",
    leverage=100,
    trade_mode=2,
    is_demo=False,
)


def test_fetch_pending_orders_converts_rows() -> None:
    client = FakeMT5Client()
    client.orders_get_result = (
        SimpleNamespace(
            ticket=10,
            symbol="EURUSD",
            volume_initial=0.1,
            volume_current=0.1,
            price_open=1.1000,
            sl=1.0950,
            tp=1.1050,
            type=2,
            state=1,
            time_setup=1_700_000_000,
            magic=42,
            comment="pending",
        ),
    )

    orders = fetch_pending_orders(client)

    assert len(orders) == 1
    assert orders[0].ticket == 10
    assert orders[0].sl == 1.0950
    assert orders[0].tp == 1.1050


def test_fetch_pending_orders_returns_empty_when_none() -> None:
    client = FakeMT5Client()
    client.orders_get_result = None
    client.last_error_result = (-10, "no ipc connection")

    assert fetch_pending_orders(client) == []


def test_fetch_history_deals_converts_rows() -> None:
    client = FakeMT5Client()
    client.history_deals_get_result = (
        SimpleNamespace(
            ticket=99,
            order=10,
            symbol="EURUSD",
            volume=0.1,
            price=1.1010,
            profit=5.0,
            type=0,
            entry=1,
            time=1_700_000_100,
            magic=42,
            comment="close",
        ),
    )

    deals = fetch_history_deals(
        client,
        datetime.fromtimestamp(0, tz=UTC),
        datetime.fromtimestamp(2_000_000_000, tz=UTC),
    )

    assert len(deals) == 1
    assert deals[0].ticket == 99
    assert deals[0].profit == 5.0


def test_fetch_history_deals_returns_empty_when_none() -> None:
    client = FakeMT5Client()
    client.history_deals_get_result = None
    client.last_error_result = (-10, "no ipc connection")

    assert (
        fetch_history_deals(
            client,
            datetime.fromtimestamp(0, tz=UTC),
            datetime.fromtimestamp(1, tz=UTC),
        )
        == []
    )


def test_fetch_history_deals_converts_position_id() -> None:
    client = FakeMT5Client()
    client.history_deals_get_result = (
        SimpleNamespace(
            ticket=99,
            order=10,
            position_id=3001,
            symbol="EURUSD",
            volume=0.1,
            price=1.1010,
            profit=5.0,
            type=0,
            entry=1,
            time=1_700_000_100,
            magic=42,
            comment="close",
        ),
    )

    deals = fetch_history_deals(
        client,
        datetime.fromtimestamp(0, tz=UTC),
        datetime.fromtimestamp(2_000_000_000, tz=UTC),
    )

    assert deals[0].position_id == 3001


def test_send_market_order_refuses_real_account() -> None:
    client = FakeMT5Client()

    with pytest.raises(MT5RealAccountError):
        send_market_order(
            client,
            account=_REAL_ACCOUNT,
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            volume=0.01,
            price=1.1000,
            stop_loss=1.0990,
            take_profit=1.1050,
            magic=1,
        )

    assert client.order_send_calls == []


def test_send_market_order_success_on_demo_account() -> None:
    client = FakeMT5Client()
    client.order_send_result = make_order_send_result(
        retcode=client.TRADE_RETCODE_DONE, order=1001, deal=2001, position=3001, price=1.1000
    )

    result = send_market_order(
        client,
        account=_DEMO_ACCOUNT,
        symbol="EURUSD",
        direction=SignalDirection.LONG,
        volume=0.01,
        price=1.1000,
        stop_loss=1.0990,
        take_profit=1.1050,
        magic=7,
        comment="test",
    )

    assert result.success is True
    assert result.order_ticket == 1001
    assert result.position_ticket == 3001
    assert result.price == 1.1000

    assert len(client.order_send_calls) == 1
    request = client.order_send_calls[0]
    assert request["symbol"] == "EURUSD"
    assert request["volume"] == 0.01
    assert request["sl"] == 1.0990
    assert request["tp"] == 1.1050
    assert request["type"] == client.ORDER_TYPE_BUY
    assert request["magic"] == 7


def test_send_market_order_uses_sell_type_for_short() -> None:
    client = FakeMT5Client()
    client.order_send_result = make_order_send_result()

    send_market_order(
        client,
        account=_DEMO_ACCOUNT,
        symbol="EURUSD",
        direction=SignalDirection.SHORT,
        volume=0.01,
        price=1.1000,
        stop_loss=1.1010,
        take_profit=1.0950,
        magic=7,
    )

    assert client.order_send_calls[0]["type"] == client.ORDER_TYPE_SELL


def test_send_market_order_rejected_by_broker() -> None:
    client = FakeMT5Client()
    client.order_send_result = make_order_send_result(retcode=10004, comment="Requote")

    result = send_market_order(
        client,
        account=_DEMO_ACCOUNT,
        symbol="EURUSD",
        direction=SignalDirection.LONG,
        volume=0.01,
        price=1.1000,
        stop_loss=1.0990,
        take_profit=1.1050,
        magic=7,
    )

    assert result.success is False
    assert result.retcode == 10004
    assert result.price is None
    assert result.comment == "Requote"


def test_send_market_order_handles_none_result() -> None:
    client = FakeMT5Client()
    client.order_send_result = None
    client.last_error_result = (-10, "no ipc connection")

    result = send_market_order(
        client,
        account=_DEMO_ACCOUNT,
        symbol="EURUSD",
        direction=SignalDirection.LONG,
        volume=0.01,
        price=1.1000,
        stop_loss=1.0990,
        take_profit=1.1050,
        magic=7,
    )

    assert result.success is False
    assert result.retcode == -10
    assert result.comment == "no ipc connection"
