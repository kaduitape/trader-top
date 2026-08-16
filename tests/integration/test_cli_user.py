"""Comandos de usuario da CLI: a porta de entrada quando ninguem entra mais.

Estes comandos existem para um momento especifico — o operador trancado do
lado de fora do proprio painel. Por isso o que se testa aqui nao e so "muda
a senha": e que o comando saiba DIZER qual dos motivos de "nao consigo
entrar" e o verdadeiro, porque cada um tem correcao diferente.

A senha nunca e argumento de linha de comando: argumento aparece no
historico do shell e no `ps` de qualquer usuario da maquina. Um teste
abaixo trava isso.
"""

from __future__ import annotations

import argparse

import pytest

from app import cli
from app.core.security import hash_password, verify_password
from app.database.repositories.user_repository import UserRepository
from app.database.session import get_session_factory


@pytest.fixture(autouse=True)
def _limpa_usuarios(engine):
    """O banco em memoria e compartilhado pela suite inteira.

    Depende de `engine` porque e ele quem cria as tabelas; limpar antes E
    depois evita tanto herdar sujeira de outro modulo quanto deixar.
    """
    del engine
    from app.database.models.audit_log import AuditLog
    from app.database.models.user import Role, User

    def apagar() -> None:
        sessao = get_session_factory()()
        try:
            for usuario in sessao.query(User).all():
                usuario.roles.clear()
            sessao.flush()
            sessao.query(User).delete()
            sessao.query(Role).delete()
            # So as acoes destes comandos: o audit log e de todo mundo.
            sessao.query(AuditLog).filter(
                AuditLog.action.in_(("password_reset", "user_create"))
            ).delete(synchronize_session=False)
            sessao.commit()
        finally:
            sessao.close()

    apagar()
    yield
    apagar()


def _cria(username: str, *, ativo: bool = True, senha: str = "senha-antiga") -> int:
    sessao = get_session_factory()()
    try:
        usuario = UserRepository(sessao).create_user(
            username=username,
            email=f"{username}@exemplo.com",
            password_hash=hash_password(senha),
        )
        usuario.is_active = ativo
        sessao.commit()
        return usuario.id
    finally:
        sessao.close()


def _senha_atual(username: str) -> str:
    sessao = get_session_factory()()
    try:
        usuario = UserRepository(sessao).get_by_username(username)
        assert usuario is not None
        return usuario.password_hash
    finally:
        sessao.close()


def _responde(monkeypatch, *respostas: str) -> None:
    """Substitui o `getpass` — o terminal nao existe sob pytest."""
    fila = list(respostas)
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": fila.pop(0))


# --- redefinir senha -------------------------------------------------------


def test_the_password_actually_changes(monkeypatch) -> None:
    _cria("admin")
    anterior = _senha_atual("admin")
    _responde(monkeypatch, "senha-nova-123", "senha-nova-123")

    codigo = cli.cmd_user_reset_password(
        argparse.Namespace(username="admin", activate=False)
    )

    assert codigo == 0
    novo = _senha_atual("admin")
    assert novo != anterior
    assert verify_password("senha-nova-123", novo)


def test_it_is_stored_hashed(monkeypatch) -> None:
    """Senha em texto puro no banco transforma um vazamento de backup em
    invasao da conta da corretora."""
    _cria("admin")
    _responde(monkeypatch, "senha-nova-123", "senha-nova-123")

    cli.cmd_user_reset_password(argparse.Namespace(username="admin", activate=False))

    assert "senha-nova-123" not in _senha_atual("admin")


def test_mismatched_confirmation_changes_nothing(monkeypatch) -> None:
    """Errar a digitacao e trocar a senha por algo desconhecido seria o pior
    resultado possivel para quem ja esta trancado do lado de fora."""
    _cria("admin")
    anterior = _senha_atual("admin")
    _responde(monkeypatch, "senha-nova-123", "outra-coisa-456")

    codigo = cli.cmd_user_reset_password(
        argparse.Namespace(username="admin", activate=False)
    )

    assert codigo == 1
    assert _senha_atual("admin") == anterior


def test_a_short_password_is_refused(monkeypatch) -> None:
    _cria("admin")
    anterior = _senha_atual("admin")
    _responde(monkeypatch, "1234", "1234")

    assert (
        cli.cmd_user_reset_password(argparse.Namespace(username="admin", activate=False))
        == 1
    )
    assert _senha_atual("admin") == anterior


