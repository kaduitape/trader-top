"""Registro de ocorrencias de qualidade de dados detectadas por
`app.market.data_quality` — uma linha por ocorrencia (nao por execucao de
checagem), permitindo consultar o historico de um simbolo ao longo do
tempo."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DataQualityEvent(Base):
    __tablename__ = "data_quality_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timeframe: Mapped[str | None] = mapped_column(String(5), nullable=True)
    check_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )

    def __repr__(self) -> str:
        return f"DataQualityEvent(check={self.check_name!r}, severity={self.severity!r})"
