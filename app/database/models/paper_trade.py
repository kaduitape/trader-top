"""Posições/trades de paper trading (Fase 10) — nunca uma ordem real.

Uma linha por posição: `status="OPEN"` enquanto aberta (campos de saída
nulos), `status="CLOSED"` depois de resolvida. No máximo uma linha
`OPEN` por (`symbol_id`, `timeframe`, `strategy_name`) — imposto pelo
`PaperTradeRepository`, não por uma constraint de banco (um índice único
parcial não é portável entre SQLite e MySQL sem complexidade extra)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class PaperTrade(Base, TimestampMixin):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timeframe: Mapped[str] = mapped_column(String(5), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False, default="rule-based")
    signal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN", index=True)

    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    take_profit: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)

    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    bars_held: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"PaperTrade(id={self.id!r}, symbol_id={self.symbol_id!r}, "
            f"strategy_name={self.strategy_name!r}, status={self.status!r})"
        )
