"""Repositorio de configuracao persistida chave-valor, e a orquestracao do
modo do sistema (Fase 10) construida sobre ele.

`get_current_mode`/`set_mode` vivem aqui (e nao em `app.core.system_mode`,
que contem so a validacao pura) porque precisam de acesso a banco
(`SystemSettingRepository` + `AuditLogRepository`) — `app.core` nunca
importa `app.database`, para evitar import circular."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import SystemMode
from app.core.system_mode import validate_transition
from app.database.models.system_setting import SystemSetting
from app.database.repositories.audit_log_repository import AuditLogRepository

_SYSTEM_MODE_KEY = "system_mode"


class SystemSettingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> str | None:
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        row = self._session.execute(stmt).scalar_one_or_none()
        return row.value if row is not None else None

    def set(self, key: str, value: str, *, description: str | None = None) -> None:
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            self._session.add(SystemSetting(key=key, value=value, description=description))
        else:
            row.value = value
            if description is not None:
                row.description = description
        self._session.flush()


def get_current_mode(session: Session) -> SystemMode:
    """O sistema sempre inicia em `DISABLED` (prompt mestre) quando nenhum
    valor foi persistido ainda."""
    value = SystemSettingRepository(session).get(_SYSTEM_MODE_KEY)
    return SystemMode(value) if value is not None else SystemMode.DISABLED


def set_mode(
    session: Session, target: SystemMode, *, reason: str, user_id: int | None = None
) -> SystemMode:
    """Valida a transicao (`app.core.system_mode.validate_transition`),
    persiste o novo modo e grava uma entrada de auditoria — nunca uma
    dessas coisas sem a outra. Levanta `SystemModeError` (sem efeito
    colateral) se a transicao nao for permitida."""
    current = get_current_mode(session)
    validate_transition(current, target)

    SystemSettingRepository(session).set(
        _SYSTEM_MODE_KEY, target.value, description="Modo operacional atual do sistema."
    )
    AuditLogRepository(session).record(
        action="system_mode_change",
        entity="system_mode",
        detail=f"{current.value} -> {target.value}: {reason}",
        user_id=user_id,
    )
    return target