def test_an_unknown_user_points_at_the_listing(monkeypatch, capsys) -> None:
    """Nome errado e senha errada produzem o mesmo sintoma na tela de login;
    aqui eles precisam se separar."""
    codigo = cli.cmd_user_reset_password(
        argparse.Namespace(username="ninguem", activate=False)
    )

    assert codigo == 1
    assert "user list" in capsys.readouterr().err


def test_an_inactive_user_is_warned_about(monkeypatch, capsys) -> None:
    """Senha nova em conta inativa nao faz o login voltar — e sem o aviso o
    operador troca a senha de novo achando que errou a digitacao."""
    _cria("admin", ativo=False)
    _responde(monkeypatch, "senha-nova-123", "senha-nova-123")

    cli.cmd_user_reset_password(argparse.Namespace(username="admin", activate=False))

    assert "INATIVO" in capsys.readouterr().err


def test_activate_brings_the_account_back(monkeypatch) -> None:
    _cria("admin", ativo=False)
    _responde(monkeypatch, "senha-nova-123", "senha-nova-123")

    cli.cmd_user_reset_password(argparse.Namespace(username="admin", activate=True))

    sessao = get_session_factory()()
    try:
        assert UserRepository(sessao).get_by_username("admin").is_active is True
    finally:
        sessao.close()


def test_the_reset_is_audited_without_the_password(monkeypatch) -> None:
    from app.database.models.audit_log import AuditLog

    _cria("admin")
    _responde(monkeypatch, "senha-nova-123", "senha-nova-123")

    cli.cmd_user_reset_password(argparse.Namespace(username="admin", activate=False))

    sessao = get_session_factory()()
    try:
        registros = sessao.query(AuditLog).filter_by(action="password_reset").all()
        assert len(registros) == 1
        assert "senha-nova-123" not in (registros[0].detail or "")
    finally:
        sessao.close()


# --- criar -----------------------------------------------------------------


def test_creating_the_first_admin(monkeypatch) -> None:
    """Com a tabela vazia, redefinir senha nao resolve: nao ha o que
    redefinir."""
    _responde(monkeypatch, "senha-nova-123", "senha-nova-123")

    codigo = cli.cmd_user_create(
        argparse.Namespace(username="admin", email="a@b.com", admin=True)
    )

    assert codigo == 0
    sessao = get_session_factory()()
    try:
        usuario = UserRepository(sessao).get_by_username("admin")
        assert usuario is not None
        assert [papel.name for papel in usuario.roles] == ["ADMIN"]
        assert verify_password("senha-nova-123", usuario.password_hash)
    finally:
        sessao.close()


def test_creating_a_duplicate_points_at_the_reset(capsys) -> None:
    _cria("admin")

    codigo = cli.cmd_user_create(
        argparse.Namespace(username="admin", email="outro@b.com", admin=False)
    )

    assert codigo == 1
    assert "reset-password" in capsys.readouterr().err


def test_a_duplicate_email_is_refused() -> None:
    """A coluna e UNIQUE: sem esta checagem o erro sairia como stacktrace de
    integridade em vez de frase."""
    _cria("admin")

    codigo = cli.cmd_user_create(
        argparse.Namespace(username="outro", email="admin@exemplo.com", admin=False)
    )

    assert codigo == 1


# --- listar ----------------------------------------------------------------


def test_the_listing_shows_state_and_roles(capsys) -> None:
    _cria("ativo")
    _cria("parado", ativo=False)

    assert cli.cmd_user_list(argparse.Namespace()) == 0

    saida = capsys.readouterr().out
    assert "ativo" in saida
    assert "INATIVO" in saida


def test_an_empty_table_tells_you_to_create(capsys) -> None:
    assert cli.cmd_user_list(argparse.Namespace()) == 0
    assert "user create" in capsys.readouterr().out


def test_the_listing_never_prints_a_hash(capsys) -> None:
    """Hash na tela vira hash em print de suporte, e dai em ataque offline."""
    _cria("admin", senha="segredo")

    cli.cmd_user_list(argparse.Namespace())

    assert _senha_atual("admin") not in capsys.readouterr().out


# --- contrato da interface -------------------------------------------------


def test_no_command_accepts_a_password_argument() -> None:
    """Senha em argumento vaza pelo historico do shell e pelo `ps`."""
    parser = cli.build_parser()
    acoes = [
        acao
        for acao in parser._actions
        if getattr(acao, "dest", None) == "command"
    ]
    subparsers = acoes[0].choices["user"]._subparsers._group_actions[0].choices

    for nome, sub in subparsers.items():
        opcoes = {opcao for acao in sub._actions for opcao in acao.option_strings}
        assert "--password" not in opcoes, f"`user {nome}` aceita senha por argumento"
