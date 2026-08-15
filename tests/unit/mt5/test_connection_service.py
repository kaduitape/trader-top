"""MT5ConnectionService: falar com o terminal, e so isso.

O que estes testes protegem, em ordem de importancia:

1. `shutdown()` SEMPRE roda — inclusive quando o teste falha no meio. Sem
   isso o terminal fica preso e o proximo teste falha por um motivo que nao
   tem nada a ver com a credencial.
2. Inicializar e autenticar sao passos distintos, com mensagens distintas:
   terminal fechado e senha errada nao podem produzir o mesmo texto.
3. Autenticar sem conta respondendo NAO e sucesso.
"""

from __future__ import annotations

import pytest

from app.mt5.connection_service import MT5ConnectionService, describe_error


class _Conta:
    def __init__(self, **campos) -> None:
        padrao = {
            "login": 12345678, "name": "Fulano", "server": "Broker-MT5-Demo",
            "company": "Broker Ltd", "currency": "USD", "balance": 10_000.0,
            "equity": 10_120.5, "margin": 500.0, "margin_free": 9_500.0,
            "leverage": 500, "trade_mode": 0,
        }
        padrao.update(campos)
        for chave, valor in padrao.items():
            setattr(self, chave, valor)


class _Terminal:
    """Terminal MT5 falso, com registro do que foi chamado."""

    __version__ = "5.0.45"

    def __init__(self, *, initialize=True, login=True, account=None, error=(-6, "auth")) -> None:
        self._initialize = initialize
        self._login = login
        self._account = account if account is not None else _Conta()
        self._error = error
        self.chamadas: list[str] = []

    def initialize(self, **kwargs):
        self.chamadas.append("initialize")
        return self._initialize

    def login(self, login, **kwargs):
        self.chamadas.append("login")
        return self._login

    def account_info(self):
        self.chamadas.append("account_info")
        return self._account

    def shutdown(self):
        self.chamadas.append("shutdown")

    def last_error(self):
        return self._error


def _servico(terminal) -> MT5ConnectionService:
    return MT5ConnectionService(client=terminal)


# --- mensagens de erro -----------------------------------------------------


def test_the_error_says_what_to_do_not_just_a_code() -> None:
    mensagem = describe_error(-6, "Authorization failed")

    assert "login, senha e servidor" in mensagem
    assert "-6" in mensagem


def test_a_missing_terminal_is_not_confused_with_bad_credentials() -> None:
    terminal = describe_error(-4, None)
    credencial = describe_error(-6, None)

    assert "Terminal" in terminal
    assert terminal != credencial


def test_an_unknown_code_still_produces_something_useful() -> None:
    assert describe_error(99999, "algo estranho") != ""


def test_no_information_at_all_does_not_produce_an_empty_message() -> None:
    assert describe_error(None, None).strip() != ""


# --- ciclo de vida ---------------------------------------------------------


def test_a_successful_test_returns_the_account(db_session) -> None:
    del db_session
    terminal = _Terminal()

    resultado = _servico(terminal).test_connection(
        login=12345678, password="x", server="Broker-MT5-Demo"
    )

    assert resultado.success is True
    assert resultado.account is not None
    assert resultado.account.login == 12345678
    assert resultado.account.currency == "USD"
    assert resultado.account.account_type == "DEMO"


def test_shutdown_runs_even_when_login_fails() -> None:
    """O `finally` que evita o terminal preso."""
    terminal = _Terminal(login=False)

    _servico(terminal).test_connection(login=1, password="x", server="S")

    assert "shutdown" in terminal.chamadas


def test_shutdown_runs_even_when_initialize_fails() -> None:
    terminal = _Terminal(initialize=False)

    _servico(terminal).test_connection(login=1, password="x", server="S")

    assert "shutdown" in terminal.chamadas


def test_a_failed_initialize_never_tries_to_login() -> None:
    """Autenticar num terminal que nao subiu produziria um erro que descreve
    a causa errada."""
    terminal = _Terminal(initialize=False)

    _servico(terminal).test_connection(login=1, password="x", server="S")

    assert "login" not in terminal.chamadas


def test_authenticating_without_an_account_is_not_success(db_session) -> None:
    """Sessao invalida: o painel nao pode dizer "conectado"."""
    del db_session
    terminal = _Terminal(account=None)
    terminal._account = None

    resultado = _servico(terminal).test_connection(login=1, password="x", server="S")

    assert resultado.success is False
    assert "sessao invalida" in resultado.message.lower()


def test_a_missing_library_is_reported_clearly() -> None:
    servico = MT5ConnectionService(client=None)

    resultado = servico.test_connection(login=1, password="x", server="S")

    assert resultado.success is False
    assert "Windows" in resultado.message


def test_a_terminal_path_that_does_not_exist_fails_before_anything(tmp_path) -> None:
    terminal = _Terminal()
    caminho = str(tmp_path / "nao-existe" / "terminal64.exe")

    resultado = _servico(terminal).test_connection(
        login=1, password="x", server="S", terminal_path=caminho
    )

    assert resultado.success is False
    assert "nao encontrado" in resultado.message
    assert terminal.chamadas == []


def test_a_real_account_is_identified(db_session) -> None:
    del db_session
    terminal = _Terminal(account=_Conta(trade_mode=1))

    resultado = _servico(terminal).test_connection(login=1, password="x", server="S")

    assert resultado.account.account_type == "REAL"


# --- diagnostico -----------------------------------------------------------


def test_the_diagnostics_never_include_a_password_field() -> None:
    import dataclasses

    from app.mt5.connection_service import Diagnostics

    campos = {campo.name for campo in dataclasses.fields(Diagnostics)}

    assert not any("password" in nome or "senha" in nome for nome in campos)


def test_the_diagnostics_report_the_library(db_session) -> None:
    del db_session
    diagnostico = _servico(_Terminal()).diagnose(
        login=1, server="S", terminal_path=None
    )

    assert diagnostico.library_installed is True
    assert diagnostico.library_version == "5.0.45"
    assert diagnostico.account_configured is True
    assert diagnostico.server_configured is True


def test_the_diagnostics_work_without_the_library() -> None:
    diagnostico = MT5ConnectionService(client=None).diagnose(
        login=None, server=None, terminal_path=None
    )

    assert diagnostico.library_installed is False
    assert diagnostico.account_configured is False


def test_the_diagnostics_do_not_leave_the_terminal_initialized() -> None:
    terminal = _Terminal()

    _servico(terminal).diagnose(login=1, server="S", terminal_path=None)

    assert terminal.chamadas.count("shutdown") >= 1


def test_this_module_cannot_send_orders() -> None:
    """Garantia estrutural: o modulo nao IMPORTA o caminho de ordens.

    Verificado por AST, e nao por busca de texto: a docstring cita
    `app.mt5.orders` justamente para explicar que nao o usa, e um teste de
    texto cru reprovaria a explicacao junto com o problema.
    """
    import ast
    from pathlib import Path

    arvore = ast.parse(Path("app/mt5/connection_service.py").read_text(encoding="utf-8"))

    importados: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(alias.name for alias in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module)

    assert not any(nome.startswith("app.mt5.orders") for nome in importados)
    assert not any(
        isinstance(no, ast.Attribute) and no.attr == "order_send"
        for no in ast.walk(arvore)
    )


@pytest.mark.parametrize("codigo", [-4, -6, -8, -10])
def test_the_documented_codes_all_have_a_hint(codigo: int) -> None:
    assert describe_error(codigo, None) != f"(codigo {codigo})"
