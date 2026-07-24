"""Dependencias FastAPI de autenticacao/autorizacao.

`get_current_user` decodifica o JWT do header `Authorization: Bearer ...` e
carrega o usuario correspondente — usado pelas rotas de API (`/api/*`).
`require_role` compoe sobre ela para restringir rotas administrativas a
perfis especificos (ver docs/security.md secao 2 — apenas ADMIN podera,
em fases futuras, solicitar liberacao de modo real).

`get_current_user_for_web` (Fase 12) e a variante usada pelas paginas
HTML do dashboard: le o MESMO JWT (`create_access_token`/
`decode_access_token`, identico ao da API), mas de um cookie httpOnly em
vez do header `Authorization` — o navegador nao anexa esse header
sozinho em uma navegacao normal de pagina. Nunca autentica sem token
valido; a ausencia/invalidez leva a um redirecionamento para `/login`
(`RedirectToLogin`), nunca a uma resposta 401 crua (ruim para
navegacao HTML).
"""

from __future__ import annotations

import secrets as _secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.enums import UserRole
from app.core.security import decode_access_token, hash_password
from app.database.models.user import User
from app.database.repositories.user_repository import UserRepository
from app.database.session import get_db

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Nao foi possivel validar as credenciais.",
    headers={"WWW-Authenticate": "Bearer"},
)

SESSION_COOKIE_NAME = "access_token"


class RedirectToLogin(Exception):
    """Levantada por `get_current_user_for_web` quando o cookie de sessao
    esta ausente/invalido/expirado — traduzida por um exception handler
    (`app/api/app.py`) num redirecionamento para `/login`, nunca um 401
    cru numa pagina HTML."""


def get_current_user(
    token: str | None = Depends(_oauth2_scheme),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> User:
    if token is None:
        raise _CREDENTIALS_ERROR
    try:
        payload = decode_access_token(token, settings)
    except JWTError as exc:
        raise _CREDENTIALS_ERROR from exc

    username = payload.get("sub")
    if not username:
        raise _CREDENTIALS_ERROR

    user = UserRepository(db).get_by_username(username)
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR
    return user


def _get_or_create_dev_user(db: Session) -> User:
    """Usuario sintetico usado apenas quando `dashboard_auth_disabled=True`
    (bypass de desenvolvimento). Sempre ADMIN, senha aleatoria/nunca usada
    (o login por formulario continua exigindo credenciais reais; isso so
    afeta o acesso direto as paginas do dashboard)."""
    repo = UserRepository(db)
    user = repo.get_by_username("dev")
    if user is not None:
        return user

    role = repo.get_or_create_role("ADMIN")
    user = repo.create_user(
        username="dev",
        email="dev@localhost",
        password_hash=hash_password(_secrets.token_urlsafe(32)),
        roles=[role],
    )
    db.commit()
    return user


def get_current_user_for_web(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> User:
    if settings.dashboard_auth_disabled:
        return _get_or_create_dev_user(db)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        raise RedirectToLogin()
    try:
        payload = decode_access_token(token, settings)
    except JWTError as exc:
        raise RedirectToLogin() from exc

    username = payload.get("sub")
    if not username:
        raise RedirectToLogin()

    user = UserRepository(db).get_by_username(username)
    if user is None or not user.is_active:
        raise RedirectToLogin()
    return user


def require_role(*allowed_roles: UserRole) -> Callable[[User], User]:
    def _dependency(user: User = Depends(get_current_user)) -> User:
        user_role_names = {role.name for role in user.roles}
        if not user_role_names.intersection({r.value for r in allowed_roles}):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuario sem permissao para esta acao.",
            )
        return user

    return _dependency
