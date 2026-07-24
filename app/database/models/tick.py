"""Ticks (bid/ask/last) por simbolo.

Unicidade em (symbol_id, timestamp, bid, ask) e a estrategia de deduplicacao
pragmatica desta fase, documentada em docs/data-model.md secao 3 — sera
revisada na Fase 3 caso a corretora forneca um identificador de sequencia
mais confiavel."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

# MySQL trunca DATETIME para resolucao de segundo INTEIRO por padrao (sem
# `fsp`), diferente do SQLite (usado nos testes), que preserva qualquer
# precisao livremente. Bug real, achado coletando ticks reais contra um
# MySQL de verdade pela primeira vez (Fase 16): duas ticks no MESMO
# segundo, com microssegundos DIFERENTES (logo, chaves de deduplicacao
# DIFERENTES do lado do Python) colidiam na unique constraint ao serem
# gravadas, porque o MySQL ja tinha truncado ambas para o mesmo segundo
# antes de comparar. `fsp=6` (microssegundos) e a UNICA forma de manter
# ticks reais (que chegam varias vezes por segundo) distinguiveis.
_TIMESTAMP_TYPE = DateTime(timezone=True).with_variant(MySQLDateTime(fsp=6), "mysql")


class Tick(Base):
    __tablename__ = "ticks"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id", "timestamp", "bid", "ask", name="uq_ticks_symbol_time_bid_ask"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(_TIMESTAMP_TYPE, nullable=False, index=True)
    bid: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    ask: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    last: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    flags: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"Tick(symbol_id={self.symbol_id!r}, timestamp={self.timestamp!r})"
