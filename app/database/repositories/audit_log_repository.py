"""Repositorio de auditoria. Toda acao sensivel (login, mudanca de modo do
sistema, alteracao de configuracao) grava uma linha aqui — nunca apenas um
log de aplicacao, que pode ser rotacionado/perdido."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.audit_log import AuditLog


class AuditLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        action: str,
        entity: str | None = None,
        detail: str | None = None,
        result: str = "SUCCESS",
        user_id: int | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            action=action, entity=entity, detail=detail, result=result, user_id=user_id
        )
        self._session.add(entry)
        self._session.flush()
        return entry

    def list_recent(self, limit: int = 50) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit)
        return list(self._session.execute(stmt).scalars().all())
