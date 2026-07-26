"""Spread Engine: tres vetos, nenhum negociavel.

O spread e o unico custo que o robo paga em TODA entrada, ganhando ou
perdendo. Um alvo de 20 pontos com spread de 8 precisa acertar quase
sempre so para empatar — por isso o terceiro veto (compatibilidade com o
alvo) e tao importante quanto o limite absoluto, e costuma ser o esquecido.

Os tres vetos exigidos:

1. **Spread acima do limite** — absoluto, configurado pelo operador.
2. **Spread alargando rapido** — sinal de evento/iliquidez chegando; entrar
   no meio do alargamento e pagar o pior preco do dia.
3. **Spread incompativel com o alvo** — a fracao do alvo consumida pelo
   custo de entrada passa do maximo aceitavel.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.apexflow.tick_flow import TickFlowMetrics


class SpreadVerdict(enum.StrEnum):
    OK = "OK"
    TOO_WIDE = "TOO_WIDE"
    WIDENING = "WIDENING"
    INCOMPATIBLE_WITH_TARGET = "INCOMPATIBLE_WITH_TARGET"
    UNKNOWN = "UNKNOWN"


SPREAD_VERDICT_LABELS: dict[SpreadVerdict, str] = {
    SpreadVerdict.OK: "Spread aceitavel",
    SpreadVerdict.TOO_WIDE: "Spread acima do limite",
    SpreadVerdict.WIDENING: "Spread alargando rapidamente",
    SpreadVerdict.INCOMPATIBLE_WITH_TARGET: "Spread incompativel com o alvo",
    SpreadVerdict.UNKNOWN: "Spread nao pode ser medido",
}

DEFAULT_MAX_SPREAD_POINTS = 30.0
DEFAULT_MAX_WIDENING_RATIO = 1.6
"""Spread medio recente 60% acima do anterior ja e alargamento, nao ruido."""

DEFAULT_MAX_SPREAD_TO_TARGET = 0.20
"""O custo de entrada nao pode consumir mais que 20% do alvo."""


@dataclass(frozen=True, slots=True)
class SpreadReading:
    verdict: SpreadVerdict
    spread_points: float | None
    mean_points: float | None
    max_points: float | None
    trend: float | None
    target_points: float | None
    spread_to_target: float | None
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return SPREAD_VERDICT_LABELS[self.verdict]

    @property
    def allows_entry(self) -> bool:
        return self.verdict == SpreadVerdict.OK


def read_spread(
    flow: TickFlowMetrics,
    *,
    target_points: float | None = None,
    max_spread_points: float = DEFAULT_MAX_SPREAD_POINTS,
    max_widening_ratio: float = DEFAULT_MAX_WIDENING_RATIO,
    max_spread_to_target: float = DEFAULT_MAX_SPREAD_TO_TARGET,
    fallback_spread_points: float | None = None,
) -> SpreadReading:
    """Avalia os tres vetos contra o fluxo de ticks medido.

    `fallback_spread_points` (normalmente o campo `spread` da ultima
    candle) so e usado quando NAO ha ticks — melhor um spread defasado e
    declarado que nenhuma leitura. `target_points` ausente desativa apenas
    o terceiro veto; os outros dois continuam valendo.
    """
    spread = flow.spread_now_points
    if spread is None:
        spread = fallback_spread_points

    if spread is None:
        return SpreadReading(
            verdict=SpreadVerdict.UNKNOWN,
            spread_points=None,
            mean_points=flow.spread_mean_points,
            max_points=flow.spread_max_points,
            trend=flow.spread_trend,
            target_points=target_points,
            spread_to_target=None,
            reasons=(
                "Sem tick nem candle recente para medir o spread — entrada "
                "bloqueada por falta de informacao, nunca por suposicao.",
            ),
        )

    ratio = spread / target_points if target_points and target_points > 0 else None
    reasons: list[str] = [f"Spread atual {spread:.1f} pontos (limite {max_spread_points:.1f})."]

    if spread > max_spread_points:
        reasons.append("Acima do limite configurado: nenhuma entrada e considerada.")
        verdict = SpreadVerdict.TOO_WIDE
    elif flow.spread_trend is not None and flow.spread_trend > max_widening_ratio:
        reasons.append(
            f"Spread {flow.spread_trend:.2f}x maior que na primeira metade da "
            "janela — alargando rapido, o robo aguarda estabilizar."
        )
        verdict = SpreadVerdict.WIDENING
    elif ratio is not None and ratio > max_spread_to_target:
        reasons.append(
            f"O spread consome {ratio * 100:.0f}% do alvo de {target_points:.1f} "
            f"pontos (maximo {max_spread_to_target * 100:.0f}%) — a operacao "
            "nasceria com expectativa negativa."
        )
        verdict = SpreadVerdict.INCOMPATIBLE_WITH_TARGET
    else:
        if ratio is not None:
            reasons.append(
                f"Consome {ratio * 100:.0f}% do alvo de {target_points:.1f} pontos."
            )
        verdict = SpreadVerdict.OK

    return SpreadReading(
        verdict=verdict,
        spread_points=spread,
        mean_points=flow.spread_mean_points,
        max_points=flow.spread_max_points,
        trend=flow.spread_trend,
        target_points=target_points,
        spread_to_target=ratio,
        reasons=tuple(reasons),
    )
