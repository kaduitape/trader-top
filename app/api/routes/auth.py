"""Rotas de autenticacao basica: login (emissao de JWT) e usuario atual."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.api.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.core.config import Settings, get_settings
from app.core.security import create_access_token, verify_password
from app.database.models.audit_log import AuditLog
from app.database.models.user import User
from app.database.repositories.user_repository import UserRepository
from app.database.session import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/login", response_model=TokenResponse)
def login(
    credentials: LoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    user = UserRepository(db).get_by_username(credentials.username)

    if (
        user is None
        or not user.is_active
        or not verify_password(credentials.password, user.password_hash)
    ):
        db.add(
            AuditLog(
                action="login",
                entity="user",
                detail=f"username={credentials.username}",
                result="FAILURE",
            )
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario ou senha invalidos.",
        )

    token = create_access_token(
        subject=user.username,
        settings=settings,
        extra_claims={"roles": [role.name for role in user.roles]},
    )

    db.add(AuditLog(user_id=user.id, action="login", entity="user", result="SUCCESS"))
    db.commit()

    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.auth_access_token_expire_minutes,
    )


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
        roles=[role.name for role in current_user.roles],
    )
