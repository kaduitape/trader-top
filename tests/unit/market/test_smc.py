from datetime import UTC, datetime, timedelta

import pytest

from app.market.price_action import CandlestickPattern, PatternDirection, PatternName
from app.market.smc import (
    LiquidityEventKind,
    compute_premium_discount,
    detect_equal_highs_lows,
    detect_fair_value_gaps,
    detect_fakey,
    detect_false_breakout,
    detect_liquidity_sweeps,
    detect_order_blocks,
    update_mitigation_status,
)
from app.market.structure import SRLevel, StructureEvent, StructureEventType, SwingKind, SwingPoint
from app.mt5.market_data import RawCandle

_START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def _candle(i: int, *, open_: float, high: float, low: float, close: float) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=i),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        spread=2,
        real_volume=0,
    )


def _swing(index: int, price: float, kind: SwingKind) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=_START + timedelta(minutes=index),
        price=price,
        kind=kind,
        confirmed_at_index=index,
    )


# --- order blocks + mitigacao -----------------------------------------------


def _order_block_candles() -> list[RawCandle]:
    return [
        _candle(0, open_=105, high=106, low=99, close=100),  # baixa -- vira o order block
        _candle(
            1, open_=100, high=103, low=99, close=102
        ),  # alta, low=99 ja mitiga a zona [99,106]
        _candle(2, open_=102, high=109, low=101, close=108),  # alta, rompimento (evento)
        _candle(3, open_=108, high=110, low=104, close=109),
        _candle(4, open_=109, high=109, low=98, close=99),
        _candle(5, open_=99, high=100, low=93, close=95),  # vira breaker (close < ob.low)
    ]


def test_detect_order_blocks_finds_last_opposite_candle() -> None:
    candles = _order_block_candles()
    event = StructureEvent(
        type=StructureEventType.BOS,
        index=2,
        open_time=candles[2].open_time,
        broken_level=105.0,
        direction=PatternDirection.BULLISH,
    )
    blocks = detect_order_blocks(candles, [event])

    assert len(blocks) == 1
    ob = blocks[0]
    assert ob.index == 0
    assert ob.high == 106.0
    assert ob.low == 99.0
    assert ob.direction == PatternDirection.BULLISH
    assert not ob.mitigated
    assert not ob.is_breaker


def test_update_mitigation_status_flips_to_breaker() -> None:
    candles = _order_block_candles()
    event = StructureEvent(
        type=StructureEventType.BOS,
        index=2,
        open_time=candles[2].open_time,
        broken_level=105.0,
        direction=PatternDirection.BULLISH,
    )
    blocks = detect_order_blocks(candles, [event])
    updated = update_mitigation_status(blocks, candles)

    assert len(updated) == 1
    ob = updated[0]
    assert ob.mitigated
    # candles[1].low (99) ja toca a zona [ob.low=99, ob.high=106] --
    # mitigado ja na primeira barra posterior ao order block.
    assert ob.mitigated_at_index == 1
    assert ob.is_breaker


def test_detect_order_blocks_no_opposite_candle_is_skipped() -> None:
    candles = [
        _candle(0, open_=100, high=101, low=99, close=101),  # alta
        _candle(1, open_=101, high=102, low=100, close=102),  # alta
    ]
    event = StructureEvent(
        type=StructureEventType.BOS,
        index=1,
        open_time=candles[1].open_time,
        broken_level=101.0,
        direction=PatternDirection.BULLISH,
    )
    assert detect_order_blocks(candles, [event]) == []


def test_detect_order_blocks_empty_inputs_never_raise() -> None:
    assert detect_order_blocks([], []) == []


# --- fair value gaps ---------------------------------------------------------


def test_detect_fair_value_gaps_bullish_exact_and_unfilled() -> None:
    candles = [
        _candle(0, open_=99, high=100, low=98, close=99.5),
        _candle(1, open_=100, high=102, low=99, close=101),
        _candle(2, open_=103, high=105, low=103, close=104),
    ]
    gaps = detect_fair_value_gaps(candles)

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.index == 2
    assert gap.direction == PatternDirection.BULLISH
    assert gap.gap_low == 100.0
    assert gap.gap_high == 103.0
    assert not gap.filled
    assert gap.filled_at_index is None


