"""Login/logout do dashboard HTML (Fase 12) — sessao via cookie httpOnly,
nao via header `Authorization` (o navegador nao o anexa sozinho numa
navegacao normal de pagina). O mesmo JWT/segredo da API (`/api/auth/
login`) e reusado — nao existe um segundo mecanismo de autenticacao
paralelo, apenas um transporte diferente (cookie em vez de header)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies.auth import SESSION_COOKIE_NAME
from app.api.templates_engine import templates
from app.core.config import Settings, get_settings
from app.core.security import create_access_token, verify_password
from app.database.repositories.audit_log_repository import AuditLogRepository
from app.database.repositories.user_repository import UserRepository
from app.database.session import get_db

router = APIRouter(tags=["web-auth"])


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None})


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
            {"error": "Usuario ou senha invalidos."},
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


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
