"""Diagnostico da ponte: cada passo isola UMA hipotese.

O valor deste modulo nao esta em detectar falha — qualquer `try/except`
detecta. Esta em nao misturar causas: "container parado" e "pacote ausente
do lado do Wine" produzem o mesmo sintoma e exigem correcoes opostas.

Por isso os testes verificam onde o diagnostico PARA, e nao so que falhou.
"""

from __future__ import annotations

import socket

import pytest

from app.mt5 import bridge_check
from app.mt5.bridge import BridgeError
from app.mt5.bridge_check import Step, check_bridge


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """Nenhum teste aqui pode tocar a rede de verdade."""

    def proibido(*args, **kwargs):  # pragma: no cover - so dispara em regressao
        raise AssertionError("teste tentou abrir socket real")

    monkeypatch.setattr(socket, "create_connection", proibido)
    monkeypatch.setattr(socket, "gethostbyname", proibido)


def _passo_ok(nome: str) -> Step:
    return Step(nome, True, "ok")


def test_it_stops_at_the_name_and_does_not_test_the_port(monkeypatch) -> None:
    """Se o nome nao resolve, o resultado do teste de porta seria ruido — e
    ruido em diagnostico faz procurar no lugar errado."""
    monkeypatch.setattr(bridge_check, "_check_library", lambda: _passo_ok("lib"))
    monkeypatch.setattr(
        bridge_check, "_check_dns", lambda host, port: Step("Nome resolve", False, "nao")
    )

    def nunca(*args, **kwargs):  # pragma: no cover
        raise AssertionError("nao deveria testar a porta")

    monkeypatch.setattr(bridge_check, "_check_tcp", nunca)

    relatorio = check_bridge("mt5", 18812)

    assert relatorio.ok is False
    assert relatorio.first_failure.name == "Nome resolve"
    assert len(relatorio.steps) == 2


def test_a_missing_library_stops_before_the_network(monkeypatch) -> None:
    """`rpyc` ausente e problema da imagem do painel, nao do MetaTrader."""
    monkeypatch.setattr(
        bridge_check,
        "_check_library",
        lambda: Step("Biblioteca rpyc", False, "nao instalada"),
    )

    relatorio = check_bridge("mt5", 18812)

    assert [passo.name for passo in relatorio.steps] == ["Biblioteca rpyc"]


def test_the_port_error_carries_the_reason(monkeypatch) -> None:
    """"Recusou" cobre causas opostas; o errno e o que separa."""

    def falha(endereco, timeout=None):
        raise OSError(111, "Connection refused")

    monkeypatch.setattr(socket, "create_connection", falha)

    passo = bridge_check._check_tcp("mt5", 18812, timeout=1.0)

    assert passo.ok is False
    assert "Connection refused" in passo.detail


def test_a_live_port_with_a_broken_bridge_is_a_different_failure(monkeypatch) -> None:
    """Porta aberta com RPyC quebrado nao e container parado, e a correcao
    tambem nao e a mesma."""
    monkeypatch.setattr(bridge_check, "_check_library", lambda: _passo_ok("lib"))
    monkeypatch.setattr(bridge_check, "_check_dns", lambda host, port: _passo_ok("dns"))
    monkeypatch.setattr(
        bridge_check, "_check_tcp", lambda host, port, timeout: _passo_ok("tcp")
    )
    monkeypatch.setattr(
        bridge_check,
        "_check_rpyc",
        lambda host, port, timeout: (Step("Ponte RPyC", False, "sem modulo"), None),
    )

    relatorio = check_bridge("mt5", 18812)

    assert relatorio.first_failure.name == "Ponte RPyC"