def test_detect_fair_value_gaps_filled_by_later_candle() -> None:
    candles = [
        _candle(0, open_=99, high=100, low=98, close=99.5),
        _candle(1, open_=100, high=102, low=99, close=101),
        _candle(2, open_=103, high=105, low=103, close=104),
        _candle(3, open_=104, high=104, low=101, close=101.5),  # volta a sobrepor o gap
    ]
    gaps = detect_fair_value_gaps(candles)

    assert len(gaps) == 1
    assert gaps[0].filled
    assert gaps[0].filled_at_index == 3


def test_detect_fair_value_gaps_bearish() -> None:
    candles = [
        _candle(0, open_=105, high=106, low=104, close=105.5),
        _candle(1, open_=104, high=105, low=101, close=102),
        _candle(2, open_=100, high=101, low=99, close=100),
    ]
    gaps = detect_fair_value_gaps(candles)

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.direction == PatternDirection.BEARISH
    assert gap.gap_low == 101.0
    assert gap.gap_high == 104.0


def test_detect_fair_value_gaps_empty_never_raises() -> None:
    assert detect_fair_value_gaps([]) == []


# --- equal highs/lows --------------------------------------------------------


def test_detect_equal_highs_lows_merges_only_when_two_or_more() -> None:
    swings = [
        _swing(1, 100.0, SwingKind.HIGH),
        _swing(2, 100.02, SwingKind.HIGH),
        _swing(3, 90.0, SwingKind.HIGH),  # fora de tolerancia -- solitario
        _swing(4, 50.0, SwingKind.LOW),  # solitario
    ]
    levels = detect_equal_highs_lows(swings, tolerance_pct=0.05)

    assert len(levels) == 1
    assert levels[0].kind == "EQUAL_HIGH"
    assert levels[0].indices == [1, 2]
    assert levels[0].price == pytest.approx((100.0 + 100.02) / 2)


def test_detect_equal_highs_lows_empty_never_raises() -> None:
    assert detect_equal_highs_lows([]) == []


# --- liquidity sweeps ---------------------------------------------------------


def test_detect_liquidity_sweeps_bearish_sweep_with_reversal() -> None:
    level = SRLevel(price=110.0, kind="RESISTANCE", touches=2, first_index=0, last_index=1)
    candles = [
        _candle(0, open_=108, high=112, low=108, close=109),  # varre acima de 110, fecha abaixo
        _candle(1, open_=109, high=109, low=105, close=106),  # confirma reversao (fecha mais baixo)
    ]
    sweeps = detect_liquidity_sweeps(candles, [level])

    assert len(sweeps) == 1
    sweep = sweeps[0]
    assert sweep.kind == LiquidityEventKind.SWEEP
    assert sweep.direction == PatternDirection.BEARISH
    assert sweep.swept_price == 110.0
    assert sweep.reversal_confirmed


def test_detect_liquidity_sweeps_genuine_breakout_is_not_a_sweep() -> None:
    level = SRLevel(price=110.0, kind="RESISTANCE", touches=2, first_index=0, last_index=1)
    candles = [
        _candle(0, open_=108, high=112, low=109, close=111)
    ]  # fecha ACIMA -- rompimento real
    assert detect_liquidity_sweeps(candles, [level]) == []


def test_detect_liquidity_sweeps_spring_and_upthrust_with_range_boundaries() -> None:
    support = SRLevel(price=100.0, kind="SUPPORT", touches=2, first_index=0, last_index=1)
    candles = [_candle(0, open_=101, high=102, low=98, close=101)]
    sweeps = detect_liquidity_sweeps(candles, [support], range_boundaries=(100.0, 150.0))
    assert len(sweeps) == 1
    assert sweeps[0].kind == LiquidityEventKind.SPRING
    assert sweeps[0].direction == PatternDirection.BULLISH


