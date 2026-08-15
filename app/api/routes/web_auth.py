"""Login/logout do dashboard HTML (Fase 12) — sessao via cookie httpOnly,
nao via header `Authorization` (o navegador nao o anexa sozinho numa
navegacao normal de pagina). O mesmo JWT/segredo da API (`/api/auth/
login`) e reusado — nao existe um segundo mecanismo de autenticacao
paralelo, apenas um transporte diferente (cookie em vez de header)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies.auth import SESSION_COOKIE_NAME
from app.api.templates_engine import templates
from app.core.config import Settings, get_settings
from app.core.google_oauth import (
    GoogleOAuthError,
    build_authorization_url,
    exchange_code_for_identity,
)
from app.core.security import create_access_token, verify_password
from app.database.repositories.audit_log_repository import AuditLogRepository
from app.database.repositories.user_repository import UserRepository
from app.database.session import get_db

router = APIRouter(tags=["web-auth"])

GOOGLE_STATE_COOKIE = "google_oauth_state"
GOOGLE_NONCE_COOKIE = "google_oauth_nonce"


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, settings: Settings = Depends(get_settings)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None, "google_oauth_enabled": settings.google_oauth_enabled},
    )


@router.post("/login", response_model=None)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    user = UserRepository(db).get_by_username(username)

    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        AuditLogRepository(db).record(
            action="login", entity="user", detail=f"username={username}", result="FAILURE"
        )
        db.commit()
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Usuario ou senha invalidos.",
                "google_oauth_enabled": settings.google_oauth_enabled,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = create_access_token(
        subject=user.username,
        settings=settings,
        extra_claims={"roles": [role.name for role in user.roles]},
    )
    AuditLogRepository(db).record(user_id=user.id, action="login", entity="user", result="SUCCESS")
    db.commit()

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=settings.auth_access_token_expire_minutes * 60,
    )
    return response


@router.get("/auth/google")
def google_login(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    if not settings.google_oauth_enabled:
        return RedirectResponse(url="/login?google=not-configured", status_code=303)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    response = RedirectResponse(
        url=build_authorization_url(settings=settings, state=state, nonce=nonce), status_code=302
    )
    secure_cookie = bool(
        settings.google_oauth_redirect_uri
        and settings.google_oauth_redirect_uri.startswith("https://")
    )
    for name, value in ((GOOGLE_STATE_COOKIE, state), (GOOGLE_NONCE_COOKIE, nonce)):
        response.set_cookie(
            name,
            value,
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
            max_age=600,
        )
    return response


@router.get("/auth/google/callback", response_model=None)
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    expected_state = request.cookies.get(GOOGLE_STATE_COOKIE)
    expected_nonce = request.cookies.get(GOOGLE_NONCE_COOKIE)
    valid_state = bool(state and expected_state and secrets.compare_digest(state, expected_state))
    if error or not code or not expected_nonce or not valid_state:
        AuditLogRepository(db).record(action="google_login", entity="user", result="FAILURE")
        db.commit()
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Login com Google cancelado ou invalido.",
                "google_oauth_enabled": settings.google_oauth_enabled,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        identity = exchange_code_for_identity(
            settings=settings, code=code, expected_nonce=expected_nonce
        )
    except GoogleOAuthError:
        AuditLogRepository(db).record(action="google_login", entity="user", result="FAILURE")
        db.commit()
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Nao foi possivel validar sua conta Google.",
                "google_oauth_enabled": settings.google_oauth_enabled,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user = UserRepository(db).get_by_email(identity.email)
    if user is None or not user.is_active:
        AuditLogRepository(db).record(
            action="google_login", entity="user", detail=f"email={identity.email}", result="FAILURE"
        )
        db.commit()
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Este e-mail Google nao possui acesso ao sistema.",
                "google_oauth_enabled": settings.google_oauth_enabled,
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )

    token = create_access_token(
        subject=user.username,
        settings=settings,
        extra_claims={"roles": [role.name for role in user.roles], "auth_provider": "google"},
    )
    AuditLogRepository(db).record(
        user_id=user.id, action="google_login", entity="user", result="SUCCESS"
    )
    db.commit()
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=settings.auth_access_token_expire_minutes * 60,
    )
    response.delete_cookie(GOOGLE_STATE_COOKIE)
    response.delete_cookie(GOOGLE_NONCE_COOKIE)
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
