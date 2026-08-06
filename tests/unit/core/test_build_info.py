"""Versao do codigo em execucao.

Serve a um proposito so: tornar visivel que o conector Windows e o servidor
web podem estar em versoes diferentes. Quando estao, o sintoma e o mais
frustrante que existe — "consertei aquilo e continua igual" — e nada no
painel denunciava isso.
"""

from __future__ import annotations

from app.core.build_info import UNKNOWN, code_version, versions_match


def test_it_always_answers_something() -> None:
    """Sem git, sem pacote instalado, ainda assim tem que responder: o
    diagnostico nao pode depender de o ambiente estar completo."""
    assert code_version()


def test_the_answer_is_stable_within_the_process() -> None:
    assert code_version() == code_version()


def test_equal_versions_match() -> None:
    assert versions_match("abc1234", "abc1234") is True


def test_different_versions_do_not_match() -> None:
    assert versions_match("abc1234", "def5678") is False


def test_an_unknown_version_never_matches() -> None:
    """Alertar sem certeza custa menos que silenciar uma divergencia real:
    investigar um alerta falso e mais barato que procurar um bug ja
    corrigido."""
    assert versions_match(UNKNOWN, UNKNOWN) is False
    assert versions_match("abc1234", UNKNOWN) is False


def test_a_missing_version_never_matches() -> None:
    assert versions_match(None, "abc1234") is False
    assert versions_match("abc1234", "") is False
