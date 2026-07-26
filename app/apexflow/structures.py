"""Estruturas de grafico do Price Action Engine.

Complementa `app.market.price_action` (que reconhece PADROES DE CANDLE:
pin bar, engolfo, doji, inside/outside bar, fakey, falso rompimento) com as
ESTRUTURAS que o brief pede e que nao cabem em uma ou tres velas:

- **fundo duplo / topo duplo** — dois extremos no mesmo nivel, com um
  recuo relevante entre eles;
- **compressao / expansao** — amplitude recente encolhendo ou abrindo em
  relacao ao proprio historico;
- **acumulacao / distribuicao** — faixa estreita com volume ALTO, que
  distingue absorcao institucional de simples mercado parado (faixa estreita
  com volume baixo e so falta de interesse);
- **range** — faixa definida por toques repetidos nos dois lados;
- **micro pullback / micro tendencia** — as mesmas leituras nas ultimas
  poucas barras, para o timing.

Separado de `price_action.py` de proposito: aquele modulo tem contrato,
catalogo e testes proprios sobre padroes de candle, e nao deve ganhar
entradas com semantica diferente. Aqui a unidade de analise e o SWING e a
JANELA, nao a vela.

Modulo puro: recebe candles e features, devolve estruturas. Nenhuma
formula de indicador e recalculada — `atr_14` e `relative_volume_20` vem
prontas de `app.market.features`.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.market.features import CandleFeatureLike
from app.market.price_action import PatternDirection
from app.market.structure import SwingKind, SwingPoint, detect_swings


class StructureKind(enum.StrEnum):
    DOUBLE_TOP = "DOUBLE_TOP"
    DOUBLE_BOTTOM = "DOUBLE_BOTTOM"
    COMPRESSION = "COMPRESSION"
    EXPANSION = "EXPANSION"
    ACCUMULATION = "ACCUMULATION"
    DISTRIBUTION = "DISTRIBUTION"
    RANGE = "RANGE"
    MICRO_PULLBACK = "MICRO_PULLBACK"
    MICRO_TREND = "MICRO_TREND"


STRUCTURE_LABELS: dict[StructureKind, str] = {
    StructureKind.DOUBLE_TOP: "Topo duplo",
    StructureKind.DOUBLE_BOTTOM: "Fundo duplo",
    StructureKind.COMPRESSION: "Compressao",
    StructureKind.EXPANSION: "Expansao",
    StructureKind.ACCUMULATION: "Acumulacao",
    StructureKind.DISTRIBUTION: "Distribuicao",
    StructureKind.RANGE: "Range definido",
    StructureKind.MICRO_PULLBACK: "Micro pullback",
    StructureKind.MICRO_TREND: "Micro tendencia",
}

LEVEL_TOLERANCE_ATR = 0.35
"""Dois extremos contam como "o mesmo nivel" quando a diferenca entre eles
cabe nessa fracao do ATR. Em ATR, nao em pontos fixos: o mesmo criterio
serve para EURUSD e para XAUUSD."""

MIN_SEPARATION_BARS = 3
"""Dois topos colados sao uma consolidacao, nao um topo duplo."""

MIN_RETRACEMENT_ATR = 0.8
"""Sem um recuo relevante entre os dois extremos nao houve rejeicao."""

COMPRESSION_RATIO = 0.70
EXPANSION_RATIO = 1.40
RANGE_WINDOW = 20
RANGE_MAX_WIDTH_ATR = 2.5
RANGE_MIN_TOUCHES = 2
HIGH_VOLUME_RATIO = 1.15
MICRO_WINDOW = 5


@dataclass(frozen=True, slots=True)
class ChartStructure:
    kind: StructureKind
    direction: PatternDirection | None
    """Sentido que a estrutura favorece. `None` para estruturas neutras
    (compressao, expansao, range), que dizem "como", nao "para onde"."""

    index: int
    open_time: datetime
    description: str

    @property
    def label(self) -> str:
        return STRUCTURE_LABELS[self.kind]


def _atr(features: pd.DataFrame) -> float | None:
    if features.empty or "atr_14" not in features.columns:
        return None
    value = features["atr_14"].iloc[-1]
    return None if pd.isna(value) or float(value) <= 0 else float(value)


def _relative_volume(features: pd.DataFrame, *, window: int) -> float | None:
    if features.empty or "relative_volume_20" not in features.columns:
        return None
    series = features["relative_volume_20"].tail(window).dropna()
    return float(series.mean()) if not series.empty else None


def _double_extremes(
    candles: Sequence[CandleFeatureLike],
    swings: Sequence[SwingPoint],
    *,
    atr: float,
) -> list[ChartStructure]:
    """Topo/fundo duplo: dois extremos no mesmo nivel com recuo entre eles."""
    results: list[ChartStructure] = []
    tolerance = atr * LEVEL_TOLERANCE_ATR

    for kind, swing_kind, structure, direction in (
        ("HIGH", SwingKind.HIGH, StructureKind.DOUBLE_TOP, PatternDirection.BEARISH),
        ("LOW", SwingKind.LOW, StructureKind.DOUBLE_BOTTOM, PatternDirection.BULLISH),
    ):
        del kind
        points = sorted(
            (swing for swing in swings if swing.kind == swing_kind),
            key=lambda swing: swing.index,
        )
        for first, second in zip(points, points[1:], strict=False):
            if second.index - first.index < MIN_SEPARATION_BARS:
                continue
            if abs(second.price - first.price) > tolerance:
                continue

            between = candles[first.index : second.index + 1]
            if not between:
                continue
            if swing_kind == SwingKind.HIGH:
                retracement = max(first.price, second.price) - min(
                    float(candle.low) for candle in between
                )
            else:
                retracement = max(
                    float(candle.high) for candle in between
                ) - min(first.price, second.price)
            if retracement < atr * MIN_RETRACEMENT_ATR:
                continue

            results.append(
                ChartStructure(
                    kind=structure,
                    direction=direction,
                    index=second.index,
                    open_time=candles[second.index].open_time,
                    description=(
                        f"{STRUCTURE_LABELS[structure]} em {second.price:.5f} "
                        f"(recuo de {retracement / atr:.2f} ATR entre os extremos)."
                    ),
                )
            )
    return results


def _volatility_structures(
    candles: Sequence[CandleFeatureLike], features: pd.DataFrame, *, window: int = RANGE_WINDOW
) -> list[ChartStructure]:
    """Compressao/expansao e acumulacao/distribuicao.

    A distincao entre faixa estreita POR FALTA DE INTERESSE e faixa estreita
    COM ABSORCAO e o volume relativo: a primeira e apenas compressao, a
    segunda e acumulacao (perto do fundo) ou distribuicao (perto do topo).
    """
    results: list[ChartStructure] = []
    if len(candles) < window * 2:
        return results

    amplitudes = [float(candle.high) - float(candle.low) for candle in candles]
    recent = sum(amplitudes[-window:]) / window
    older = sum(amplitudes[-window * 2 : -window]) / window
    if older <= 0:
        return results

    ratio = recent / older
    last_index = len(candles) - 1
    last_time = candles[last_index].open_time
    relative_volume = _relative_volume(features, window=window)

    if ratio <= COMPRESSION_RATIO:
        results.append(
            ChartStructure(
                kind=StructureKind.COMPRESSION,
                direction=None,
                index=last_index,
                open_time=last_time,
                description=(
                    f"Amplitude media {ratio:.2f}x a das {window} barras anteriores — "
                    "mercado comprimindo."
                ),
            )
        )
        if relative_volume is not None and relative_volume >= HIGH_VOLUME_RATIO:
            window_candles = candles[-window:]
            highest = max(float(candle.high) for candle in window_candles)
            lowest = min(float(candle.low) for candle in window_candles)
            close = float(candles[-1].close)
            span = highest - lowest
            position = (close - lowest) / span if span > 0 else 0.5
            # Faixa estreita com volume alto: alguem esta absorvendo. Perto
            # do fundo da faixa isso e acumulacao; perto do topo, distribuicao.
            if position <= 0.5:
                kind, direction = StructureKind.ACCUMULATION, PatternDirection.BULLISH
            else:
                kind, direction = StructureKind.DISTRIBUTION, PatternDirection.BEARISH
            results.append(
                ChartStructure(
                    kind=kind,
                    direction=direction,
                    index=last_index,
                    open_time=last_time,
                    description=(
                        f"{STRUCTURE_LABELS[kind]}: faixa estreita com volume "
                        f"{relative_volume:.2f}x o normal, preco no "
                        f"{position * 100:.0f}% da faixa."
                    ),
                )
            )
    elif ratio >= EXPANSION_RATIO:
        results.append(
            ChartStructure(
                kind=StructureKind.EXPANSION,
                direction=None,
                index=last_index,
                open_time=last_time,
                description=(
                    f"Amplitude media {ratio:.2f}x a das {window} barras "
                    "anteriores — expansao em curso."
                ),
            )
        )
    return results


def _range_structure(
    candles: Sequence[CandleFeatureLike], *, atr: float, window: int = RANGE_WINDOW
) -> ChartStructure | None:
    """Faixa definida: largura contida em poucos ATR e toques nos dois lados."""
    if len(candles) < window:
        return None
    window_candles = candles[-window:]
    highest = max(float(candle.high) for candle in window_candles)
    lowest = min(float(candle.low) for candle in window_candles)
    width = highest - lowest
    if width <= 0 or width > atr * RANGE_MAX_WIDTH_ATR:
        return None

    tolerance = atr * LEVEL_TOLERANCE_ATR
    top_touches = sum(
        1 for candle in window_candles if float(candle.high) >= highest - tolerance
    )
    bottom_touches = sum(
        1 for candle in window_candles if float(candle.low) <= lowest + tolerance
    )
    if top_touches < RANGE_MIN_TOUCHES or bottom_touches < RANGE_MIN_TOUCHES:
        return None

    return ChartStructure(
        kind=StructureKind.RANGE,
        direction=None,
        index=len(candles) - 1,
        open_time=candles[-1].open_time,
        description=(
            f"Range de {lowest:.5f} a {highest:.5f} ({width / atr:.2f} ATR de "
            f"largura, {top_touches} toque(s) no topo e {bottom_touches} no fundo)."
        ),
    )


def _micro_structures(
    candles: Sequence[CandleFeatureLike], *, atr: float, window: int = MICRO_WINDOW
) -> list[ChartStructure]:
    """Leituras de timing nas ultimas `window` barras.

    - **micro tendencia**: as barras recentes fecham majoritariamente no
      mesmo sentido E o deslocamento total e relevante em ATR;
    - **micro pullback**: existe micro tendencia no trecho anterior e a(s)
      ultima(s) barra(s) andaram CONTRA ela, sem desfazer o movimento.

    A ordem importa: um micro pullback so faz sentido dentro de uma micro
    tendencia, e e por isso que o pullback e avaliado sobre o trecho
    anterior, nao sobre a janela inteira.
    """
    results: list[ChartStructure] = []
    if len(candles) < window + 2:
        return results

    closes = [float(candle.close) for candle in candles]
    last_index = len(candles) - 1
    last_time = candles[last_index].open_time

    recent = closes[-window:]
    steps = [b - a for a, b in zip(recent, recent[1:], strict=False)]
    if not steps:
        return results

    ups = sum(1 for step in steps if step > 0)
    downs = sum(1 for step in steps if step < 0)
    displacement = recent[-1] - recent[0]
    strength = abs(displacement) / atr

    # Micro tendencia exige movimento ININTERRUPTO. Tolerar uma barra
    # contraria aqui engoliria justamente o caso do micro pullback, que e a
    # leitura mais util das duas para o timing.
    trend_direction: PatternDirection | None = None
    if strength >= 0.5 and ups == len(steps):
        trend_direction = PatternDirection.BULLISH
    elif strength >= 0.5 and downs == len(steps):
        trend_direction = PatternDirection.BEARISH

    if trend_direction is not None:
        results.append(
            ChartStructure(
                kind=StructureKind.MICRO_TREND,
                direction=trend_direction,
                index=last_index,
                open_time=last_time,
                description=(
                    f"Micro tendencia {trend_direction.value.lower()} nas ultimas "
                    f"{window} barras ({strength:.2f} ATR de deslocamento)."
                ),
            )
        )
        return results

    # Sem micro tendencia na janela inteira: pode ser tendencia com a ultima
    # barra contra. Avalia o trecho SEM a ultima barra.
    body = closes[-(window + 1) : -1]
    body_steps = [b - a for a, b in zip(body, body[1:], strict=False)]
    if not body_steps:
        return results
    body_displacement = body[-1] - body[0]
    body_strength = abs(body_displacement) / atr
    body_ups = sum(1 for step in body_steps if step > 0)
    body_downs = sum(1 for step in body_steps if step < 0)
    last_step = closes[-1] - closes[-2]

    if body_strength < 0.5 or last_step == 0:
        return results

    prior_direction: PatternDirection | None = None
    if body_ups == len(body_steps) and last_step < 0:
        prior_direction = PatternDirection.BULLISH
    elif body_downs == len(body_steps) and last_step > 0:
        prior_direction = PatternDirection.BEARISH

    if prior_direction is None:
        return results

    pullback_size = abs(last_step) / atr
    # Um "pullback" que desfaz todo o movimento nao e correcao, e reversao.
    if pullback_size >= body_strength:
        return results

    results.append(
        ChartStructure(
            kind=StructureKind.MICRO_PULLBACK,
            direction=prior_direction,
            index=last_index,
            open_time=last_time,
            description=(
                f"Micro pullback de {pullback_size:.2f} ATR contra uma micro "
                f"tendencia {prior_direction.value.lower()} de "
                f"{body_strength:.2f} ATR — correcao, nao reversao."
            ),
        )
    )
    return results


def detect_structures(
    candles: Sequence[CandleFeatureLike],
    features: pd.DataFrame,
    *,
    swings: Sequence[SwingPoint] | None = None,
) -> list[ChartStructure]:
    """Todas as estruturas presentes na serie, ordenadas por barra.

    Serie curta ou sem ATR devolve lista vazia — a ausencia de estrutura e
    reportada como ausencia, nunca como uma estrutura "neutra" inventada.
    """
    atr = _atr(features)
    if atr is None or len(candles) < MICRO_WINDOW + 2:
        return []

    resolved_swings = list(swings) if swings is not None else detect_swings(candles)
    structures: list[ChartStructure] = [
        *_double_extremes(candles, resolved_swings, atr=atr),
        *_volatility_structures(candles, features),
        *_micro_structures(candles, atr=atr),
    ]
    range_structure = _range_structure(candles, atr=atr)
    if range_structure is not None:
        structures.append(range_structure)

    return sorted(structures, key=lambda structure: structure.index)


def latest_by_kind(
    structures: Sequence[ChartStructure],
) -> dict[StructureKind, ChartStructure]:
    """Ultima ocorrencia de cada tipo — o que o feature vector consome."""
    result: dict[StructureKind, ChartStructure] = {}
    for structure in structures:
        current = result.get(structure.kind)
        if current is None or structure.index >= current.index:
            result[structure.kind] = structure
    return result
