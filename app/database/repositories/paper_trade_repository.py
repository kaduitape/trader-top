"""Repositorio de paper trades (Fase 10). Garante no maximo uma posicao
`OPEN` por (`symbol_id`, `timeframe`, `strategy_name`) a nivel de
aplicacao — ver `app.database.models.paper_trade` sobre por que isso nao
e uma constraint de banco nesta fase."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.paper_trade import PaperTrade
from app.database.models.symbol import Symbol


class PaperTradeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_open(self, symbol_id: int, timeframe: str, strategy_name: str) -> PaperTrade | None:
        stmt = select(PaperTrade).where(
            PaperTrade.symbol_id == symbol_id,
            PaperTrade.timeframe == timeframe,
            PaperTrade.strategy_name == strategy_name,
            PaperTrade.status == "OPEN",
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def open_position(
        self,
        *,
        symbol_id: int,
        timeframe: str,
        strategy_name: str,
        model_version: str,
        signal_id: str,
        direction: str,
        entry_time: datetime,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        volume: Decimal,
    ) -> PaperTrade:
        trade = PaperTrade(
            symbol_id=symbol_id,
            timeframe=timeframe,
            strategy_name=strategy_name,
            model_version=model_version,
            signal_id=signal_id,
            direction=direction,
            status="OPEN",
            entry_time=entry_time,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            volume=volume,
        )
        self._session.add(trade)
        self._session.flush()
        return trade

    def close_position(
        self,
        trade: PaperTrade,
        *,
        exit_time: datetime,
        exit_price: Decimal,
        exit_reason: str,
        net_pnl: Decimal,
        bars_held: int,
    ) -> None:
        trade.status = "CLOSED"
        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.net_pnl = net_pnl
        trade.bars_held = bars_held
        self._session.flush()

    def list_recent(
        self, symbol_id: int, timeframe: str, strategy_name: str, limit: int = 50
    ) -> list[PaperTrade]:
        stmt = (
            select(PaperTrade)
            .where(
                PaperTrade.symbol_id == symbol_id,
                PaperTrade.timeframe == timeframe,
                PaperTrade.strategy_name == strategy_name,
            )
            .order_by(PaperTrade.entry_time.desc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars().all())

    def list_all_recent(self, limit: int = 50) -> list[tuple[PaperTrade, str]]:
        """Para o dashboard (Fase 12): todos os simbolos/estrategias, mais
        recentes primeiro, com o nome do simbolo ja resolvido via join
        (evita N+1 queries ao montar a tabela)."""
        stmt = (
            select(PaperTrade, Symbol.name)
            .join(Symbol, Symbol.id == PaperTrade.symbol_id)
            .order_by(PaperTrade.entry_time.desc())
            .limit(limit)
        )
        return [(trade, symbol_name) for trade, symbol_name in self._session.execute(stmt)]
