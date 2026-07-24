"""Repositório de `DriftEvent` (Fase 13). Só persiste ocorrências
`WARNING`/`CRITICAL` — quem chama nunca deve gravar um resultado `NONE`
(ver `app.monitoring.drift`)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.drift_event import DriftEvent


class DriftEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        drift_type: str,
        severity: str,
        metric_name: str,
        current_value: float,
        detail: str,
        model_version: str | None = None,
        symbol_id: int | None = None,
        timeframe: str | None = None,
        baseline_value: float | None = None,
        threshold_value: float | None = None,
    ) -> DriftEvent:
        event = DriftEvent(
            drift_type=drift_type,
            severity=severity,
            model_version=model_version,
            symbol_id=symbol_id,
            timeframe=timeframe,
            metric_name=metric_name,
            baseline_value=baseline_value,
            current_value=current_value,
            threshold_value=threshold_value,
            detail=detail,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def list_recent(self, limit: int = 50) -> list[DriftEvent]:
        stmt = select(DriftEvent).order_by(DriftEvent.detected_at.desc()).limit(limit)
        return list(self._session.execute(stmt).scalars().all())
