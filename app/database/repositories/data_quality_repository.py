"""Repositorio de ocorrencias de qualidade de dados. Cada chamada de
`bulk_insert` grava um evento por `DataQualityIssue` — e um log de
ocorrencias, nao um snapshot deduplicado (o mesmo problema pode, e deve,
ser registrado de novo se persistir em coletas futuras)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.database.models.data_quality_event import DataQualityEvent
from app.market.data_quality import DataQualityIssue


class DataQualityEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def bulk_insert(
        self, symbol_id: int, timeframe: str | None, issues: Sequence[DataQualityIssue]
    ) -> int:
        if not issues:
            return 0

        rows = [
            DataQualityEvent(
                symbol_id=symbol_id,
                timeframe=timeframe,
                check_name=issue.check,
                severity=issue.severity.value,
                message=issue.message,
            )
            for issue in issues
        ]
        self._session.add_all(rows)
        self._session.flush()
        return len(rows)
