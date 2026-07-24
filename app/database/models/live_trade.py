"""Ordens/posições enviadas a uma conta DEMO (Fase 11) — nunca uma conta
real. Uma linha por sinal processado pelo executor, mesmo quando
rejeitado pelo risco ou pelo broker — auditoria completa de TODO sinal,
não só dos que viraram posição (exigência do prompt mestre: nunca
esconder o que aconteceu com um sinal).

`order_state` segue `app.execution.order_state.OrderState`. No máximo uma
linha com `order_state="POSITION_OPEN"` por (`symbol_id`, `timeframe`,
`strategy_name`) — imposto pelo `LiveTradeRepository`, mesma razão da
Fase 10 (`PaperTrade`): um índice único parcial não é portável entre
SQLite e MySQL sem complexidade extra."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class LiveTrade(Base, TimestampMixin):
    __tablename__ = "live_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timeframe: Mapped[str] = mapped_column(String(5), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False, default="rule-based")
    signal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    order_state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    mt5_order_ticket: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mt5_position_ticket: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)

    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    def __repr__(self) -> str:
        return (
            f"LiveTrade(id={self.id!r}, symbol_id={self.symbol_id!r}, "
            f"strategy_name={self.strategy_name!r}, order_state={self.order_state!r})"
        )
