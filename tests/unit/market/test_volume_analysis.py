from datetime import UTC, datetime, timedelta

import pandas as pd

from app.market.structure import SwingKind, SwingPoint
from app.market.volume_analysis import VolumeEventKind, detect_volume_events
from app.mt5.market_data import RawCandle

_START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def _candle(i: int) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=i),
        open=100.0,
        high=105.0,
        low=95.0,
        close=101.0,
        tick_volume=100,
        spread=2,
        real_volume=0,
    )


def _candles(n: int) -> list[RawCandle]:
    return [_candle(i) for i in range(n)]


def _features(rows: list[dict[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _swing(index: int, price: float, kind: SwingKind) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=_START + timedelta(minutes=index),
        price=price,
        kind=kind,
        confirmed_at_index=index,
    )


def test_detects_climax() -> None:
    features = _features(
        [{"relative_volume_20": 2.5, "rsi_14": 50.0, "candle_body": 8.0, "candle_amplitude": 10.0}]
    )
    events = detect_volume_events(_candles(1), features, [])

    assert len(events) == 1
    assert events[0].kind == VolumeEventKind.CLIMAX
    assert events[0].index == 0


def test_detects_absorption() -> None:
    features = _features(
        [{"relative_volume_20": 2.5, "rsi_14": 50.0, "candle_body": 1.0, "candle_amplitude": 10.0}]
    )
    events = detect_volume_events(_candles(1), features, [])

    assert len(events) == 1
    assert events[0].kind == VolumeEventKind.ABSORPTION


def test_detects_exhaustion_after_climax() -> None:
    features = _features(
        [
            {
                "relative_volume_20": 2.5,
                "rsi_14": 50.0,
                "candle_body": 8.0,
                "candle_amplitude": 10.0,
            },
            {
                "relative_volume_20": 0.5,
                "rsi_14": 50.0,
                "candle_body": 4.0,
                "candle_amplitude": 10.0,
            },
        ]
    )
    events = detect_volume_events(_candles(2), features, [])

    kinds_by_index = {e.index: e.kind for e in events}
    assert kinds_by_index[0] == VolumeEventKind.CLIMAX
    assert kinds_by_index[1] == VolumeEventKind.EXHAUSTION


def _quiet_row() -> dict[str, float]:
    return {"relative_volume_20": 1.0, "rsi_14": 50.0, "candle_body": 5.0, "candle_amplitude": 10.0}


def test_detects_bullish_divergence() -> None:
    rows = [_quiet_row() for _ in range(6)]
    rows[1]["rsi_14"] = 30.0
    rows[4]["rsi_14"] = 40.0
    features = _features(rows)

    swings = [
        _swing(1, 100.0, SwingKind.LOW),
        _swing(4, 95.0, SwingKind.LOW),  # fundo mais baixo, RSI mais alto -- divergencia de alta
    ]
    events = detect_volume_events(_candles(6), features, swings)

    divergences = [e for e in events if e.kind == VolumeEventKind.BULLISH_DIVERGENCE]
    assert len(divergences) == 1
    assert divergences[0].index == 4


def test_detects_bearish_divergence() -> None:
    rows = [_quiet_row() for _ in range(6)]
    rows[1]["rsi_14"] = 70.0
    rows[4]["rsi_14"] = 60.0
    features = _features(rows)

    swings = [
        _swing(1, 100.0, SwingKind.HIGH),
        _swing(4, 105.0, SwingKind.HIGH),  # topo mais alto, RSI mais baixo -- divergencia de baixa
    ]
    events = detect_volume_events(_candles(6), features, swings)

    divergences = [e for e in events if e.kind == VolumeEventKind.BEARISH_DIVERGENCE]
    assert len(divergences) == 1
    assert divergences[0].index == 4


def test_quiet_market_produces_no_events() -> None:
    rows = [_quiet_row() for _ in range(5)]
    features = _features(rows)
    events = detect_volume_events(_candles(5), features, [])
    assert events == []


def test_empty_swings_does_not_break_divergence_detection() -> None:
    rows = [_quiet_row() for _ in range(3)]
    features = _features(rows)
    events = detect_volume_events(_candles(3), features, [])
    assert events == []


def test_empty_candles_and_features_never_raise() -> None:
    assert detect_volume_events([], pd.DataFrame(), []) == []
