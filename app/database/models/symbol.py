"""Simbolo negociavel e sua especificacao (limites de volume, casas
decimais). Mantidos em uma unica tabela nesta fase — ver docs/data-model.md
para a justificativa de nao criar `symbol_specifications` separada ainda."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class Symbol(Base, TimestampMixin):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    digits: Mapped[int] = mapped_column(Integer, nullable=False)
    point: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    volume_min: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    volume_max: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    volume_step: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    trade_contract_size: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"Symbol(name={self.name!r})"