def test_the_session_is_closed_even_when_the_terminal_fails(monkeypatch) -> None:
    """Sem isso, cada diagnostico deixa um socket aberto no container do MT5
    — e o sintoma aparece muito depois, longe da causa."""

    class _Sessao:
        module = object()

        def __init__(self) -> None:
            self.fechada = False

        def close(self) -> None:
            self.fechada = True

    sessao = _Sessao()
    monkeypatch.setattr(bridge_check, "_check_library", lambda: _passo_ok("lib"))
    monkeypatch.setattr(bridge_check, "_check_dns", lambda host, port: _passo_ok("dns"))
    monkeypatch.setattr(
        bridge_check, "_check_tcp", lambda host, port, timeout: _passo_ok("tcp")
    )
    monkeypatch.setattr(
        bridge_check,
        "_check_rpyc",
        lambda host, port, timeout: (_passo_ok("Ponte RPyC"), sessao),
    )
    monkeypatch.setattr(
        bridge_check,
        "_check_terminal",
        lambda modulo: Step("Terminal responde", False, "fechado"),
    )

    relatorio = check_bridge("mt5", 18812)

    assert sessao.fechada is True
    assert relatorio.ok is False


def test_all_six_green_means_ok(monkeypatch) -> None:
    for nome in ("_check_library",):
        monkeypatch.setattr(bridge_check, nome, lambda: _passo_ok("lib"))
    monkeypatch.setattr(bridge_check, "_check_dns", lambda host, port: _passo_ok("dns"))
    monkeypatch.setattr(
        bridge_check, "_check_tcp", lambda host, port, timeout: _passo_ok("tcp")
    )

    class _Sessao:
        module = object()

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        bridge_check,
        "_check_rpyc",
        lambda host, port, timeout: (_passo_ok("rpyc"), _Sessao()),
    )
    monkeypatch.setattr(
        bridge_check, "_check_terminal", lambda modulo: _passo_ok("terminal")
    )

    assert check_bridge("mt5", 18812).ok is True


def test_a_bridge_error_becomes_the_step_message(monkeypatch) -> None:
    """A frase acionavel de `connect_bridge` nao pode ser trocada por um
    generico ao atravessar o diagnostico."""

    def falha(host, port, timeout=None):
        raise BridgeError("container do MetaTrader parado")

    monkeypatch.setattr("app.mt5.bridge.connect_bridge", falha)

    passo, sessao = bridge_check._check_rpyc("mt5", 18812, timeout=1.0)

    assert sessao is None
    assert "container do MetaTrader parado" in passo.detail


# --- descoberta de candidatos ----------------------------------------------


def test_a_failed_name_offers_reachable_candidates(monkeypatch) -> None:
    """"Nao resolve" nao diz QUAL nome usar — e quem olha o painel nao tem
    `docker ps` a mao."""

    def sem_dns(nome):
        raise OSError("nao resolve")

    def conecta(endereco, timeout=None):
        if endereco[0] == "mt5":
            return _Fechavel()
        raise OSError("recusado")

    monkeypatch.setattr(socket, "gethostbyname", sem_dns)
    monkeypatch.setattr(socket, "create_connection", conecta)

    passo = bridge_check._check_dns("mt5-wine", 18812)

    assert passo.ok is False
    assert "mt5" in passo.detail
    assert "Host da ponte" in passo.detail


def test_when_nothing_answers_it_says_so(monkeypatch) -> None:
    """Sugestao vazia apresentada como lista seria pior que nenhuma."""

    def sem_dns(nome):
        raise OSError("nao resolve")

    def recusa(endereco, timeout=None):
        raise OSError("recusado")

    monkeypatch.setattr(socket, "gethostbyname", sem_dns)
    monkeypatch.setattr(socket, "create_connection", recusa)

    passo = bridge_check._check_dns("mt5-wine", 18812)

    assert "Nenhum nome conhecido" in passo.detail


def test_the_probe_skips_the_host_that_already_failed(monkeypatch) -> None:
    """Sugerir de volta o nome que acabou de falhar seria ruido."""
    tentados: list[str] = []

    def registra(endereco, timeout=None):
        tentados.append(endereco[0])
        raise OSError("recusado")

    monkeypatch.setattr(socket, "create_connection", registra)

    bridge_check.suggest_hosts(18812, skip="mt5-wine")

    assert "mt5-wine" not in tentados


class _Fechavel:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None
