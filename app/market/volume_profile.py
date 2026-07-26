"""Perfil de volume por hora do dia e leitura do volume corrente.

O volume do Forex e ciclico: 03:00 UTC em EURUSD e naturalmente fraco,
14:00 UTC e naturalmente forte. Comparar o volume atual com uma media
global esconderia isso. Aqui o volume corrente e comparado com a MEDIANA
HISTORICA DA MESMA HORA — a unica comparacao que responde "esta forte ou
fraco PARA ESTE HORARIO?", que e exatamente o criterio pedido pelo seletor
de operacional (`app.execution.playbook`).

Usa `tick_volume` das candles ja coletadas (a unica medida de volume que o
MetaTrader entrega de forma confiavel em Forex — volume real de mercado nao
existe nesse mercado descentralizado; ver `docs/assumptions.md`). Mediana,
nao media, porque um unico dia de noticia distorce a media de uma hora
inteira.

Modulo puro (sem banco, sem MetaTrader): recebe candles, devolve leitura.
"""

from __future__ import annotations

import enum
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.market.features import CandleFeatureLike


class VolumeLevel(enum.StrEnum):
    DEAD = "DEAD"
    """Praticamente sem negocios — nem o spread e confiavel."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    """Pico atipico: normalmente noticia/evento, nao continuidade de fluxo."""

    UNKNOWN = "UNKNOWN"
    """Historico insuficiente para uma comparacao honesta."""


VOLUME_LEVEL_LABELS: dict[VolumeLevel, str] = {
    VolumeLevel.DEAD: "Parado",
    VolumeLevel.LOW: "Fraco",
    VolumeLevel.NORMAL: "Normal",
    VolumeLevel.HIGH: "Forte",
    VolumeLevel.EXTREME: "Explosivo",
    VolumeLevel.UNKNOWN: "Sem historico",
}

DEAD_RATIO = 0.30
LOW_RATIO = 0.65
HIGH_RATIO = 1.50
EXTREME_RATIO = 2.50

MIN_SAMPLES_PER_HOUR = 3
"""Menos que isso na mesma hora nao e baseline — e coincidencia."""

RECENT_BARS = 3
"""O volume corrente e a mediana das ultimas barras fechadas, nao a ultima
sozinha: uma unica candle e ruido demais para virar decisao operacional."""


@dataclass(frozen=True, slots=True)
class HourlyVolumeProfile:
    """Mediana de `tick_volume` por hora UTC, mais o baseline global."""

    medians_by_hour: dict[int, float] = field(default_factory=dict)
    samples_by_hour: dict[int, int] = field(default_factory=dict)
    overall_median: float = 0.0
    total_samples: int = 0

    def median_for_hour(self, hour: int) -> float | None:
        if self.samples_by_hour.get(hour, 0) < MIN_SAMPLES_PER_HOUR:
            return None
        return self.medians_by_hour.get(hour)

    @property
    def has_baseline(self) -> bool:
        return self.total_samples > 0 and self.overall_median > 0


@dataclass(frozen=True, slots=True)
class VolumeReading:
    """Como esta o fluxo AGORA, comparado ao que e normal neste horario."""

    level: VolumeLevel
    current_volume: float
    hour: int
    hour_median: float | None
    overall_median: float
    ratio_vs_hour: float | None
    ratio_vs_overall: float | None
    baseline_used: str
    """`hora` (comparacao com a mesma hora), `global` (sem amostras
    suficientes na hora) ou `nenhum` (sem historico)."""

    reasons: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return VOLUME_LEVEL_LABELS[self.level]

    @property
    def ratio(self) -> float | None:
        return self.ratio_vs_hour if self.ratio_vs_hour is not None else self.ratio_vs_overall

    @property
    def is_tradeable(self) -> bool:
        """Volume compativel com execucao. `EXTREME` NAO entra: pico de
        evento tem spread alargado e slippage imprevisivel — o seletor de
        operacional prefere ficar de fora a operar dentro do pico."""
        return self.level in (VolumeLevel.LOW, VolumeLevel.NORMAL, VolumeLevel.HIGH)


def _volume_of(candle: CandleFeatureLike) -> float:
    return float(getattr(candle, "tick_volume", 0) or 0)


def build_volume_profile(candles: Sequence[CandleFeatureLike]) -> HourlyVolumeProfile:
    """Constroi o perfil horario a partir das candles ja coletadas.

    Espera candles de um unico timeframe, em ordem crescente de
    `open_time`. Series curtas produzem um perfil sem baseline por hora —
    situacao reportada, nunca preenchida com um numero inventado.
    """
    buckets: dict[int, list[float]] = {}
    everything: list[float] = []
    for candle in candles:
        volume = _volume_of(candle)
        if volume <= 0:
            continue
        hour = candle.open_time.hour
        buckets.setdefault(hour, []).append(volume)
        everything.append(volume)

    medians = {hour: statistics.median(values) for hour, values in buckets.items()}
    samples = {hour: len(values) for hour, values in buckets.items()}
    return HourlyVolumeProfile(
        medians_by_hour=medians,
        samples_by_hour=samples,
        overall_median=statistics.median(everything) if everything else 0.0,
        total_samples=len(everything),
    )


def _classify(ratio: float) -> VolumeLevel:
    if ratio < DEAD_RATIO:
        return VolumeLevel.DEAD
    if ratio < LOW_RATIO:
        return VolumeLevel.LOW
    if ratio < HIGH_RATIO:
        return VolumeLevel.NORMAL
    if ratio < EXTREME_RATIO:
        return VolumeLevel.HIGH
    return VolumeLevel.EXTREME


def read_current_volume(
    candles: Sequence[CandleFeatureLike],
    *,
    profile: HourlyVolumeProfile | None = None,
    now: datetime | None = None,
) -> VolumeReading:
    """Le o volume corrente contra o baseline da mesma hora.

    A hora de referencia vem da ULTIMA CANDLE (o fluxo que de fato foi
    medido), nao do relogio da maquina — assim a leitura continua correta
    quando o worker processa com atraso.
    """
    if not candles:
        return VolumeReading(
            level=VolumeLevel.UNKNOWN,
            current_volume=0.0,
            hour=(now or datetime.now(UTC)).hour,
            hour_median=None,
            overall_median=0.0,
            ratio_vs_hour=None,
            ratio_vs_overall=None,
            baseline_used="nenhum",
            reasons=("Sem candles para medir volume.",),
        )

    resolved_profile = profile if profile is not None else build_volume_profile(candles)
    recent = [_volume_of(candle) for candle in candles[-RECENT_BARS:]]
    recent = [value for value in recent if value > 0]
    current = statistics.median(recent) if recent else 0.0
    hour = candles[-1].open_time.hour

    hour_median = resolved_profile.median_for_hour(hour)
    overall_median = resolved_profile.overall_median
    ratio_vs_hour = (
        current / hour_median if hour_median is not None and hour_median > 0 else None
    )
    ratio_vs_overall = current / overall_median if overall_median > 0 else None

    reasons: list[str] = []
    if ratio_vs_hour is not None:
        baseline_used = "hora"
        level = _classify(ratio_vs_hour)
        reasons.append(
            f"Volume atual {current:.0f} contra mediana {hour_median:.0f} da mesma "
            f"hora ({hour:02d}:00 UTC, {resolved_profile.samples_by_hour.get(hour, 0)} "
            f"amostras): {ratio_vs_hour:.2f}x."
        )
    elif ratio_vs_overall is not None:
        baseline_used = "global"
        level = _classify(ratio_vs_overall)
        reasons.append(
            f"Sem amostras suficientes para {hour:02d}:00 UTC (minimo "
            f"{MIN_SAMPLES_PER_HOUR}); comparado com a mediana geral "
            f"{overall_median:.0f}: {ratio_vs_overall:.2f}x."
        )
    else:
        baseline_used = "nenhum"
        level = VolumeLevel.UNKNOWN
        reasons.append(
            "Historico insuficiente para estabelecer baseline de volume — "
            "leitura reportada como desconhecida, nunca estimada."
        )

    return VolumeReading(
        level=level,
        current_volume=current,
        hour=hour,
        hour_median=hour_median,
        overall_median=overall_median,
        ratio_vs_hour=ratio_vs_hour,
        ratio_vs_overall=ratio_vs_overall,
        baseline_used=baseline_used,
        reasons=tuple(reasons),
    )
