"""Adaptador cTrader como `BrokerPort`.

O foco aqui e o que o motor de execucao enxerga — e, acima de tudo, que a
guarda de coerencia entre modo configurado e tipo de conta vale na cTrader
exatamente como vale no MetaTrader, nos dois sentidos.
"""

from __future__ import annotations

import pytest

from app.broker.ctrader.adapter import CTraderBroker
from app.broker.ctrader.protocol import PayloadType
from app.broker.port import BrokerAccountMismatchError, BrokerError, OrderRequest
from app.strategies.base import SignalDirection

POSICAO_ABERTA = {
    "positionId": 987654,
    "price": 1.08412,
    "stopLoss": 1.08260,
    "takeProfit": 1.08890,
    "tradeData": {
        "symbolId": 1,
        "volume": 500_000,
        "tradeSide": 1,
        "openTimestamp": 1_780_000_000_000,
    },
}


def broker(client, **kwargs) -> CTraderBroker:
    return CTraderBroker(client, **kwargs)


def test_the_port_reports_its_name(client) -> None:
    assert broker(client).name == "ctrader"


def test_account_is_converted_from_cents(client) -> None:
    conta = broker(client).account()

    assert conta.balance == pytest.approx(10_000.0)
    assert conta.is_demo is True
    assert conta.leverage == 30


def test_demo_mode_refuses_a_real_account(client, transport) -> None:
    transport._respostas[int(PayloadType.TRADER_REQ)]["payload"]["trader"]["isLive"] = True

    with pytest.raises(BrokerAccountMismatchError, match="modo DEMO com conta REAL"):
        broker(client, allow_real_account=False).account()


def test_real_mode_refuses_a_demo_account(client) -> None:
    """O sentido inverso importa igual: o operador acredita estar
    arriscando dinheiro e nao esta."""
    with pytest.raises(BrokerAccountMismatchError, match="modo REAL com conta demo"):
        broker(client, allow_real_account=True).account()


def test_an_unknown_account_type_is_refused_instead_of_guessed(client, transport) -> None:
    """Chutar aqui e chutar se o dinheiro e de verdade."""
    del transport._respostas[int(PayloadType.TRADER_REQ)]["payload"]["trader"]["isLive"]

    with pytest.raises(BrokerError, match="nao informou se a conta e demo"):
        broker(client).account()


def test_a_configured_expectation_covers_a_silent_broker(client, transport) -> None:
    del transport._respostas[int(PayloadType.TRADER_REQ)]["payload"]["trader"]["isLive"]

    conta = broker(client, expect_demo=True).account()

    assert conta.is_demo is True


def test_the_account_is_checked_before_any_order_is_sent(client, transport) -> None:
    """A guarda so protege se rodar ANTES do envio."""
    transport._respostas[int(PayloadType.TRADER_REQ)]["payload"]["trader"]["isLive"] = True

    with pytest.raises(BrokerAccountMismatchError):
        broker(client).send_market_order(
            OrderRequest(
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                volume_lots=0.05,
                stop_loss=1.08260,
                take_profit=1.08890,
            )
        )

    assert transport.enviados(PayloadType.NEW_ORDER_REQ) == []


def test_a_successful_order_returns_the_position_id(client) -> None:
    resultado = broker(client).send_market_order(
        OrderRequest(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            volume_lots=0.05,
            stop_loss=1.08260,
            take_profit=1.08890,
        )
    )

    assert resultado.accepted is True
    assert resultado.position_id == "987654"
    assert resultado.price == pytest.approx(1.08412)


def test_an_order_without_a_position_is_not_reported_as_accepted(client, transport) -> None:
    """Ordem aceita mas nao preenchida nao e posicao aberta — dizer que foi
    faria o sistema acreditar que esta no mercado quando nao esta."""
    transport._respostas[int(PayloadType.NEW_ORDER_REQ)] = {
        "payload": {"executionType": 2, "order": {}}
    }

    resultado = broker(client).send_market_order(
        OrderRequest(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            volume_lots=0.05,
            stop_loss=1.08260,
            take_profit=1.08890,
        )
    )

    assert resultado.accepted is False
    assert resultado.position_id is None


def test_open_positions_are_translated_to_lots_and_names(client, transport) -> None:
    transport._respostas[int(PayloadType.RECONCILE_REQ)] = {
        "payload": {"position": [POSICAO_ABERTA]}
    }

    posicoes = broker(client).open_positions()

    assert len(posicoes) == 1
    posicao = posicoes[0]
    assert posicao.position_id == "987654"
    assert posicao.symbol == "EURUSD"
    assert posicao.direction == SignalDirection.LONG
    assert posicao.volume_lots == pytest.approx(0.05)
    assert posicao.stop_loss == pytest.approx(1.08260)
    assert posicao.opened_at is not None


def test_positions_can_be_filtered_by_symbol(client, transport) -> None:
    outra = dict(POSICAO_ABERTA)
    outra["positionId"] = 111
    outra["tradeData"] = {**POSICAO_ABERTA["tradeData"], "symbolId": 41}
    transport._respostas[int(PayloadType.RECONCILE_REQ)] = {
        "payload": {"position": [POSICAO_ABERTA, outra]}
    }

    assert len(broker(client).open_positions("XAUUSD")) == 1
    assert broker(client).open_positions("XAUUSD")[0].symbol == "XAUUSD"


def test_protection_change_reaches_the_open_position(client, transport) -> None:
    transport._respostas[int(PayloadType.RECONCILE_REQ)] = {
        "payload": {"position": [POSICAO_ABERTA]}
    }

    resultado = broker(client).modify_protection(
        "987654", stop_loss=1.08412, take_profit=1.08890
    )

    assert resultado.accepted is True
    pedido = transport.enviados(PayloadType.AMEND_POSITION_SLTP_REQ)[0]
    assert pedido["positionId"] == 987654


def test_changing_protection_of_a_closed_position_fails_clearly(client) -> None:
    with pytest.raises(BrokerError, match="nao esta mais aberta"):
        broker(client).modify_protection("42", stop_loss=1.0, take_profit=2.0)


def test_a_non_numeric_position_id_fails_before_the_network(client, transport) -> None:
    with pytest.raises(BrokerError, match="positionId cTrader invalido"):
        broker(client).modify_protection("abc", stop_loss=1.0, take_profit=2.0)
    assert transport.enviados(PayloadType.AMEND_POSITION_SLTP_REQ) == []
