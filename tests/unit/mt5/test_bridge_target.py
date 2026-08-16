"""Precedencia entre o que esta salvo no painel e o que esta no ambiente.

O bug que isto impede e o pior tipo de bug de configuracao: a tela mostra um
valor, o sistema usa outro, e nenhum log explica a diferenca. Quem salvou
`mt5` no painel e continuou vendo erro apontando para o host antigo do
`.env` nao tem como descobrir sozinho.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.mt5.bridge import DEFAULT_BRIDGE_PORT, resolve_target


@dataclass
class _Credencial:
    bridge_host: str | None = None
    bridge_port: int | None = None


@dataclass
class _Settings:
    mt5_bridge_host: str | None = None
    mt5_bridge_port: int = DEFAULT_BRIDGE_PORT


def test_the_panel_wins_over_the_environment() -> None:
    host, porta = resolve_target(
        _Credencial(bridge_host="mt5", bridge_port=19000),
        _Settings(mt5_bridge_host="antigo", mt5_bridge_port=18812),
    )

    assert (host, porta) == ("mt5", 19000)


def test_the_environment_still_works_when_the_panel_is_empty() -> None:
    """Instalacao que ja configurou por `.env` nao pode parar de funcionar."""
    host, porta = resolve_target(
        _Credencial(), _Settings(mt5_bridge_host="mt5-wine", mt5_bridge_port=18812)
    )

    assert (host, porta) == ("mt5-wine", 18812)


def test_no_credential_at_all_falls_back_to_the_environment() -> None:
    host, _ = resolve_target(None, _Settings(mt5_bridge_host="mt5"))

    assert host == "mt5"


def test_nothing_configured_means_no_bridge() -> None:
    """Sem ponte o sistema volta ao caminho antigo (pacote local, Windows) —
    e nao para com erro."""
    host, porta = resolve_target(None, _Settings())

    assert host is None
    assert porta == DEFAULT_BRIDGE_PORT


def test_blank_is_treated_as_absent() -> None:
    """Campo de formulario devolve string vazia, nao `None`."""
    host, _ = resolve_target(_Credencial(bridge_host="   "), _Settings())

    assert host is None


def test_a_host_without_a_port_uses_the_default() -> None:
    _, porta = resolve_target(_Credencial(bridge_host="mt5"), _Settings())

    assert porta == DEFAULT_BRIDGE_PORT
