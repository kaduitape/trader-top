"""Cliente da Open API: sequencia de autenticacao, catalogo e envio.

Estes testes verificam o que sairia pelo fio. A conexao em si (socket, TLS,
enquadramento) e a unica parte que exige conta real — ver
`app/broker/ctrader/transport.py`.
"""

from __future__ import annotations

import pytest

from app.broker.ctrader.protocol import PayloadType
from app.broker.port import BrokerError
from app.strategies.base import SignalDirection


def test_application_is_authorized_before_the_account(client, transport) -> None:
    """A Open API recusa autenticar a conta antes da aplicacao — a ordem
    das duas mensagens nao e estetica."""
    client.authenticate()

    assert transport.tipos() == [
        int(PayloadType.APPLICATION_AUTH_REQ),
        int(PayloadType.ACCOUNT_AUTH_REQ),
    ]


def test_authentication_happens_once_per_connection(client, transport) -> None:
    client.authenticate()
    client.authenticate()
    client.trader()

    assert transport.tipos().count(int(PayloadType.APPLICATION_AUTH_REQ)) == 1


def test_credentials_go_in_the_right_fields(client, transport) -> None:
    client.authenticate()

    app_auth = transport.enviados(PayloadType.APPLICATION_AUTH_REQ)[0]
    conta_auth = transport.enviados(PayloadType.ACCOUNT_AUTH_REQ)[0]
    assert app_auth == {"clientId": "app-id", "clientSecret": "app-secret"}
    assert conta_auth["accessToken"] == "token-da-conta"
    assert conta_auth["ctidTraderAccountId"] == 555111


def test_symbol_catalog_is_loaded_once_and_reused(client, transport) -> None:
    """Buscar o catalogo a cada ordem colocaria uma ida a rede no caminho
    mais sensivel do sistema."""
    client.load_symbols()
    client.load_symbols()

    assert transport.tipos().count(int(PayloadType.SYMBOLS_LIST_REQ)) == 1


def test_symbol_is_resolved_by_name(client) -> None:
    assert client.resolve_symbol("EURUSD").symbol_id == 1
    assert client.resolve_symbol("eurusd").symbol_id == 1


def test_broker_suffix_is_tolerated(client, transport) -> None:
    """Corretoras renomeiam instrumentos; cair fora por um sufixo deixaria
    o robo parado sem motivo real."""
    transport._respostas[int(PayloadType.SYMBOLS_LIST_REQ)] = {
        "payload": {
            "symbol": [
                {
                    "symbolId": 3,
                    "symbolName": "EURUSD.r",
                    "digits": 5,
                    "lotSize": 10_000_000,
                    "minVolume": 100_000,
                    "maxVolume": 10_000_000_000,
                    "stepVolume": 100_000,
                }
            ]
        }
    }
    assert client.resolve_symbol("EURUSD").symbol_id == 3


def test_an_ambiguous_symbol_is_refused_instead_of_guessed(client, transport) -> None:
    """Escolher "o primeiro" seria adivinhar em qual instrumento o dinheiro
    entra."""
    transport._respostas[int(PayloadType.SYMBOLS_LIST_REQ)] = {
        "payload": {
            "symbol": [
                {"symbolId": 3, "symbolName": "EURUSD.r", "lotSize": 10_000_000},
                {"symbolId": 4, "symbolName": "EURUSD.pro", "lotSize": 10_000_000},
            ]
        }
    }
    with pytest.raises(BrokerError, match="ambiguo"):
        client.resolve_symbol("EURUSD")


def test_an_unknown_symbol_says_so(client) -> None:
    with pytest.raises(BrokerError, match="nao existe"):
        client.resolve_symbol("NAOEXISTE")


def test_market_order_carries_converted_volume_and_attached_protection(client, transport) -> None:
    """O ponto central: 0,05 lote vira 500.000 cents, e stop/alvo viajam no
    MESMO pedido — nunca depois."""
    symbol = client.resolve_symbol("EURUSD")

    client.new_market_order(
        symbol=symbol,
        direction=SignalDirection.LONG,
        volume_lots=0.05,
        stop_loss=1.08260,
        take_profit=1.08890,
        label="ai-trader",
    )

    pedido = transport.enviados(PayloadType.NEW_ORDER_REQ)[0]
    assert pedido["volume"] == 500_000
    assert pedido["symbolId"] == 1
    assert pedido["tradeSide"] == 1
    assert pedido["orderType"] == 1
    assert pedido["stopLoss"] == 1.08260
    assert pedido["takeProfit"] == 1.08890
    assert pedido["label"] == "ai-trader"


def test_a_sell_uses_trade_side_two(client, transport) -> None:
    client.new_market_order(
        symbol=client.resolve_symbol("EURUSD"),
        direction=SignalDirection.SHORT,
        volume_lots=0.10,
        stop_loss=1.09,
        take_profit=1.07,
    )
    assert transport.enviados(PayloadType.NEW_ORDER_REQ)[0]["tradeSide"] == 2


def test_prices_are_rounded_to_the_symbol_digits(client, transport) -> None:
    """XAUUSD tem 2 casas; mandar 5 seria recusado pela corretora."""
    client.new_market_order(
        symbol=client.resolve_symbol("XAUUSD"),
        direction=SignalDirection.LONG,
        volume_lots=0.10,
        stop_loss=2401.234567,
        take_profit=2450.987654,
    )
    pedido = transport.enviados(PayloadType.NEW_ORDER_REQ)[0]
    assert pedido["stopLoss"] == 2401.23
    assert pedido["takeProfit"] == 2450.99


def test_a_volume_that_rounds_to_zero_is_refused_before_sending(client, transport) -> None:
    with pytest.raises(BrokerError, match="arredonda para zero"):
        client.new_market_order(
            symbol=client.resolve_symbol("EURUSD"),
            direction=SignalDirection.LONG,
            volume_lots=0.0001,
            stop_loss=1.08,
            take_profit=1.09,
        )
    assert transport.enviados(PayloadType.NEW_ORDER_REQ) == []


def test_a_volume_above_the_broker_maximum_is_refused_before_sending(client, transport) -> None:
    with pytest.raises(BrokerError, match="fora da faixa"):
        client.new_market_order(
            symbol=client.resolve_symbol("EURUSD"),
            direction=SignalDirection.LONG,
            volume_lots=500.0,
            stop_loss=1.08,
            take_profit=1.09,
        )
    assert transport.enviados(PayloadType.NEW_ORDER_REQ) == []


def test_amend_only_carries_protection_fields(client, transport) -> None:
    """A mensagem nao tem campo de volume nem de fechamento: por definicao
    do protocolo nao existe caminho daqui para encerrar posicao."""
    client.amend_protection(position_id=987654, stop_loss=1.0840, take_profit=1.0900)

    pedido = transport.enviados(PayloadType.AMEND_POSITION_SLTP_REQ)[0]
    assert set(pedido) == {
        "ctidTraderAccountId",
        "positionId",
        "stopLoss",
        "takeProfit",
    }


def test_an_api_error_becomes_a_broker_error(client, transport) -> None:
    transport._respostas[int(PayloadType.TRADER_REQ)] = {
        "payloadType": int(PayloadType.ERROR_RES),
        "payload": {"errorCode": "NOT_ENOUGH_MONEY", "description": "sem margem"},
    }
    with pytest.raises(BrokerError, match="NOT_ENOUGH_MONEY"):
        client.trader()
