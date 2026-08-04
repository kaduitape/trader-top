"""Conversoes de unidade da cTrader Open API.

Um erro aqui nao levanta excecao: manda uma ordem cem vezes maior do que a
pretendida. E o tipo de bug que so aparece no extrato.
"""

from __future__ import annotations

import pytest

from app.broker.ctrader.protocol import (
    PayloadType,
    SymbolInfo,
    envelope,
    error_text,
    is_error,
    parse_symbol,
)

# EURUSD tipico: 1 lote = 100.000 unidades = 10.000.000 "cents" na API.
EURUSD = SymbolInfo(
    symbol_id=1,
    name="EURUSD",
    digits=5,
    lot_size_cents=10_000_000,
    min_volume_cents=100_000,  # 0,01 lote
    max_volume_cents=1_000_000_000,  # 100 lotes
    step_volume_cents=100_000,
)


def test_one_lot_becomes_the_symbol_lot_size() -> None:
    """A conversao usa o `lotSize` que a corretora informou — nunca um
    100.000 chutado, que varia por instrumento."""
    assert EURUSD.lots_to_cents(1.0) == 10_000_000


def test_micro_lot_conversion() -> None:
    assert EURUSD.lots_to_cents(0.01) == 100_000


def test_volume_rounds_down_to_the_step_never_up() -> None:
    """Arredondar para cima aumentaria o risco alem do que o motor calculou."""
    assert EURUSD.lots_to_cents(0.015) == 100_000


def test_volume_below_the_step_becomes_zero_instead_of_a_surprise() -> None:
    assert EURUSD.lots_to_cents(0.001) == 0


def test_round_trip_keeps_the_lot_value() -> None:
    assert EURUSD.cents_to_lots(EURUSD.lots_to_cents(0.05)) == pytest.approx(0.05)


def test_volume_outside_the_broker_range_is_detected() -> None:
    assert not EURUSD.volume_is_tradable(EURUSD.min_volume_cents - 1)
    assert not EURUSD.volume_is_tradable(EURUSD.max_volume_cents + 1)
    assert EURUSD.volume_is_tradable(EURUSD.min_volume_cents)


def test_a_broken_lot_size_fails_loudly() -> None:
    quebrado = SymbolInfo(
        symbol_id=9,
        name="RUIM",
        digits=5,
        lot_size_cents=0,
        min_volume_cents=0,
        max_volume_cents=0,
        step_volume_cents=0,
    )
    with pytest.raises(ValueError):
        quebrado.lots_to_cents(1.0)


def test_envelope_carries_type_and_correlation_id() -> None:
    mensagem = envelope(PayloadType.NEW_ORDER_REQ, {"volume": 1}, msg_id="abc")
    assert mensagem == {
        "clientMsgId": "abc",
        "payloadType": 2106,
        "payload": {"volume": 1},
    }


def test_payload_types_match_the_official_enum() -> None:
    """Valores conferidos no `.proto` publicado pela Spotware."""
    assert PayloadType.APPLICATION_AUTH_REQ == 2100
    assert PayloadType.ACCOUNT_AUTH_REQ == 2102
    assert PayloadType.NEW_ORDER_REQ == 2106
    assert PayloadType.AMEND_POSITION_SLTP_REQ == 2110
    assert PayloadType.SYMBOLS_LIST_REQ == 2114
    assert PayloadType.TRADER_REQ == 2121
    assert PayloadType.RECONCILE_REQ == 2124


def test_symbol_parsing_survives_a_missing_field() -> None:
    """Um instrumento incompleto no meio de centenas nao pode derrubar a
    carga inteira do catalogo."""
    info = parse_symbol({"symbolId": 7, "symbolName": "xauusd"})
    assert info.symbol_id == 7
    assert info.name == "XAUUSD"
    assert info.lot_size_cents == 0


def test_error_messages_are_recognized_and_readable() -> None:
    erro = {"payloadType": 2142, "payload": {"errorCode": "NOT_ENOUGH_MONEY", "description": "sem margem"}}
    assert is_error(erro)
    assert "NOT_ENOUGH_MONEY" in error_text(erro)
    assert "sem margem" in error_text(erro)
