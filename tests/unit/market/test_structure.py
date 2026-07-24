from datetime import UTC, datetime, timedelta

import pytest

from app.market.price_action import PatternDirection
from app.market.structure import (
    StructureEventType,
    StructureLabel,
    SwingKind,
    SwingPoint,
    TrendStructureLabel,
    cluster_swing_levels,
    detect_channel,
    detect_structure_events,
    detect_swings,
    fit_trendline,
    label_swing_structure,
    pivot_points,
)
from app.mt5.market_data import RawCandle

_START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def _candle(
    i: int, *, high: float, low: float, close: float, open_: float | None = None
) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=i),
        open=open_ if open_ is not None else close,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        spread=2,
        real_volume=0,
    )


# --- detect_swings --------------------------------------------------------

_ZIGZAG = [
    (100, 99),  # 0
    (101, 100),  # 1
    (105, 101),  # 2  <- swing high (105)
    (102, 100),  # 3
    (101, 99),  # 4
    (100, 95),  # 5  <- swing low (95)
    (102, 97),  # 6
    (104, 98),  # 7
    (108, 103),  # 8  <- swing high (108)
    (105, 100),  # 9
    (103, 99),  # 10
]


def _zigzag_candles(rows: list[tuple[float, float]]) -> list[RawCandle]:
    return [_candle(i, high=h, low=low, close=(h + low) / 2) for i, (h, low) in enumerate(rows)]


def test_detect_swings_exact_indices_and_prices() -> None:
    candles = _zigzag_candles(_ZIGZAG)
    swings = detect_swings(candles, left_bars=2, right_bars=2)

    assert [(s.index, s.price, s.kind, s.confirmed_at_index) for s in swings] == [
        (2, 105.0, SwingKind.HIGH, 4),
        (5, 95.0, SwingKind.LOW, 7),
        (8, 108.0, SwingKind.HIGH, 10),
    ]


def test_detect_swings_empty_or_short_sequence_never_raises() -> None:
    assert detect_swings([]) == []
    assert detect_swings(_zigzag_candles(_ZIGZAG[:3])) == []


def test_detect_swings_truncated_tail_does_not_change_earlier_swings() -> None:
    full = detect_swings(_zigzag_candles(_ZIGZAG), left_bars=2, right_bars=2)
    truncated = detect_swings(_zigzag_candles(_ZIGZAG[:9]), left_bars=2, right_bars=2)

    # A ultima swing (indice 8) exige barras futuras que o corte removeu --
    # ausencia esperada, nao um bug. As duas primeiras devem ser identicas.
    assert truncated == full[:2]
    assert len(truncated) == 2


# --- label_swing_structure -------------------------------------------------


def _swing(index: int, price: float, kind: SwingKind) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=_START + timedelta(minutes=index),
        price=price,
        kind=kind,
        confirmed_at_index=index,
    )


def test_label_swing_structure_clean_uptrend() -> None:
    swings = [
        _swing(2, 100, SwingKind.HIGH),
        _swing(4, 95, SwingKind.LOW),
        _swing(6, 105, SwingKind.HIGH),
        _swing(8, 98, SwingKind.LOW),
        _swing(10, 110, SwingKind.HIGH),
        _swing(12, 101, SwingKind.LOW),
    ]
    labels = label_swing_structure(swings)

    assert [(lab.swing.index, lab.label) for lab in labels] == [
        (6, TrendStructureLabel.HH),
        (8, TrendStructureLabel.HL),
        (10, TrendStructureLabel.HH),
        (12, TrendStructureLabel.HL),
    ]


def test_label_swing_structure_clean_downtrend() -> None:
    swings = [
        _swing(2, 100, SwingKind.HIGH),
        _swing(4, 95, SwingKind.LOW),
        _swing(6, 90, SwingKind.HIGH),
        _swing(8, 80, SwingKind.LOW),
        _swing(10, 85, SwingKind.HIGH),
        _swing(12, 70, SwingKind.LOW),
    ]
    labels = label_swing_structure(swings)

    assert [(lab.swing.index, lab.label) for lab in labels] == [
        (6, TrendStructureLabel.LH),
        (8, TrendStructureLabel.LL),
        (10, TrendStructureLabel.LH),
        (12, TrendStructureLabel.LL),
    ]


def test_label_swing_structure_sideways_mixed_labels_no_crash() -> None:
    swings = [
        _swing(2, 100, SwingKind.HIGH),
        _swing(4, 95, SwingKind.LOW),
        _swing(6, 98, SwingKind.HIGH),
        _swing(8, 97, SwingKind.LOW),
        _swing(10, 102, SwingKind.HIGH),
        _swing(12, 80, SwingKind.LOW),
    ]
    labels = label_swing_structure(swings)

    assert [(lab.swing.index, lab.label) for lab in labels] == [
        (6, TrendStructureLabel.LH),
        (8, TrendStructureLabel.HL),
        (10, TrendStructureLabel.HH),
        (12, TrendStructureLabel.LL),
    ]


def test_label_swing_structure_first_swing_of_each_kind_has_no_label() -> None:
    swings = [_swing(2, 100, SwingKind.HIGH), _swing(4, 95, SwingKind.LOW)]
    assert label_swing_structure(swings) == []


def test_label_swing_structure_empty_never_raises() -> None:
    assert label_swing_structure([]) == []


# --- detect_structure_events -----------------------------------------------


def _structure_label(swing: SwingPoint) -> StructureLabel:
    return StructureLabel(swing=swing, label=TrendStructureLabel.HH)