def test_detect_liquidity_sweeps_empty_never_raises() -> None:
    assert detect_liquidity_sweeps([], []) == []


# --- premium/discount/OTE -----------------------------------------------------


def test_compute_premium_discount_hand_computed() -> None:
    swing_high = _swing(5, 110.0, SwingKind.HIGH)
    swing_low = _swing(2, 100.0, SwingKind.LOW)

    zone = compute_premium_discount(swing_high, swing_low)

    assert zone.range_high == 110.0
    assert zone.range_low == 100.0
    assert zone.equilibrium == pytest.approx(105.0)
    assert zone.premium_zone == pytest.approx((105.0, 110.0))
    assert zone.discount_zone == pytest.approx((100.0, 105.0))
    assert zone.ote_zone[0] == pytest.approx(110.0 - 0.786 * 10)
    assert zone.ote_zone[1] == pytest.approx(110.0 - 0.618 * 10)


# --- fakey / false breakout ----------------------------------------------------


def _inside_bar_pattern(index: int) -> CandlestickPattern:
    return CandlestickPattern(
        name=PatternName.INSIDE_BAR,
        direction=PatternDirection.NEUTRAL,
        index=index,
        open_time=_START + timedelta(minutes=index),
        strength=0.5,
        description="",
    )


def test_detect_fakey_bearish() -> None:
    candles = [
        _candle(0, open_=105, high=110, low=100, close=104),  # mother bar
        _candle(1, open_=104, high=108, low=102, close=105),  # inside bar
        _candle(
            2, open_=105, high=109, low=104, close=107
        ),  # rompe acima (108) e fecha abaixo (107<108)
    ]
    inside_bars = [_inside_bar_pattern(1)]
    fakeys = detect_fakey(candles, inside_bars)

    assert len(fakeys) == 1
    assert fakeys[0].name == PatternName.FAKEY
    assert fakeys[0].direction == PatternDirection.BEARISH
    assert fakeys[0].index == 2


def test_detect_fakey_bullish() -> None:
    candles = [
        _candle(0, open_=104, high=110, low=100, close=105),  # mother bar
        _candle(1, open_=105, high=108, low=102, close=104),  # inside bar
        _candle(
            2, open_=103, high=105, low=101, close=103
        ),  # rompe abaixo (102) e fecha acima (103>102)
    ]
    inside_bars = [_inside_bar_pattern(1)]
    fakeys = detect_fakey(candles, inside_bars)

    assert len(fakeys) == 1
    assert fakeys[0].direction == PatternDirection.BULLISH


def test_detect_fakey_ignores_non_inside_bar_patterns() -> None:
    candles = [
        _candle(0, open_=105, high=110, low=100, close=104),
        _candle(1, open_=104, high=108, low=102, close=105),
    ]
    not_inside = CandlestickPattern(
        name=PatternName.DOJI,
        direction=PatternDirection.NEUTRAL,
        index=1,
        open_time=candles[1].open_time,
        strength=0.5,
        description="",
    )
    assert detect_fakey(candles, [not_inside]) == []


def test_detect_fakey_empty_never_raises() -> None:
    assert detect_fakey([], []) == []


def test_detect_false_breakout_resistance() -> None:
    level = SRLevel(price=100.0, kind="RESISTANCE", touches=2, first_index=0, last_index=1)
    candles = [_candle(0, open_=99, high=102, low=98, close=99.5)]
    patterns = detect_false_breakout(candles, [level])

    assert len(patterns) == 1
    assert patterns[0].name == PatternName.FALSE_BREAKOUT
    assert patterns[0].direction == PatternDirection.BEARISH


def test_detect_false_breakout_support() -> None:
    level = SRLevel(price=100.0, kind="SUPPORT", touches=2, first_index=0, last_index=1)
    candles = [_candle(0, open_=101, high=102, low=98, close=100.5)]
    patterns = detect_false_breakout(candles, [level])

    assert len(patterns) == 1
    assert patterns[0].direction == PatternDirection.BULLISH


def test_detect_false_breakout_empty_never_raises() -> None:
    assert detect_false_breakout([], []) == []
