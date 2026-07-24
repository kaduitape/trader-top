"""Healthcheck. Nao depende de autenticacao — usado por monitoramento
externo e pelo proprio dashboard para exibir o status do sistema."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.enums import SystemMode
from app.database.repositories.system_setting_repository import get_current_mode
from app.database.session import get_db

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    app_name: str
    app_env: str
    system_mode: SystemMode
    database_connected: bool
    checked_at: datetime


@router.get("/health", response_model=HealthResponse)
def health(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> HealthResponse:
    database_connected = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database_connected = False

    # O modo real e persistido (`system_settings`, a partir da Fase 10) —
    # `settings.system_mode` e apenas o valor de BOOT antes de qualquer
    # `mode set`, nunca o modo atual de verdade. Reportar o valor estatico
    # aqui seria um healthcheck mentiroso assim que o sistema avancasse de
    # modo (bug real, corrigido na Fase 15).
    system_mode = get_current_mode(db) if database_connected else settings.system_mode

    return HealthResponse(
        status="ok" if database_connected else "degraded",
        app_name=settings.app_name,
        app_env=settings.app_env,
        system_mode=system_mode,
        database_connected=database_connected,
        checked_at=datetime.now(UTC),
    )
