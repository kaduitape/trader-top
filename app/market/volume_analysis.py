"""Eventos de volume: climax, absorcao, exaustao e divergencia volume/RSI
x preco (Fase 18.5).

Reaproveita colunas ja calculadas por `app.market.features.build_candle_features`
(`relative_volume_20`, `rsi_14`, `candle_body`, `candle_amplitude`) — nenhuma
formula e recalculada aqui, so interpretada. Divergencia usa os swings ja
detectados por `app.market.structure.detect_swings` (mesma leak-safety:
`SwingPoint.confirmed_at_index` continua valendo, este modulo nao adiciona
nenhum vazamento novo)."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.market.features import CandleFeatureLike
from app.market.structure import SwingKind, SwingPoint


class VolumeEventKind(enum.StrEnum):
    CLIMAX = "CLIMAX"
    ABSORPTION = "ABSORPTION"
    EXHAUSTION = "EXHAUSTION"
    BULLISH_DIVERGENCE = "BULLISH_DIVERGENCE"
    BEARISH_DIVERGENCE = "BEARISH_DIVERGENCE"


@dataclass(frozen=True, slots=True)
class VolumeEvent:
    kind: VolumeEventKind
    index: int
    open_time: datetime
    description: str


def _divergences(features: pd.DataFrame, swings: Sequence[SwingPoint]) -> list[VolumeEvent]:
    ordered = sorted(swings, key=lambda s: s.index)
    events: list[VolumeEvent] = []
    last_high: SwingPoint | None = None
    last_high_rsi: float | None = None
    last_low: SwingPoint | None = None
    last_low_rsi: float | None = None

    for swing in ordered:
        if swing.index >= len(features):
            continue
        rsi_value = features["rsi_14"].iloc[swing.index]
        if pd.isna(rsi_value):
            continue

        if swing.kind == SwingKind.HIGH:
            if (
                last_high is not None
                and last_high_rsi is not None
                and swing.price > last_high.price
                and rsi_value < last_high_rsi
            ):
                events.append(
                    VolumeEvent(
                        kind=VolumeEventKind.BEARISH_DIVERGENCE,
                        index=swing.index,
                        open_time=swing.open_time,
                        description=(
                            "Divergencia de baixa: preco fez topo mais alto, "
                            "mas RSI nao confirmou (topo mais baixo)."
                        ),
                    )
                )
            last_high, last_high_rsi = swing, rsi_value
        else:
            if (
                last_low is not None
                and last_low_rsi is not None
                and swing.price < last_low.price
                and rsi_value > last_low_rsi
            ):
                events.append(
                    VolumeEvent(
                        kind=VolumeEventKind.BULLISH_DIVERGENCE,
                        index=swing.index,
                        open_time=swing.open_time,
                        description=(
                            "Divergencia de alta: preco fez fundo mais baixo, "
                            "mas RSI nao confirmou (fundo mais alto)."
                        ),
                    )
                )
            last_low, last_low_rsi = swing, rsi_value

    return events


def detect_volume_events(
    candles: Sequence[CandleFeatureLike],
    features: pd.DataFrame,
    swings: Sequence[SwingPoint],
    *,
    climax_relative_volume: float = 2.0,
    absorption_body_ratio: float = 0.3,
    climax_body_ratio: float = 0.6,
    exhaustion_relative_volume: float = 0.8,
) -> list[VolumeEvent]:
    """`features` deve ser o resultado de `build_candle_features` para os
    MESMOS `candles` (mesmo indice posicional) — climax/absorcao usam
    `relative_volume_20`/`candle_body`/`candle_amplitude`; exaustao e um
    climax seguido de queda abrupta de volume; divergencia usa `rsi_14`
    nos indices dos `swings`."""
    events: list[VolumeEvent] = []
    climax_indices: list[int] = []
    n = min(len(candles), len(features))

    for i in range(n):
        rel_vol = features["relative_volume_20"].iloc[i]
        body = features["candle_body"].iloc[i]
        amplitude = features["candle_amplitude"].iloc[i]
        if pd.isna(rel_vol) or pd.isna(body) or pd.isna(amplitude) or amplitude <= 0:
            continue

        body_ratio = body / amplitude
        if rel_vol >= climax_relative_volume and body_ratio >= climax_body_ratio:
            events.append(
                VolumeEvent(
                    kind=VolumeEventKind.CLIMAX,
                    index=i,
                    open_time=candles[i].open_time,
                    description="Volume muito acima da media com candle direcional forte (climax).",
                )
            )
            climax_indices.append(i)
        elif rel_vol >= climax_relative_volume and body_ratio <= absorption_body_ratio:
            events.append(
                VolumeEvent(
                    kind=VolumeEventKind.ABSORPTION,
                    index=i,
                    open_time=candles[i].open_time,
                    description="Volume muito acima da media com pouco progresso de preco (absorcao).",
                )
            )

    for i in climax_indices:
        j = i + 1
        if j >= n:
            continue
        rel_vol_next = features["relative_volume_20"].iloc[j]
        if not pd.isna(rel_vol_next) and rel_vol_next <= exhaustion_relative_volume:
            events.append(
                VolumeEvent(
                    kind=VolumeEventKind.EXHAUSTION,
                    index=j,
                    open_time=candles[j].open_time,
                    description="Queda abrupta de volume logo apos um climax (possivel exaustao).",
                )
            )

    events.extend(_divergences(features, swings))
    events.sort(key=lambda e: e.index)
    return events
