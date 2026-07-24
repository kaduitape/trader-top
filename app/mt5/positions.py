"""Leitura de posicoes abertas — somente leitura, nenhuma modificacao."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.mt5.client import MT5ClientProtocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawPosition:
    ticket: int
    symbol: str
    volume: float
    price_open: float
    price_current: float
    profit: float
    swap: float
    position_type: int
    opened_at: datetime
    magic: int
    comment: str


def _row_to_position(row: object) -> RawPosition:
    return RawPosition(
        ticket=int(getattr(row, "ticket", 0)),
        symbol=str(getattr(row, "symbol", "")),
        volume=float(getattr(row, "volume", 0.0)),
        price_open=float(getattr(row, "price_open", 0.0)),
        price_current=float(getattr(row, "price_current", 0.0)),
        profit=float(getattr(row, "profit", 0.0)),
        swap=float(getattr(row, "swap", 0.0)),
        position_type=int(getattr(row, "type", 0)),
        opened_at=datetime.fromtimestamp(int(getattr(row, "time", 0)), tz=UTC),
        magic=int(getattr(row, "magic", 0)),
        comment=str(getattr(row, "comment", "")),
    )


def fetch_open_positions(client: MT5ClientProtocol, symbol: str | None = None) -> list[RawPosition]:
    rows = client.positions_get(symbol=symbol)
    if rows is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_positions_get_failed",
            extra={"mt5_error_code": code, "mt5_error_description": description},
        )
        return []
    return [_row_to_position(row) for row in rows]
