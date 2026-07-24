from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.mt5.auto_sync import MT5AutoSyncWorker
from app.mt5.market_data import RawCandle


def _candle(open_time: datetime) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        open=1.1,
        high=1.2,
        low=1.0,
        close=1.15,
        tick_volume=100,
        spread=2,
        real_volume=0,
    )


def test_closed_candles_excludes_bar_still_in_formation() -> None:
    now = datetime(2026, 7, 23, 12, 10, tzinfo=UTC)
    closed = _candle(now - timedelta(minutes=20))
    open_bar = _candle(now - timedelta(minutes=10))

    result = MT5AutoSyncWorker._closed_candles(
        [closed, open_bar],
        server_now=now,
        timeframe_seconds=15 * 60,
    )

    assert result == [closed]
