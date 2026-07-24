"""Registro de ocorrências de drift detectadas por `app.monitoring.drift`
— uma linha por ocorrência (não por execução de checagem), mesmo padrão
de `DataQualityEvent` (Fase 3). Só `WARNING`/`CRITICAL` são persistidos
— um resultado `NONE` (sem drift) não gera linha, para não inchar a
tabela com "está tudo bem" repetido a cada execução (mesmo raciocínio de
uma tabela de alertas, não de log de aplicação)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DriftEvent(Base):
    __tablename__ = "drift_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    drift_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    """`FEATURE`, `CALIBRATION`, `PERFORMANCE` ou `DATA_FEED`."""
    severity: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    """`WARNING` ou `CRITICAL` — `NONE` nunca é persistido."""

    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    symbol_id: Mapped[int | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True, index=True
    )
    timeframe: Mapped[str | None] = mapped_column(String(5), nullable=True)

    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    detail: Mapped[str] = mapped_column(String(1000), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )

    def __repr__(self) -> str:
        return f"DriftEvent(drift_type={self.drift_type!r}, severity={self.severity!r})"
