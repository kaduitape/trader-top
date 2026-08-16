"""Ponte para o MetaTrader sob Wine.

O projeto inteiro assumia que o terminal so era alcancavel de uma maquina
Windows. Com o mt5-wine em Docker isso deixa de valer, e a ponte e o que
transforma essa premissa em configuracao.

O que estes testes protegem: que o modulo remoto entre no lugar do local
SEM o resto do sistema perceber, que a conexao seja fechada (senao cada
teste acumula um socket no container do MetaTrader) e que a falha mais
comum — container parado — vire uma frase acionavel.
"""

from __future__ import annotations

import pytest

from app.mt5.bridge import (
    DEFAULT_BRIDGE_PORT,
    BridgeError,
    BridgeSession,
    connect_bridge,
    describe_target,
)
from app.mt5.connection_service import MT5ConnectionService


class _Conexao:
    """Conexao RPyC falsa."""

    def __init__(self, *, modulo=object()) -> None:
        self._config: dict = {}
        self.fechada = False
        self.modules = type("Mods", (), {"MetaTrader5": modulo})()

    def close(self) -> None:
        self.fechada = True


def _instalar_connect(monkeypatch, resultado) -> list[tuple]:
    """Substitui `rpyc.utils.classic.connect` e registra as chamadas."""
    chamadas: list[tuple] = []

    def falso(host, port, **kwargs):
        chamadas.append((host, port))
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    import rpyc.utils.classic as classic

    monkeypatch.setattr(classic, "connect", falso)
    return chamadas


# --- conexao ---------------------------------------------------------------


def test_it_returns_the_remote_module(monkeypatch) -> None:
    modulo = object()
    _instalar_connect(monkeypatch, _Conexao(modulo=modulo))

    sessao = connect_bridge("mt5", 18812)

    assert sessao.module is modulo


def test_the_default_port_matches_the_common_image() -> None:
    assert DEFAULT_BRIDGE_PORT == 18812


def test_a_refused_connection_says_nothing_is_listening(monkeypatch) -> None:
    """Recusa ATIVA nao e container parado — e o oposto: ele respondeu.
    Tratar os dois com a mesma frase manda procurar no lugar errado, e foi
    o que aconteceu em producao."""
    _instalar_connect(monkeypatch, ConnectionRefusedError("recusado"))

    with pytest.raises(BridgeError) as exc:
        connect_bridge("mt5", 18812)

    mensagem = str(exc.value)
    assert "18812" in mensagem
    assert "nada escuta" in mensagem
    assert "docker logs" in mensagem


def test_an_unreachable_host_is_not_described_as_idle(monkeypatch) -> None:
    """Timeout/host inacessivel continua sendo "container parado"."""
    _instalar_connect(monkeypatch, TimeoutError("sem resposta"))

    with pytest.raises(BridgeError) as exc:
        connect_bridge("mt5", 18812)

    assert "nada escuta" not in str(exc.value)


def test_a_missing_module_on_the_other_side_is_distinguished(monkeypatch) -> None:
    """Ponte de pe com Python sem o pacote e um problema DIFERENTE de
    container parado, e a correcao tambem e."""

    class _SemModulo(_Conexao):
        def __init__(self) -> None:
            super().__init__()

            class _Mods:
                def __getattr__(self, nome):
                    raise AttributeError(nome)

            self.modules = _Mods()

    conexao = _SemModulo()
    _instalar_connect(monkeypatch, conexao)

    with pytest.raises(BridgeError) as exc:
        connect_bridge("mt5", 18812)

    assert "MetaTrader5" in str(exc.value)
    assert conexao.fechada is True, "conexao inutil precisa ser fechada"


def test_the_timeout_is_applied_to_the_connection(monkeypatch) -> None:
    conexao = _Conexao()
    _instalar_connect(monkeypatch, conexao)

    connect_bridge("mt5", 18812, timeout=42.0)

    assert conexao._config["sync_request_timeout"] == 42.0


def test_closing_the_session_closes_the_connection() -> None:
    conexao = _Conexao()

    BridgeSession(module=object(), _connection=conexao).close()

    assert conexao.fechada is True


def test_closing_twice_does_not_explode() -> None:
    class _Ruim:
        def close(self):
            raise RuntimeError("ja fechada")

    BridgeSession(module=object(), _connection=_Ruim()).close()


def test_the_target_description_is_readable() -> None:
    assert describe_target("mt5", 18812) == "mt5:18812"
    assert "local" in describe_target(None, 18812)


# --- integracao com o servico ---------------------------------------------


class _TerminalRemoto:
    __version__ = "5.0.45"

    def __init__(self) -> None:
        self.chamadas: list[str] = []

    def initialize(self, **kwargs):
        self.chamadas.append("initialize")
        return True

    def login(self, login, **kwargs):
        self.chamadas.append("login")
        return True

    def account_info(self):
        self.chamadas.append("account_info")
        return type(
            "Conta", (), {
                "login": 999, "name": "Wine", "server": "Broker-Demo",
                "company": "Broker", "currency": "USD", "balance": 100.0,
                "equity": 100.0, "margin": 0.0, "margin_free": 100.0,
                "leverage": 100, "trade_mode": 0,
            },
        )()

    def shutdown(self):
        self.chamadas.append("shutdown")

    def last_error(self):
        return (0, "ok")


def test_the_service_uses_the_bridge_when_configured(monkeypatch) -> None:
    """O ponto do desenho: nada no servico muda por estar do outro lado."""
    remoto = _TerminalRemoto()
    conexao = _Conexao(modulo=remoto)
    _instalar_connect(monkeypatch, conexao)

    servico = MT5ConnectionService(bridge_host="mt5", bridge_port=18812)
    resultado = servico.test_connection(login=999, password="x", server="Broker-Demo")

    assert resultado.success is True
    assert resultado.account.login == 999
    assert "login" in remoto.chamadas


def test_the_bridge_is_closed_after_the_test(monkeypatch) -> None:
    """Sem isso, cada teste deixa um socket aberto no container do MT5."""
    conexao = _Conexao(modulo=_TerminalRemoto())
    _instalar_connect(monkeypatch, conexao)

    MT5ConnectionService(bridge_host="mt5").test_connection(
        login=1, password="x", server="S"
    )

    assert conexao.fechada is True


def test_a_bridge_failure_is_reported_instead_of_missing_library(monkeypatch) -> None:
    """"Biblioteca nao instalada" descreveria a causa errada quando o
    problema e o container do MetaTrader estar parado."""
    _instalar_connect(monkeypatch, ConnectionRefusedError("recusado"))

    resultado = MT5ConnectionService(bridge_host="mt5").test_connection(
        login=1, password="x", server="S"
    )

    assert resultado.success is False
    assert "nada escuta" in resultado.message
    assert "nao instalada" not in resultado.message


def test_without_a_bridge_the_behaviour_is_unchanged() -> None:
    """Quem roda no Windows nao pode ser afetado por isto."""
    servico = MT5ConnectionService(client=None, bridge_host=None)

    resultado = servico.test_connection(login=1, password="x", server="S")

    assert resultado.success is False
    assert "ponte" in resultado.message.lower() or "Windows" in resultado.message
