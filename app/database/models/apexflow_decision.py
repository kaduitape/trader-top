"""Learning Engine: toda decisao do ApexFlow AI, operada ou nao.

Uma linha por decisao — inclusive (e principalmente) as de NAO OPERAR, que
sao a maioria. Registrar so as entradas produziria um historico
enviesado: seria impossivel avaliar depois se o robo deixou passar boas
oportunidades ou se acertou ao ficar de fora.

O `feature_vector` e gravado como JSON junto de `feature_version`, entao
um modelo treinado no futuro pode ser reavaliado contra exatamente os
mesmos sensores que o motor tinha no momento — sem depender de
reconstruir dados de mercado que ja mudaram.

`live_trade_id` liga a decisao a execucao quando ela virou ordem, e os
campos de resultado sao preenchidos depois, quando a operacao fecha.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class ApexFlowDecisionRecord(Base, TimestampMixin):
    __tablename__ = "apexflow_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timeframe: Mapped[str] = mapped_column(String(5), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    action: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    probability_buy: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    probability_sell: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    probability_abstain: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    min_confidence: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)

    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(50), nullable=False)
    completeness: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)

    context_state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    session_rating: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    volume_level: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    spread_points: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    atr_points: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    ticks_per_second: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    mtf_alignment: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)

    vetoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature_vector: Mapped[str | None] = mapped_column(Text, nullable=True)

    live_trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("live_trades.id", ondelete="SET NULL"), nullable=True, index=True
    )
    result_net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    result_r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    result_max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"ApexFlowDecisionRecord(id={self.id!r}, symbol_id={self.symbol_id!r}, "
            f"action={self.action!r}, confidence={self.confidence!r})"
        )
