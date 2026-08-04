"""Adaptador MT5 como `BrokerPort`.

Estes testes existem para garantir uma coisa: a porta nao afrouxou nada. As
protecoes que `app.mt5.orders` ja fazia continuam valendo depois do
reempacotamento — em particular a coerencia entre modo configurado e tipo de
conta, nos dois sentidos.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.broker.mt5_adapter import MT5Broker
from app.broker.port import BrokerAccountMismatchError, BrokerError, OrderRequest
from app.mt5.account import AccountSnapshot
from app.strategies.base import SignalDirection
from tests.fixtures.fake_mt5_client import (
    ACCOUNT_TRADE_MODE_DEMO,
    ACCOUNT_TRADE_MODE_REAL,
    TRADE_RETCODE_DONE,
    FakeMT5Client,
)

DEMO = AccountSnapshot(
    login=51234567,
    server="Tickmill-Demo",
    balance=10_000.0,
    equity=10_142.35,
    margin=0.0,
    margin_free=10_000.0,
    currency="USD",
    leverage=30,
    trade_mode=ACCOUNT_TRADE_MODE_DEMO,
    is_demo=True,
)

REAL = AccountSnapshot(
    login=71234567,
    server="Tickmill-Live",
    balance=5_000.0,
    equity=5_000.0,
    margin=0.0,
    margin_free=5_000.0,
    currency="USD",
    leverage=30,
    trade_mode=ACCOUNT_TRADE_MODE_REAL,
    is_demo=False,
)


def make_client() -> FakeMT5Client:
    client = FakeMT5Client()
    client.account_info_result = SimpleNamespace(
        login=DEMO.login,
        server=DEMO.server,
        balance=DEMO.balance,
        equity=DEMO.equity,
        margin=0.0,
        margin_free=DEMO.margin_free,
        currency=DEMO.currency,
        leverage=DEMO.leverage,
        trade_mode=ACCOUNT_TRADE_MODE_DEMO,
    )
    client.symbol_info_result = SimpleNamespace(
        name="EURUSD",
        digits=5,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=8,
        trade_mode=4,
        visible=True,
        description="Euro vs Dollar",
    )
    client.symbol_info_tick_result = SimpleNamespace(
        time=1_780_000_000, bid=1.08400, ask=1.08412, last=1.08406, volume=1, flags=2
    )
    client.order_send_result = SimpleNamespace(
        retcode=TRADE_RETCODE_DONE,
        order=111,
        deal=222,
        price=1.08412,
        comment="Request executed",
    )
    client.positions_get_result = [
        SimpleNamespace(
            ticket=987654,
            symbol="EURUSD",
            volume=0.05,
            price_open=1.08412,
            price_current=1.08500,
            profit=4.4,
            swap=0.0,
            type=0,
            time=1_780_000_000,
            magic=0,
            comment="",
        )
    ]
    return client


def order() -> OrderRequest:
    return OrderRequest(
        symbol="EURUSD",
        direction=SignalDirection.LONG,
        volume_lots=0.05,
        stop_loss=1.08260,
        take_profit=1.08890,
        price=1.08412,
    )


def test_the_port_reports_its_name() -> None:
    assert MT5Broker(make_client(), account=DEMO).name == "mt5"


def test_account_is_exposed_through_the_port() -> None:
    conta = MT5Broker(make_client(), account=DEMO).account()

    assert conta.login == DEMO.login
    assert conta.is_demo is True
    assert conta.balance == pytest.approx(10_000.0)


def test_open_positions_are_translated() -> None:
    posicoes = MT5Broker(make_client(), account=DEMO).open_positions()

    assert len(posicoes) == 1
    assert posicoes[0].position_id == "987654"
    assert posicoes[0].symbol == "EURUSD"
    assert posicoes[0].direction == SignalDirection.LONG
    assert posicoes[0].volume_lots == pytest.approx(0.05)


def test_a_market_order_goes_through_and_returns_the_ticket() -> None:
    client = make_client()

    resultado = MT5Broker(client, account=DEMO).send_market_order(order())

    assert resultado.accepted is True
    assert resultado.raw_code == TRADE_RETCODE_DONE
    assert client.order_send_calls, "a ordem precisa chegar ao cliente MT5"


def test_demo_mode_still_refuses_a_real_account() -> None:
    """A guarda de `app.mt5.orders` continua valendo atraves da porta."""
    with pytest.raises(BrokerAccountMismatchError):
        MT5Broker(make_client(), account=REAL, allow_real_account=False).send_market_order(
            order()
        )


def test_real_mode_still_refuses_a_demo_account() -> None:
    with pytest.raises(BrokerAccountMismatchError):
        MT5Broker(make_client(), account=DEMO, allow_real_account=True).send_market_order(
            order()
        )


def test_protection_change_finds_the_symbol_of_the_position() -> None:
    client = make_client()

    resultado = MT5Broker(client, account=DEMO).modify_protection(
        "987654", stop_loss=1.08412, take_profit=1.08890
    )

    assert resultado.accepted is True


def test_changing_protection_of_a_closed_position_fails_clearly() -> None:
    client = make_client()
    client.positions_get_result = []

    with pytest.raises(BrokerError, match="nao esta mais aberta"):
        MT5Broker(client, account=DEMO).modify_protection(
            "987654", stop_loss=1.0, take_profit=2.0
        )


def test_a_non_numeric_ticket_fails_before_touching_the_terminal() -> None:
    with pytest.raises(BrokerError, match="Ticket MT5 invalido"):
        MT5Broker(make_client(), account=DEMO).modify_protection(
            "abc", stop_loss=1.0, take_profit=2.0
        )
