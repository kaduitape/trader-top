"""Repositorio de candles. A insercao e sempre deduplicada por
(symbol_id, timeframe, open_time) — nunca cria candles duplicados, mesmo
que a mesma janela seja coletada mais de uma vez (ex.: coletas agendadas
com sobreposicao)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.database.models.candle import Candle
from app.database.models.symbol import Symbol
from app.database.repositories._time_utils import as_aware_utc
from app.mt5.market_data import RawCandle


def _decimal(value: float) -> Decimal:
    """Converte via `str()` para evitar herdar a imprecisao binaria do
    `float` ao gravar em colunas `Numeric`."""
    return Decimal(str(value))


class CandleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def bulk_upsert(self, symbol_id: int, timeframe: str, candles: list[RawCandle]) -> int:
        """Insere as candles que ainda nao existem. Retorna quantas foram
        efetivamente inseridas."""
        if not candles:
            return 0

        open_times = [c.open_time for c in candles]
        stmt = select(Candle.open_time).where(
            Candle.symbol_id == symbol_id,
            Candle.timeframe == timeframe,
            Candle.open_time.in_(open_times),
        )
        existing_times = {as_aware_utc(row[0]) for row in self._session.execute(stmt)}

        new_rows = [
            Candle(
                symbol_id=symbol_id,
                timeframe=timeframe,
                open_time=c.open_time,
                open=_decimal(c.open),
                high=_decimal(c.high),
                low=_decimal(c.low),
                close=_decimal(c.close),
                tick_volume=c.tick_volume,
                spread=c.spread,
                real_volume=c.real_volume,
            )
            for c in candles
            if c.open_time not in existing_times
        ]

        if new_rows:
            self._session.add_all(new_rows)
            self._session.flush()

        return len(new_rows)

    def get_last_open_time(self, symbol_id: int, timeframe: str) -> datetime | None:
        """Usado para preenchimento incremental: se houver candle
        conhecida, so buscamos do MetaTrader o que vier depois dela."""
        stmt = (
            select(Candle.open_time)
            .where(Candle.symbol_id == symbol_id, Candle.timeframe == timeframe)
            .order_by(desc(Candle.open_time))
            .limit(1)
        )
        row = self._session.execute(stmt).first()
        return as_aware_utc(row[0]) if row is not None else None

    def get_recent(self, symbol_id: int, timeframe: str, limit: int) -> list[Candle]:
        """Retorna as `limit` candles mais recentes, em ordem cronologica
        crescente (para que checagens de gap/ordem facam sentido)."""
        stmt = (
            select(Candle)
            .where(Candle.symbol_id == symbol_id, Candle.timeframe == timeframe)
            .order_by(desc(Candle.open_time))
            .limit(limit)
        )
        rows = list(self._session.execute(stmt).scalars())
        return list(reversed(rows))

    def get_all(
        self,
        symbol_id: int,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Retorna todas as candles armazenadas (opcionalmente filtradas
        por periodo), em ordem cronologica crescente. Usado pelo backtester
        (Fase 5), que precisa do historico completo, nao apenas das mais
        recentes."""
        stmt = select(Candle).where(Candle.symbol_id == symbol_id, Candle.timeframe == timeframe)
        if start is not None:
            stmt = stmt.where(Candle.open_time >= start)
        if end is not None:
            stmt = stmt.where(Candle.open_time <= end)
        stmt = stmt.order_by(Candle.open_time)
        return list(self._session.execute(stmt).scalars())

    def summary(self) -> list[tuple[str, str, int, datetime, datetime]]:
        """Visao agregada por simbolo+timeframe (quantidade de candles,
        primeira/ultima aberta) -- usada pela pagina `/dashboard/
        market-data` (Fase 16), nao pelo pipeline de coleta/backtest."""
        stmt = (
            select(
                Symbol.name,
                Candle.timeframe,
                func.count(Candle.id),
                func.min(Candle.open_time),
                func.max(Candle.open_time),
            )
            .join(Symbol, Symbol.id == Candle.symbol_id)
            .group_by(Symbol.name, Candle.timeframe)
            .order_by(Symbol.name, Candle.timeframe)
        )
        return list(self._session.execute(stmt).tuples().all())
