"""Selecao de corretora por configuracao.

O comportamento que mais importa aqui e negativo: com credencial faltando, o
sistema NAO pode cair de volta no MT5 em silencio. "Achei que estava operando
na cTrader" e um jeito ruim de descobrir onde o dinheiro foi parar.
"""

from __future__ import annotations

import pytest

from app.broker.factory import (
    BROKER_CTRADER,
    BROKER_MT5,
    build_ctrader_broker,
    resolve_broker_name,
)
from app.broker.port import BrokerError
from app.core.config import Settings


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"app_secret_key": "chave-de-teste-1234"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_mt5_is_the_default() -> None:
    assert resolve_broker_name(settings()) == BROKER_MT5


def test_ctrader_can_be_selected() -> None:
    assert resolve_broker_name(settings(broker="ctrader")) == BROKER_CTRADER


def test_selection_is_case_insensitive() -> None:
    assert resolve_broker_name(settings(broker="CTrader")) == BROKER_CTRADER


def test_an_unknown_broker_fails_loudly() -> None:
    with pytest.raises(BrokerError, match="nao e suportado"):
        resolve_broker_name(settings(broker="binance"))


def test_missing_credentials_name_every_variable_that_is_missing() -> None:
    with pytest.raises(BrokerError) as exc:
        build_ctrader_broker(settings(broker="ctrader"))

    mensagem = str(exc.value)
    for variavel in (
        "CTRADER_CLIENT_ID",
        "CTRADER_CLIENT_SECRET",
        "CTRADER_ACCESS_TOKEN",
        "CTRADER_ACCOUNT_ID",
    ):
        assert variavel in mensagem


def test_a_partial_configuration_still_refuses() -> None:
    """Meia credencial nao e melhor que nenhuma — nao pode virar tentativa
    de conexao que falha depois, no meio de um ciclo."""
    with pytest.raises(BrokerError, match="CTRADER_ACCESS_TOKEN"):
        build_ctrader_broker(
            settings(
                broker="ctrader",
                ctrader_client_id="id",
                ctrader_client_secret="secret",
                ctrader_account_id=123,
            )
        )
