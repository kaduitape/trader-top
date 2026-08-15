"""Credenciais MT5: cifradas no banco, nunca em claro em lugar nenhum.

Guardar senha de corretora no banco amplia a superficie de exposicao — quem
lê o banco (backup, replica, dump de suporte) passa a ter o material. A
criptografia existe para que ler o banco nao baste.

Estes testes cobrem os criterios de aceite que sao verificaveis em codigo:
senha protegida, edicao sem apagar a senha, e senha ausente de qualquer
saida.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.database.models.mt5_credential import Mt5Credential
from app.database.repositories.mt5_credential_repository import Mt5CredentialRepository

SENHA = "S3nh4-do-MT5!"


@pytest.fixture(autouse=True)
def _limpa(db_session):
    def apagar() -> None:
        db_session.execute(delete(Mt5Credential))
        db_session.commit()

    apagar()
    yield
    apagar()


@pytest.fixture
def repo(db_session) -> Mt5CredentialRepository:
    return Mt5CredentialRepository(db_session)


# --- criptografia ----------------------------------------------------------


def test_the_ciphertext_does_not_contain_the_password() -> None:
    cifrado = encrypt_secret(SENHA)

    assert SENHA not in cifrado
    assert decrypt_secret(cifrado) == SENHA


def test_two_encryptions_of_the_same_password_differ() -> None:
    """Fernet usa IV aleatorio. Cifrado deterministico deixaria comparar
    campos para descobrir contas com a mesma senha."""
    assert encrypt_secret(SENHA) != encrypt_secret(SENHA)


def test_the_mask_never_carries_the_value() -> None:
    mascara = mask_secret(encrypt_secret(SENHA))

    assert mascara == "•" * 12
    assert mask_secret(None) is None


# --- armazenamento ---------------------------------------------------------


def test_the_password_is_stored_encrypted(repo, db_session) -> None:
    repo.save(login=12345678, server="Broker-MT5-Live", account_type="DEMO", password=SENHA)
    db_session.commit()

    registro = repo.get_active()
    assert registro.password_encrypted != SENHA
    assert SENHA not in registro.password_encrypted
    assert repo.reveal_password(registro) == SENHA


def test_the_first_save_requires_a_password(repo) -> None:
    with pytest.raises(ValueError):
        repo.save(login=1, server="X", account_type="DEMO", password=None)


def test_editing_without_a_password_keeps_the_existing_one(repo, db_session) -> None:
    """O criterio de aceite mais facil de errar: mudar o servidor nao pode
    exigir redigitar a senha."""
    repo.save(login=12345678, server="Antigo", account_type="DEMO", password=SENHA)
    db_session.commit()

    repo.save(login=12345678, server="Novo-Server", account_type="REAL", password=None)
    db_session.commit()

    registro = repo.get_active()
    assert registro.server == "Novo-Server"
    assert registro.account_type == "REAL"
    assert repo.reveal_password(registro) == SENHA


def test_changing_the_password_invalidates_the_previous_result(repo, db_session) -> None:
    """Um "sucesso" de ontem nao diz nada sobre a credencial de agora."""
    repo.save(login=1, server="S", account_type="DEMO", password=SENHA)
    db_session.commit()
    repo.record_test(repo.get_active(), success=True)
    db_session.commit()

    repo.save(login=1, server="S", account_type="DEMO", password="outra-senha")
    db_session.commit()

    assert repo.get_active().last_test_status is None


def test_an_unknown_account_type_falls_back_to_demo(repo, db_session) -> None:
    """Valor invalido nunca pode virar REAL por acidente."""
    repo.save(login=1, server="S", account_type="QUALQUER", password=SENHA)
    db_session.commit()

    assert repo.get_active().account_type == "DEMO"


def test_the_repr_does_not_leak_the_secret(repo, db_session) -> None:
    """`repr` acaba em log com facilidade."""
    repo.save(login=12345678, server="S", account_type="DEMO", password=SENHA)
    db_session.commit()

    texto = repr(repo.get_active())
    assert SENHA not in texto
    assert "password" not in texto.lower()


# --- resultado do teste ----------------------------------------------------


def test_a_failure_does_not_move_the_last_success(repo, db_session) -> None:
    """"Testei agora e falhou" e "funcionou tres dias atras" sao fatos
    diferentes — junta-los faria a tela dizer "conectado" sobre uma
    configuracao quebrada."""
    repo.save(login=1, server="S", account_type="DEMO", password=SENHA)
    db_session.commit()

    sucesso_em = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    repo.record_test(repo.get_active(), success=True, now=sucesso_em)
    db_session.commit()

    falha_em = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    repo.record_test(repo.get_active(), success=False, error="Login invalido", now=falha_em)
    db_session.commit()

    registro = repo.get_active()
    assert registro.last_test_status == "failure"
    assert registro.last_error == "Login invalido"
    assert registro.last_success_at == sucesso_em.replace(tzinfo=None)
    assert registro.last_test_at == falha_em.replace(tzinfo=None)


def test_a_success_clears_the_previous_error(repo, db_session) -> None:
    repo.save(login=1, server="S", account_type="DEMO", password=SENHA)
    db_session.commit()
    repo.record_test(repo.get_active(), success=False, error="Senha incorreta")
    db_session.commit()

    repo.record_test(repo.get_active(), success=True)
    db_session.commit()

    assert repo.get_active().last_error is None
