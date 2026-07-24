"""Estrutura de mercado: swings, HH/HL/LH/LL, BOS/CHoCH/MSS e
suporte/resistencia (Fase 18.3).

Leak-safety de swings (importante, mesma categoria de atraso ja documentada
para `ema_21_slope` em `FEATURE_CATALOG` — nao e bug): um fractal no indice
`i` so e CONHECIDO depois que `right_bars` candles futuras confirmam que
nenhuma fez um preco mais extremo. `SwingPoint.confirmed_at_index` deixa
isso explicito. Quem consome um `SwingPoint`/`StructureLabel`/
`StructureEvent` nunca deve usar um cujo indice de confirmacao seja maior
que a ultima barra fechada disponivel — `detect_structure_events` ja
respeita isso (so emite eventos em barras onde o rompimento ja e visivel
em dados fechados, nunca espiando a frente).

BOS/CHoCH/MSS: terminologia ambigua na literatura de Smart Money Concepts;
regra adotada explicitamente aqui (documentada tambem no plano da Fase 18):
- BOS (Break of Structure): rompimento a FAVOR da tendencia vigente
  (continuacao).
- CHoCH (Change of Character): PRIMEIRO rompimento CONTRARIO a tendencia
  vigente, ainda nao confirmado — pode ser reversao real ou apenas ruido.
- MSS (Market Structure Shift): um CHoCH que e CONFIRMADO por um segundo
  rompimento na MESMA nova direcao — dois estagios, reduz alarme falso de
  reversao (vies conservador, consistente com o resto do projeto)."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np

from app.market.features import CandleFeatureLike
from app.market.price_action import PatternDirection


class SwingKind(enum.StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    index: int
    open_time: datetime
    price: float
    kind: SwingKind
    confirmed_at_index: int


class TrendStructureLabel(enum.StrEnum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"


@dataclass(frozen=True, slots=True)
class StructureLabel:
    swing: SwingPoint
    label: TrendStructureLabel


class StructureEventType(enum.StrEnum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    MSS = "MSS"


@dataclass(frozen=True, slots=True)
class StructureEvent:
    type: StructureEventType
    index: int
    open_time: datetime
    broken_level: float
    direction: PatternDirection


@dataclass(frozen=True, slots=True)
class PivotLevels:
    pivot: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


@dataclass(frozen=True, slots=True)
class SRLevel:
    price: float
    kind: Literal["SUPPORT", "RESISTANCE"]
    touches: int
    first_index: int
    last_index: int


@dataclass(frozen=True, slots=True)
class Trendline:
    slope: float
    intercept: float

    def value_at(self, x: int) -> float:
        return self.slope * x + self.intercept


@dataclass(frozen=True, slots=True)
class Channel:
    kind: Literal["ASCENDING", "DESCENDING", "HORIZONTAL"]
    upper: Trendline
    lower: Trendline


def detect_swings(
    candles: Sequence[CandleFeatureLike], *, left_bars: int = 2, right_bars: int = 2
) -> list[SwingPoint]:
    """Fractal classico: `candles[i]` e um topo (fundo) se sua maxima
    (minima) e estritamente maior (menor) que todas as `left_bars`
    anteriores e `right_bars` seguintes — evita reportar o mesmo patamar
    duas vezes num trecho plano."""
    n = len(candles)
    window = left_bars + right_bars
    if n <= window:
        return []

    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]

    swings: list[SwingPoint] = []
    for i in range(left_bars, n - right_bars):
        neighborhood_highs = highs[i - left_bars : i] + highs[i + 1 : i + right_bars + 1]
        if highs[i] > max(neighborhood_highs):
            swings.append(
                SwingPoint(
                    index=i,
                    open_time=candles[i].open_time,
                    price=highs[i],
                    kind=SwingKind.HIGH,
                    confirmed_at_index=i + right_bars,
                )
            )
            continue
        neighborhood_lows = lows[i - left_bars : i] + lows[i + 1 : i + right_bars + 1]
        if lows[i] < min(neighborhood_lows):
            swings.append(
                SwingPoint(
                    index=i,
                    open_time=candles[i].open_time,
                    price=lows[i],
                    kind=SwingKind.LOW,
                    confirmed_at_index=i + right_bars,
                )
            )
    return swings


def label_swing_structure(swings: Sequence[SwingPoint]) -> list[StructureLabel]:
    """HH/HL/LH/LL comparando cada swing com o swing ANTERIOR do MESMO
    tipo (ordem cronologica por `index`). O primeiro swing de cada tipo
    nao tem referencia anterior — nao entra na saida (nao ha rotulo
    valido para ele ainda)."""
    ordered = sorted(swings, key=lambda s: s.index)
    labels: list[StructureLabel] = []
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None

    for swing in ordered:
        if swing.kind == SwingKind.HIGH:
            if last_high is not None:
                label = (
                    TrendStructureLabel.HH
                    if swing.price > last_high.price
                    else TrendStructureLabel.LH
                )
                labels.append(StructureLabel(swing=swing, label=label))
            last_high = swing
        else:
            if last_low is not None:
                label = (
                    TrendStructureLabel.HL
                    if swing.price > last_low.price
                    else TrendStructureLabel.LL
                )
                labels.append(StructureLabel(swing=swing, label=label))
            last_low = swing

    return labels


class _Trend(enum.StrEnum):
    UP = "UP"
    DOWN = "DOWN"


def detect_structure_events(
    candles: Sequence[CandleFeatureLike], labels: Sequence[StructureLabel]
) -> list[StructureEvent]:
    """Varre os fechamentos em ordem cronologica procurando rompimentos do
    swing de resistencia/suporte mais recente CONFIRMADO ate aquele
    ponto — nunca usa um swing cujo `confirmed_at_index` seja posterior a
    barra que esta sendo avaliada."""
    swings_by_confirmation: dict[int, list[SwingPoint]] = {}
    for label in labels:
        swings_by_confirmation.setdefault(label.swing.confirmed_at_index, []).append(label.swing)

    active_resistance: SwingPoint | None = None
    active_support: SwingPoint | None = None
    resistance_broken = True
    support_broken = True

    trend: _Trend | None = None
    pending_reversal: PatternDirection | None = None
    events: list[StructureEvent] = []

    for i, candle in enumerate(candles):
        newly_confirmed = swings_by_confirmation.get(i, [])
        for swing in newly_confirmed:
            if swing.kind == SwingKind.HIGH:
                active_resistance = swing
                resistance_broken = False
            else:
                active_support = swing
                support_broken = False

        close = float(candle.close)

        if (
            active_resistance is not None
            and not resistance_broken
            and close > active_resistance.price
        ):
            resistance_broken = True
            if trend is None or trend == _Trend.UP:
                trend = _Trend.UP
                pending_reversal = None
                event_type = StructureEventType.BOS
            elif pending_reversal == PatternDirection.BULLISH:
                trend = _Trend.UP
                pending_reversal = None
                event_type = StructureEventType.MSS
            else:
                pending_reversal = PatternDirection.BULLISH
                event_type = StructureEventType.CHOCH
            events.append(
                StructureEvent(
                    type=event_type,
                    index=i,
                    open_time=candle.open_time,
                    broken_level=active_resistance.price,
                    direction=PatternDirection.BULLISH,
                )
            )

        if active_support is not None and not support_broken and close < active_support.price:
            support_broken = True
            if trend is None or trend == _Trend.DOWN:
                trend = _Trend.DOWN
                pending_reversal = None
                event_type = StructureEventType.BOS
            elif pending_reversal == PatternDirection.BEARISH:
                trend = _Trend.DOWN
                pending_reversal = None
                event_type = StructureEventType.MSS
            else:
                pending_reversal = PatternDirection.BEARISH
                event_type = StructureEventType.CHOCH
            events.append(
                StructureEvent(
                    type=event_type,
                    index=i,
                    open_time=candle.open_time,
                    broken_level=active_support.price,
                    direction=PatternDirection.BEARISH,
                )
            )

    return events


def pivot_points(*, prev_high: float, prev_low: float, prev_close: float) -> PivotLevels:
    """Pivots classicos de floor trader — formula padrao, nao inventada
    para este projeto."""
    pivot = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * pivot - prev_low
    s1 = 2 * pivot - prev_high
    r2 = pivot + (prev_high - prev_low)
    s2 = pivot - (prev_high - prev_low)
    r3 = prev_high + 2 * (pivot - prev_low)
    s3 = prev_low - 2 * (prev_high - pivot)
    return PivotLevels(pivot=pivot, r1=r1, r2=r2, r3=r3, s1=s1, s2=s2, s3=s3)


def cluster_swing_levels(
    swings: Sequence[SwingPoint], *, tolerance_pct: float = 0.1
) -> list[SRLevel]:
    """Agrupa swings do MESMO tipo cujos precos ficam dentro de
    `tolerance_pct`% um do outro (relativo ao preco) num unico nivel de
    S/R — swings de topo viram RESISTANCE, de fundo viram SUPPORT."""
    levels: list[SRLevel] = []
    for kind, sr_kind in ((SwingKind.HIGH, "RESISTANCE"), (SwingKind.LOW, "SUPPORT")):
        same_kind = sorted((s for s in swings if s.kind == kind), key=lambda s: s.price)
        cluster: list[SwingPoint] = []
        for swing in same_kind:
            if cluster:
                cluster_avg = sum(s.price for s in cluster) / len(cluster)
                tolerance = abs(cluster_avg) * tolerance_pct / 100
                if abs(swing.price - cluster_avg) > tolerance:
                    levels.append(_cluster_to_level(cluster, sr_kind))  # type: ignore[arg-type]
                    cluster = []
            cluster.append(swing)
        if cluster:
            levels.append(_cluster_to_level(cluster, sr_kind))  # type: ignore[arg-type]
    return levels


def _cluster_to_level(cluster: list[SwingPoint], kind: Literal["SUPPORT", "RESISTANCE"]) -> SRLevel:
    indices = [s.index for s in cluster]
    return SRLevel(
        price=sum(s.price for s in cluster) / len(cluster),
        kind=kind,
        touches=len(cluster),
        first_index=min(indices),
        last_index=max(indices),
    )


def fit_trendline(points: Sequence[tuple[int, float]]) -> Trendline:
    """Regressao linear simples (minimos quadrados) sobre `(indice,
    preco)`. Exige pelo menos 2 pontos."""
    if len(points) < 2:
        raise ValueError("fit_trendline exige pelo menos 2 pontos.")
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(xs, ys, 1)
    return Trendline(slope=float(slope), intercept=float(intercept))


def detect_channel(swings: Sequence[SwingPoint], *, min_points: int = 3) -> Channel | None:
    """Ajusta uma reta aos topos e outra aos fundos; `None` se nao houver
    pelo menos `min_points` de cada tipo (dados insuficientes, nunca um
    canal inventado)."""
    highs = sorted((s for s in swings if s.kind == SwingKind.HIGH), key=lambda s: s.index)
    lows = sorted((s for s in swings if s.kind == SwingKind.LOW), key=lambda s: s.index)
    if len(highs) < min_points or len(lows) < min_points:
        return None

    upper = fit_trendline([(s.index, s.price) for s in highs])
    lower = fit_trendline([(s.index, s.price) for s in lows])

    combined_slope = (upper.slope + lower.slope) / 2
    epsilon = 1e-9
    if combined_slope > epsilon:
        kind: Literal["ASCENDING", "DESCENDING", "HORIZONTAL"] = "ASCENDING"
    elif combined_slope < -epsilon:
        kind = "DESCENDING"
    else:
        kind = "HORIZONTAL"

    return Channel(kind=kind, upper=upper, lower=lower)
