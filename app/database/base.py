"""Base declarativa e mixins compartilhados entre todos os modelos ORM."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base declarativa unica de todo o schema. Toda migration Alembic
    depende de `Base.metadata` para autodetectar alteracoes de modelo."""


class TimestampMixin:
    """Adiciona `created_at`/`updated_at` em UTC a qualquer modelo."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
