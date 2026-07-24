"""Zonas de Smart Money Concepts e padroes de price action dependentes de
nivel (Fase 18.4).

Depende de `app.market.structure` (swings/eventos/niveis de S/R, Fase 18.3)
e `app.market.price_action` (padroes de candle, Fase 18.2) — nunca o
contrario, mantendo a ordem de estagios.

Breaker Block e Mitigation Block sao tratados como ESTADOS do mesmo
`OrderBlock` (`mitigated`/`is_breaker`), nao tres dataclasses paralelas: um
order block "mitigado" e um que ja foi revisitado pelo preco; um "breaker"
e um order block mitigado cujo range foi TOTALMENTE rompido depois,
invertendo a polaridade (suporte falho vira resistencia, e vice-versa).
Colapsar os tres conceitos num objeto so evita manter tres detectores em
lockstep para a mesma familia de fenomeno."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from app.market.features import CandleFeatureLike
from app.market.price_action import CandlestickPattern, PatternDirection, PatternName
from app.market.structure import SRLevel, StructureEvent, SwingKind, SwingPoint


@dataclass(frozen=True, slots=True)
class OrderBlock:
    index: int
    open_time: datetime
    direction: PatternDirection
    high: float
    low: float
    mitigated: bool
    mitigated_at_index: int | None
    is_breaker: bool


@dataclass(frozen=True, slots=True)
class FairValueGap:
    index: int
    open_time: datetime
    direction: PatternDirection
    gap_high: float
    gap_low: float
    filled: bool
    filled_at_index: int | None


@dataclass(frozen=True, slots=True)
class EqualLevel:
    price: float
    kind: Literal["EQUAL_HIGH", "EQUAL_LOW"]
    indices: list[int]


class LiquidityEventKind(enum.StrEnum):
    SWEEP = "SWEEP"
    SPRING = "SPRING"
    UPTHRUST = "UPTHRUST"


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    kind: LiquidityEventKind
    index: int
    open_time: datetime
    swept_price: float
    direction: PatternDirection
    reversal_confirmed: bool


@dataclass(frozen=True, slots=True)
class PremiumDiscountZone:
    range_high: float
    range_low: float
    equilibrium: float
    premium_zone: tuple[float, float]
    discount_zone: tuple[float, float]
    ote_zone: tuple[float, float]


def detect_order_blocks(
    candles: Sequence[CandleFeatureLike], events: Sequence[StructureEvent]
) -> list[OrderBlock]:
    """Para cada evento de estrutura, o order block e o ULTIMO candle de
    cor oposta antes do rompimento (a "ultima ordem" do lado perdedor
    antes do movimento impulsivo). Sem candle oposto anterior (serie toda
    de uma cor) — nenhum order block para esse evento, nunca inventado."""
    blocks: list[OrderBlock] = []
    for event in events:
        want_bearish_candle = event.direction == PatternDirection.BULLISH
        idx = event.index - 1
        found_index: int | None = None
        while idx >= 0:
            c = candles[idx]
            open_, close = float(c.open), float(c.close)
            is_bearish = close < open_
            is_bullish = close > open_
            if want_bearish_candle and is_bearish:
                found_index = idx
                break
            if not want_bearish_candle and is_bullish:
                found_index = idx
                break
            idx -= 1
        if found_index is None:
            continue
        candle = candles[found_index]
        blocks.append(
            OrderBlock(
                index=found_index,
                open_time=candle.open_time,
                direction=event.direction,
                high=float(candle.high),
                low=float(candle.low),
                mitigated=False,
                mitigated_at_index=None,
                is_breaker=False,
            )
        )
    return blocks


def update_mitigation_status(
    order_blocks: Sequence[OrderBlock], candles: Sequence[CandleFeatureLike]
) -> list[OrderBlock]:
    """Recalcula `mitigated`/`is_breaker` varrendo os candles POSTERIORES a
    cada order block. Bullish OB (zona de suporte): mitigado quando o preco
    volta a operar dentro da zona (`low <= ob.high`); vira breaker quando,
    depois de mitigado, um fechamento rompe totalmente abaixo de `ob.low`
    (suporte falho, agora resistencia). Espelho para Bearish OB."""
    updated: list[OrderBlock] = []
    for ob in order_blocks:
        mitigated = ob.mitigated
        mitigated_at = ob.mitigated_at_index
        is_breaker = ob.is_breaker

        for j in range(ob.index + 1, len(candles)):
            c = candles[j]
            high, low, close = float(c.high), float(c.low), float(c.close)
            if ob.direction == PatternDirection.BULLISH:
                if not mitigated and low <= ob.high:
                    mitigated = True
                    mitigated_at = j
                if mitigated and close < ob.low:
                    is_breaker = True
            else:
                if not mitigated and high >= ob.low:
                    mitigated = True
                    mitigated_at = j
                if mitigated and close > ob.high:
                    is_breaker = True

        updated.append(
            replace(ob, mitigated=mitigated, mitigated_at_index=mitigated_at, is_breaker=is_breaker)
        )
    return updated


def _ranges_overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    return low_a <= high_b and high_a >= low_b


def detect_fair_value_gaps(candles: Sequence[CandleFeatureLike]) -> list[FairValueGap]:
    """FVG classico de 3 candles: gap entre a maxima de `candles[i-2]` e a
    minima de `candles[i]` (alta), ou o espelho (baixa) — `candles[i-1]` e
    o candle impulsivo que "pula" a zona. `filled` fica `True` na primeira
    barra POSTERIOR cujo range volta a sobrepor a zona do gap."""
    gaps: list[FairValueGap] = []
    for i in range(2, len(candles)):
        prev2, cur = candles[i - 2], candles[i]
        prev2_high, prev2_low = float(prev2.high), float(prev2.low)
        cur_high, cur_low = float(cur.high), float(cur.low)

        if prev2_high < cur_low:
            direction = PatternDirection.BULLISH
            gap_low, gap_high = prev2_high, cur_low
        elif prev2_low > cur_high:
            direction = PatternDirection.BEARISH
            gap_low, gap_high = cur_high, prev2_low
        else:
            continue

        filled = False
        filled_at: int | None = None
        for j in range(i + 1, len(candles)):
            c = candles[j]
            if _ranges_overlap(float(c.low), float(c.high), gap_low, gap_high):
                filled = True
                filled_at = j
                break

        gaps.append(
            FairValueGap(
                index=i,
                open_time=cur.open_time,
                direction=direction,
                gap_high=gap_high,
                gap_low=gap_low,
                filled=filled,
                filled_at_index=filled_at,
            )
        )
    return gaps


def detect_equal_highs_lows(
    swings: Sequence[SwingPoint], *, tolerance_pct: float = 0.05
) -> list[EqualLevel]:
    """Agrupa swings do mesmo tipo com precos dentro de `tolerance_pct`% —
    so vira `EqualLevel` quando pelo menos 2 swings se agrupam (um swing
    solitario nao e "igual" a nada)."""
    levels: list[EqualLevel] = []
    for kind, level_kind in ((SwingKind.HIGH, "EQUAL_HIGH"), (SwingKind.LOW, "EQUAL_LOW")):
        same_kind = sorted((s for s in swings if s.kind == kind), key=lambda s: s.price)
        cluster: list[SwingPoint] = []
        for swing in same_kind:
            if cluster:
                cluster_avg = sum(s.price for s in cluster) / len(cluster)
                tolerance = abs(cluster_avg) * tolerance_pct / 100
                if abs(swing.price - cluster_avg) > tolerance:
                    if len(cluster) >= 2:
                        levels.append(_cluster_to_equal_level(cluster, level_kind))  # type: ignore[arg-type]
                    cluster = []
            cluster.append(swing)
        if len(cluster) >= 2:
            levels.append(_cluster_to_equal_level(cluster, level_kind))  # type: ignore[arg-type]
    return levels


def _cluster_to_equal_level(
    cluster: list[SwingPoint], kind: Literal["EQUAL_HIGH", "EQUAL_LOW"]
) -> EqualLevel:
    return EqualLevel(
        price=sum(s.price for s in cluster) / len(cluster),
        kind=kind,
        indices=sorted(s.index for s in cluster),
    )


def _breaks_and_rejects(
    candle: CandleFeatureLike, level_price: float, side: Literal["above", "below"]
) -> bool:
    high, low, close = float(candle.high), float(candle.low), float(candle.close)
    if side == "above":
        return high > level_price and close < level_price
    return low < level_price and close > level_price


def _near(price: float, boundary: float, tolerance_pct: float = 0.05) -> bool:
    tolerance = abs(boundary) * tolerance_pct / 100
    return abs(price - boundary) <= tolerance


def detect_liquidity_sweeps(
    candles: Sequence[CandleFeatureLike],
    levels: Sequence[SRLevel] | Sequence[EqualLevel],
    *,
    range_boundaries: tuple[float, float] | None = None,
) -> list[LiquiditySweep]:
    """Varredura de liquidez: pavio alem de um nivel de resistencia/topo
    igual seguido de fechamento de volta abaixo (venda), ou o espelho
    (compra). Com `range_boundaries=(low, high)` informado, uma varredura
    perto do limite inferior vira SPRING e perto do superior vira
    UPTHRUST — caso contrario, SWEEP generico."""
    results: list[LiquiditySweep] = []
    for level in levels:
        for i, candle in enumerate(candles):
            if level.kind in ("RESISTANCE", "EQUAL_HIGH") and _breaks_and_rejects(
                candle, level.price, "above"
            ):
                kind = LiquidityEventKind.SWEEP
                if range_boundaries is not None and _near(level.price, range_boundaries[1]):
                    kind = LiquidityEventKind.UPTHRUST
                reversal_confirmed = i + 1 < len(candles) and float(candles[i + 1].close) < float(
                    candle.close
                )
                results.append(
                    LiquiditySweep(
                        kind=kind,
                        index=i,
                        open_time=candle.open_time,
                        swept_price=level.price,
                        direction=PatternDirection.BEARISH,
                        reversal_confirmed=reversal_confirmed,
                    )
                )
            elif level.kind in ("SUPPORT", "EQUAL_LOW") and _breaks_and_rejects(
                candle, level.price, "below"
            ):
                kind = LiquidityEventKind.SWEEP
                if range_boundaries is not None and _near(level.price, range_boundaries[0]):
                    kind = LiquidityEventKind.SPRING
                reversal_confirmed = i + 1 < len(candles) and float(candles[i + 1].close) > float(
                    candle.close
                )
                results.append(
                    LiquiditySweep(
                        kind=kind,
                        index=i,
                        open_time=candle.open_time,
                        swept_price=level.price,
                        direction=PatternDirection.BULLISH,
                        reversal_confirmed=reversal_confirmed,
                    )
                )
    return results


def compute_premium_discount(swing_high: SwingPoint, swing_low: SwingPoint) -> PremiumDiscountZone:
    """Zona de equilibrio/premium/discount + OTE (61.8%-78.6%) do range
    entre `swing_high` e `swing_low`. OTE e medida a partir do topo do
    range para baixo (convencao padrao de retracao Fibonacci), independente
    de qual dos dois swings veio primeiro cronologicamente."""
    range_high, range_low = swing_high.price, swing_low.price
    equilibrium = (range_high + range_low) / 2
    span = range_high - range_low
    ote_low = range_high - 0.786 * span
    ote_high = range_high - 0.618 * span
    return PremiumDiscountZone(
        range_high=range_high,
        range_low=range_low,
        equilibrium=equilibrium,
        premium_zone=(equilibrium, range_high),
        discount_zone=(range_low, equilibrium),
        ote_zone=(ote_low, ote_high),
    )


def detect_fakey(
    candles: Sequence[CandleFeatureLike], inside_bars: Sequence[CandlestickPattern]
) -> list[CandlestickPattern]:
    """Fakey: inside bar (`app.market.price_action`) seguido de um candle
    que rompe a maxima/minima do inside bar mas FECHA de volta dentro do
    seu range — rompimento falso."""
    results: list[CandlestickPattern] = []
    for pattern in inside_bars:
        if pattern.name != PatternName.INSIDE_BAR:
            continue
        i = pattern.index
        if i + 1 >= len(candles):
            continue
        inside_high, inside_low = float(candles[i].high), float(candles[i].low)
        nxt = candles[i + 1]
        next_high, next_low, next_close = float(nxt.high), float(nxt.low), float(nxt.close)

        if next_high > inside_high and next_close < inside_high:
            results.append(
                CandlestickPattern(
                    name=PatternName.FAKEY,
                    direction=PatternDirection.BEARISH,
                    index=i + 1,
                    open_time=nxt.open_time,
                    strength=0.5,
                    description="Fakey de baixa: rompimento falso acima do inside bar, fechamento de volta abaixo.",
                )
            )
        elif next_low < inside_low and next_close > inside_low:
            results.append(
                CandlestickPattern(
                    name=PatternName.FAKEY,
                    direction=PatternDirection.BULLISH,
                    index=i + 1,
                    open_time=nxt.open_time,
                    strength=0.5,
                    description="Fakey de alta: rompimento falso abaixo do inside bar, fechamento de volta acima.",
                )
            )
    return results


def detect_false_breakout(
    candles: Sequence[CandleFeatureLike], levels: Sequence[SRLevel]
) -> list[CandlestickPattern]:
    """Rompimento falso de um nivel de S/R: pavio alem do nivel, fechamento
    de volta para o lado original."""
    results: list[CandlestickPattern] = []
    for level in levels:
        for i, candle in enumerate(candles):
            if level.kind == "RESISTANCE" and _breaks_and_rejects(candle, level.price, "above"):
                results.append(
                    CandlestickPattern(
                        name=PatternName.FALSE_BREAKOUT,
                        direction=PatternDirection.BEARISH,
                        index=i,
                        open_time=candle.open_time,
                        strength=0.5,
                        description=f"Rompimento falso da resistencia {level.price:.5f}.",
                    )
                )
            elif level.kind == "SUPPORT" and _breaks_and_rejects(candle, level.price, "below"):
                results.append(
                    CandlestickPattern(
                        name=PatternName.FALSE_BREAKOUT,
                        direction=PatternDirection.BULLISH,
                        index=i,
                        open_time=candle.open_time,
                        strength=0.5,
                        description=f"Rompimento falso do suporte {level.price:.5f}.",
                    )
                )
    return results


__all__ = [
    "OrderBlock",
    "FairValueGap",
    "EqualLevel",
    "LiquidityEventKind",
    "LiquiditySweep",
    "PremiumDiscountZone",
    "detect_order_blocks",
    "update_mitigation_status",
    "detect_fair_value_gaps",
    "detect_equal_highs_lows",
    "detect_liquidity_sweeps",
    "compute_premium_discount",
    "detect_fakey",
    "detect_false_breakout",
]
