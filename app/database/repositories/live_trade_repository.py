"""Repositório de `LiveTrade` (Fase 11). Garante no máximo uma posição
ativa (`POSITION_OPEN` ou `RECONCILING`) por (`symbol_id`, `timeframe`,
`strategy_name`) a nível de aplicação — mesma razão de
`PaperTradeRepository` (Fase 10).

Todas as consultas de estado aceitam `timeframe=None`, que significa
"todos os timeframes desta estratégia neste símbolo". Isso existe para o
piloto automático (`app.execution.autopilot`), que troca de timeframe
conforme o horário/volume: sem esse escopo mais amplo, mudar de M5 para
M15 esconderia a posição aberta e os contadores do dia, e os limites de
risco seriam contornados sem querer."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models.live_trade import LiveTrade
from app.database.models.symbol import Symbol
from app.execution.order_state import OrderState

_ACTIVE_STATES = (OrderState.POSITION_OPEN.value, OrderState.RECONCILING.value)


class LiveTradeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _scope(symbol_id: int, timeframe: str | None, strategy_name: str) -> list:
        """Filtros comuns. `timeframe=None` abrange todos os timeframes."""
        conditions = [
            LiveTrade.symbol_id == symbol_id,
            LiveTrade.strategy_name == strategy_name,
        ]
        if timeframe is not None:
            conditions.append(LiveTrade.timeframe == timeframe)
        return conditions

    def get_active_position(
        self, symbol_id: int, timeframe: str | None, strategy_name: str
    ) -> LiveTrade | None:
        stmt = (
            select(LiveTrade)
            .where(
                *self._scope(symbol_id, timeframe, strategy_name),
                LiveTrade.order_state.in_(_ACTIVE_STATES),
            )
            .order_by(LiveTrade.signal_time.desc())
        )
        return self._session.execute(stmt).scalars().first()

    def create(
        self,
        *,
        symbol_id: int,
        timeframe: str,
        strategy_name: str,
        model_version: str,
        signal_id: str,
        direction: str,
        order_state: OrderState,
        signal_time: datetime,
        rejection_reason: str | None = None,
        mt5_order_ticket: int | None = None,
        mt5_position_ticket: int | None = None,
        entry_time: datetime | None = None,
        entry_price: Decimal | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        volume: Decimal | None = None,
    ) -> LiveTrade:
        trade = LiveTrade(
            symbol_id=symbol_id,
            timeframe=timeframe,
            strategy_name=strategy_name,
            model_version=model_version,
            signal_id=signal_id,
            direction=direction,
            order_state=order_state.value,
            rejection_reason=rejection_reason,
            mt5_order_ticket=mt5_order_ticket,
            mt5_position_ticket=mt5_position_ticket,
            signal_time=signal_time,
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
        trade: LiveTrade,
        *,
        exit_time: datetime,
        exit_price: Decimal,
        net_pnl: Decimal,
    ) -> None:
        trade.order_state = OrderState.CLOSED.value
        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.net_pnl = net_pnl
        self._session.flush()

    def mark_reconciling(self, trade: LiveTrade) -> None:
        trade.order_state = OrderState.RECONCILING.value
        self._session.flush()

    def list_recent(
        self, symbol_id: int, timeframe: str | None, strategy_name: str, limit: int = 50
    ) -> list[LiveTrade]:
        stmt = (
            select(LiveTrade)
            .where(*self._scope(symbol_id, timeframe, strategy_name))
            .order_by(LiveTrade.signal_time.desc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars().all())

    def list_all_recent(self, limit: int = 50) -> list[tuple[LiveTrade, str]]:
        """Para o dashboard (Fase 12): todos os simbolos/estrategias, mais
        recentes primeiro, com o nome do simbolo ja resolvido via join."""
        stmt = (
            select(LiveTrade, Symbol.name)
            .join(Symbol, Symbol.id == LiveTrade.symbol_id)
            .order_by(LiveTrade.signal_time.desc())
            .limit(limit)
        )
        return [(trade, symbol_name) for trade, symbol_name in self._session.execute(stmt)]

    def count_entries_since(
        self, symbol_id: int, timeframe: str | None, strategy_name: str, *, since: datetime
    ) -> int:
        """Conta posições que chegaram a abrir hoje (qualquer estado atual
        — inclusive já fechadas) — é isso que o limite diário de trades
        deve contar, não apenas as ainda abertas."""
        stmt = select(func.count()).where(
            *self._scope(symbol_id, timeframe, strategy_name),
            LiveTrade.entry_time.isnot(None),
            LiveTrade.entry_time >= since,
        )
        return int(self._session.execute(stmt).scalar_one())

    def get_recent_closed(
        self, symbol_id: int, timeframe: str | None, strategy_name: str, limit: int = 20
    ) -> list[LiveTrade]:
        """Mais recentes primeiro — usado para contar perdas consecutivas,
        que não são limitadas ao dia corrente (uma sequência de perdas de
        ontem ainda deveria bloquear a manhã seguinte, até intervenção)."""
        stmt = (
            select(LiveTrade)
            .where(
                *self._scope(symbol_id, timeframe, strategy_name),
                LiveTrade.order_state == OrderState.CLOSED.value,
            )
            .order_by(LiveTrade.exit_time.desc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars().all())

    def sum_net_pnl_since(
        self, symbol_id: int, timeframe: str | None, strategy_name: str, *, since: datetime
    ) -> float:
        stmt = select(func.coalesce(func.sum(LiveTrade.net_pnl), 0)).where(
            *self._scope(symbol_id, timeframe, strategy_name),
            LiveTrade.order_state == OrderState.CLOSED.value,
            LiveTrade.exit_time >= since,
        )
        result = self._session.execute(stmt).scalar_one()
        return float(result) if result is not None else 0.0

    def get_last_entry_time(
        self, symbol_id: int, timeframe: str | None, strategy_name: str
    ) -> datetime | None:
        stmt = select(func.max(LiveTrade.entry_time)).where(
            *self._scope(symbol_id, timeframe, strategy_name),
            LiveTrade.entry_time.isnot(None),
        )
        return self._session.execute(stmt).scalar_one_or_none()
