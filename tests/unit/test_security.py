import time

import pytest
from jose import JWTError

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_is_not_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"


def test_verify_password_roundtrip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_access_token_roundtrip() -> None:
    settings = get_settings()
    token = create_access_token(subject="alice", settings=settings)
    payload = decode_access_token(token, settings)
    assert payload["sub"] == "alice"


def test_access_token_rejects_tampered_signature() -> None:
    settings = get_settings()
    token = create_access_token(subject="alice", settings=settings)
    # Adultera o primeiro caractere (inicio do header em base64url), nunca
    # o ultimo: os ultimos 1-2 caracteres de um segmento base64 podem cair
    # em bits de padding "nao significativos", o que tornaria a adulteracao
    # flaky (o payload decodificado ficaria identico por coincidencia).
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    with pytest.raises(JWTError):
        decode_access_token(tampered, settings)


def test_access_token_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    settings_copy = settings.model_copy(update={"auth_access_token_expire_minutes": 0})
    token = create_access_token(subject="alice", settings=settings_copy)
    time.sleep(1)
    with pytest.raises(JWTError):
        decode_access_token(token, settings_copy)
