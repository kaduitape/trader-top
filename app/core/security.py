"""Primitivas de seguranca: hash de senha e tokens JWT.

Nenhuma senha e armazenada ou comparada em texto puro. Nenhum segredo e
logado (ver `app/core/logging.py` para o mascaramento defensivo adicional).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import Settings

# bcrypt trunca o segredo em 72 bytes; senhas maiores sao rejeitadas
# explicitamente em vez de silenciosamente truncadas, para nao dar falsa
# sensacao de que o restante da senha importa.
_MAX_PASSWORD_BYTES = 72


def hash_password(plain_password: str) -> str:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {_MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, password_hash: str) -> bool:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(encoded, password_hash.encode("ascii"))


def create_access_token(
    *,
    subject: str,
    settings: Settings,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.auth_access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": expires_at,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.app_secret_key, algorithm=settings.auth_jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Decodifica e valida um token JWT.

    Levanta `jose.JWTError` (ou subclasse) se o token for invalido ou tiver
    expirado — o chamador (dependencia de autenticacao do FastAPI) e
    responsavel por traduzir isso em uma resposta HTTP 401.
    """
    try:
        return jwt.decode(token, settings.app_secret_key, algorithms=[settings.auth_jwt_algorithm])
    except JWTError:
        raise
