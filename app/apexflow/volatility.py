"""Volatility Engine: so opera quando ha movimento para pagar o custo.

A regra central e simples e costuma ser a mais rentavel do sistema: **sem
volatilidade suficiente, nao existe alvo que pague spread + comissao +
slippage**. Um mercado parado nao e "menos arriscado", e uma expectativa
matematica negativa garantida.

Duas escalas, porque elas discordam e a discordancia e informacao:

- **barras** — ATR e volatilidade realizada, ja calculados em
  `app.market.features`, nunca recalculados aqui;
- **segundos** — dispersao dos precos dos ticks na janela recente, que
  detecta contracao/expansao muito antes da candle fechar.

ATR alto com volatilidade de segundos baixa = o movimento ja aconteceu e o
mercado congelou. ATR baixo com segundos altos = expansao comecando.
"""

from __future__ import annotations

import enum
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from app.apexflow.tick_flow import TickLike

MIN_TICKS_FOR_SECOND_VOLATILITY = 10
EXPANSION_RATIO = 1.30
CONTRACTION_RATIO = 0.75


class VolatilityState(enum.StrEnum):
    EXPANDING = "EXPANDING"
    STABLE = "STABLE"
    CONTRACTING = "CONTRACTING"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


VOLATILITY_STATE_LABELS: dict[VolatilityState, str] = {
    VolatilityState.EXPANDING: "Expandindo",
    VolatilityState.STABLE: "Estavel",
    VolatilityState.CONTRACTING: "Contraindo",
    VolatilityState.INSUFFICIENT: "Insuficiente para operar",
    VolatilityState.UNKNOWN: "Sem dados",
}


@dataclass(frozen=True, slots=True)
class VolatilityReading:
    state: VolatilityState
    atr_points: float | None
    atr_ratio: float | None
    """ATR atual / ATR medio da janela de referencia."""

    true_range_points: float | None
    realized_volatility: float | None
    second_volatility_points: float | None
    """Desvio-padrao dos precos dos ticks, em pontos."""

    min_required_points: float
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return VOLATILITY_STATE_LABELS[self.state]

    @property
    def allows_entry(self) -> bool:
        return self.state in (
            VolatilityState.EXPANDING,
            VolatilityState.STABLE,
            VolatilityState.CONTRACTING,
        )


def _last(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns or frame.empty:
        return None
    value = frame[column].iloc[-1]
    return None if pd.isna(value) else float(value)


def second_volatility_points(ticks: Sequence[TickLike], *, point: float) -> float | None:
    """Dispersao dos precos medios dos ticks, em pontos do simbolo."""
    if len(ticks) < MIN_TICKS_FOR_SECOND_VOLATILITY:
        return None
    safe_point = point if point > 0 else 1.0
    mids = [(float(tick.bid) + float(tick.ask)) / 2 for tick in ticks]
    return statistics.pstdev(mids) / safe_point


def read_volatility(
    features: pd.DataFrame,
    ticks: Sequence[TickLike],
    *,
    point: float,
    min_atr_points: float,
    baseline_bars: int = 100,
) -> VolatilityReading:
    """Avalia se ha movimento suficiente e para onde a volatilidade caminha.

    `min_atr_points` e o piso operacional: abaixo dele o motor devolve
    `INSUFFICIENT` e a decisao vira abstencao, sem excecao.
    """
    atr = _last(features, "atr_14")
    realized = _last(features, "realized_volatility_20")
    seconds = second_volatility_points(ticks, point=point)
    safe_point = point if point > 0 else 1.0
    atr_points = atr / safe_point if atr is not None else None

    true_range = None
    if {"high", "low"} <= set(features.columns) and not features.empty:
        true_range = (
            float(features["high"].iloc[-1]) - float(features["low"].iloc[-1])
        ) / safe_point

    atr_ratio = None
    if atr is not None and "atr_14" in features.columns:
        baseline = features["atr_14"].tail(baseline_bars).mean()
        if not pd.isna(baseline) and baseline > 0:
            atr_ratio = atr / float(baseline)

    reasons: list[str] = []
    if atr_points is None:
        state = VolatilityState.UNKNOWN
        reasons.append("ATR indisponivel — volatilidade nao pode ser avaliada.")
    elif atr_points < min_atr_points:
        state = VolatilityState.INSUFFICIENT
        reasons.append(
            f"ATR de {atr_points:.1f} pontos abaixo do minimo operacional "
            f"({min_atr_points:.1f}): nenhum alvo pagaria spread e slippage."
        )
    elif atr_ratio is not None and atr_ratio >= EXPANSION_RATIO:
        state = VolatilityState.EXPANDING
        reasons.append(
            f"ATR {atr_ratio:.2f}x acima da media recente — volatilidade em expansao."
        )
    elif atr_ratio is not None and atr_ratio <= CONTRACTION_RATIO:
        state = VolatilityState.CONTRACTING
        reasons.append(
            f"ATR {atr_ratio:.2f}x da media recente — mercado comprimindo "
            "(favorece rompimento, desfavorece continuidade)."
        )
    else:
        state = VolatilityState.STABLE
        reasons.append(f"ATR de {atr_points:.1f} pontos dentro da faixa normal.")

    if seconds is not None:
        reasons.append(f"Volatilidade dos ultimos ticks: {seconds:.1f} pontos.")
    else:
        reasons.append(
            "Ticks insuficientes para medir a volatilidade de curtissimo prazo."
        )

    return VolatilityReading(
        state=state,
        atr_points=atr_points,
        atr_ratio=atr_ratio,
        true_range_points=true_range,
        realized_volatility=realized,
        second_volatility_points=seconds,
        min_required_points=min_atr_points,
        reasons=tuple(reasons),
    )