def test_detect_structure_events_continuation_is_bos() -> None:
    candles = [
        _candle(0, high=101, low=99, close=100),
        _candle(1, high=101, low=99, close=100),
        _candle(2, high=105, low=103, close=104),
        _candle(3, high=107, low=105, close=106),
    ]
    swing_high = SwingPoint(
        index=2,
        open_time=candles[2].open_time,
        price=105.0,
        kind=SwingKind.HIGH,
        confirmed_at_index=2,
    )
    events = detect_structure_events(candles, [_structure_label(swing_high)])

    assert len(events) == 1
    assert events[0].type == StructureEventType.BOS
    assert events[0].index == 3
    assert events[0].broken_level == 105.0
    assert events[0].direction == PatternDirection.BULLISH


def test_detect_structure_events_reversal_choch_then_mss_in_order() -> None:
    closes = [100, 100, 106, 96, 94, 93, 91, 89]
    candles = [_candle(i, high=c + 1, low=c - 1, close=c) for i, c in enumerate(closes)]

    swing_a = SwingPoint(
        index=1,
        open_time=candles[1].open_time,
        price=105.0,
        kind=SwingKind.HIGH,
        confirmed_at_index=1,
    )
    swing_b = SwingPoint(
        index=3,
        open_time=candles[3].open_time,
        price=95.0,
        kind=SwingKind.LOW,
        confirmed_at_index=3,
    )
    swing_c = SwingPoint(
        index=6,
        open_time=candles[6].open_time,
        price=90.0,
        kind=SwingKind.LOW,
        confirmed_at_index=6,
    )
    labels = [_structure_label(swing_a), _structure_label(swing_b), _structure_label(swing_c)]

    events = detect_structure_events(candles, labels)

    assert [(e.type, e.index, e.direction, e.broken_level) for e in events] == [
        (StructureEventType.BOS, 2, PatternDirection.BULLISH, 105.0),
        (StructureEventType.CHOCH, 4, PatternDirection.BEARISH, 95.0),
        (StructureEventType.MSS, 7, PatternDirection.BEARISH, 90.0),
    ]


def test_detect_structure_events_empty_inputs_never_raise() -> None:
    assert detect_structure_events([], []) == []


# --- pivot_points -----------------------------------------------------------


def test_pivot_points_hand_computed() -> None:
    levels = pivot_points(prev_high=110.0, prev_low=100.0, prev_close=105.0)

    assert levels.pivot == pytest.approx(105.0)
    assert levels.r1 == pytest.approx(110.0)
    assert levels.s1 == pytest.approx(100.0)
    assert levels.r2 == pytest.approx(115.0)
    assert levels.s2 == pytest.approx(95.0)
    assert levels.r3 == pytest.approx(120.0)
    assert levels.s3 == pytest.approx(90.0)


# --- cluster_swing_levels ---------------------------------------------------


def test_cluster_swing_levels_merges_within_tolerance_and_separates_outside() -> None:
    swings = [
        _swing(1, 100.05, SwingKind.HIGH),
        _swing(2, 100.08, SwingKind.HIGH),
        _swing(3, 101.0, SwingKind.HIGH),
        _swing(4, 50.0, SwingKind.LOW),
    ]
    levels = cluster_swing_levels(swings, tolerance_pct=0.1)

    resistance_levels = [lvl for lvl in levels if lvl.kind == "RESISTANCE"]
    support_levels = [lvl for lvl in levels if lvl.kind == "SUPPORT"]

    assert len(resistance_levels) == 2
    merged = next(lvl for lvl in resistance_levels if lvl.touches == 2)
    lone = next(lvl for lvl in resistance_levels if lvl.touches == 1)
    assert merged.price == pytest.approx((100.05 + 100.08) / 2)
    assert lone.price == pytest.approx(101.0)

    assert len(support_levels) == 1
    assert support_levels[0].touches == 1
    assert support_levels[0].price == pytest.approx(50.0)


def test_cluster_swing_levels_empty_never_raises() -> None:
    assert cluster_swing_levels([]) == []


# --- fit_trendline / detect_channel ----------------------------------------


def test_fit_trendline_perfectly_linear_points() -> None:
    line = fit_trendline([(0, 100.0), (1, 102.0), (2, 104.0), (3, 106.0)])
    assert line.slope == pytest.approx(2.0)
    assert line.intercept == pytest.approx(100.0)


def test_fit_trendline_requires_at_least_two_points() -> None:
    with pytest.raises(ValueError):
        fit_trendline([(0, 100.0)])


def test_detect_channel_ascending() -> None:
    highs = [_swing(i, 100 + i, SwingKind.HIGH) for i in (1, 4, 7)]
    lows = [_swing(i, 90 + i, SwingKind.LOW) for i in (2, 5, 8)]
    channel = detect_channel(highs + lows, min_points=3)

    assert channel is not None
    assert channel.kind == "ASCENDING"
    assert channel.upper.slope == pytest.approx(1.0)
    assert channel.lower.slope == pytest.approx(1.0)


def test_detect_channel_returns_none_with_insufficient_points() -> None:
    highs = [_swing(1, 100, SwingKind.HIGH), _swing(4, 101, SwingKind.HIGH)]
    lows = [_swing(2, 90, SwingKind.LOW), _swing(5, 91, SwingKind.LOW)]
    assert detect_channel(highs + lows, min_points=3) is None


def test_detect_channel_empty_never_raises() -> None:
    assert detect_channel([]) is None
