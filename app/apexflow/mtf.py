"""Multi-Timeframe Analyzer com PAPEIS fixos por timeframe.

Cada timeframe responde uma pergunta diferente, e nunca a pergunta de
outro. Misturar os papeis e a origem de dois erros classicos: entrar no
H1 (stop enorme, alvo distante, decisao lenta) ou ler contexto no M1
(ruido virando "tendencia").

| Timeframe | Papel        | Responde                                  |
|-----------|--------------|-------------------------------------------|
| H1        | MACRO        | Para que lado o mercado esta inclinado?    |
| M15       | CONTEXTO     | A estrutura permite operar esse lado?      |
| M5        | CONFIRMACAO  | O movimento esta se desenvolvendo?         |
| M1        | TIMING       | O gatilho apareceu agora?                  |
| Tick      | EXECUCAO     | O fluxo suporta o preco de entrada?        |

**H1 nunca gera entrada** — e uma regra imposta por codigo
(`ENTRY_TIMEFRAMES`), nao uma convencao. `alignment_score` mede quanto os
timeframes concordam, com peso maior para os mais altos: divergencia entre
macro e timing e o principal motivo de abstencao honesta.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import pandas as pd

from app.market.regimes import Trend, classify_latest_regime
from app.mt5.market_data import Timeframe


class TimeframeRole(enum.StrEnum):
    MACRO = "MACRO"
    CONTEXT = "CONTEXT"
    CONFIRMATION = "CONFIRMATION"
    TIMING = "TIMING"
    EXECUTION = "EXECUTION"


ROLE_LABELS: dict[TimeframeRole, str] = {
    TimeframeRole.MACRO: "Direcao macro",
    TimeframeRole.CONTEXT: "Contexto",
    TimeframeRole.CONFIRMATION: "Confirmacao",
    TimeframeRole.TIMING: "Timing",
    TimeframeRole.EXECUTION: "Execucao",
}

TIMEFRAME_ROLES: dict[Timeframe, TimeframeRole] = {
    Timeframe.H1: TimeframeRole.MACRO,
    Timeframe.M15: TimeframeRole.CONTEXT,
    Timeframe.M5: TimeframeRole.CONFIRMATION,
    Timeframe.M1: TimeframeRole.TIMING,
}

ROLE_WEIGHTS: dict[TimeframeRole, float] = {
    TimeframeRole.MACRO: 0.40,
    TimeframeRole.CONTEXT: 0.30,
    TimeframeRole.CONFIRMATION: 0.20,
    TimeframeRole.TIMING: 0.10,
}

ENTRY_TIMEFRAMES: frozenset[Timeframe] = frozenset(
    {Timeframe.M1, Timeframe.M5, Timeframe.M15}
)
"""Timeframes onde uma entrada pode nascer. H1 esta deliberadamente fora."""


class UnsupportedEntryTimeframeError(ValueError):
    """Tentativa de gerar entrada em um timeframe reservado a contexto."""


@dataclass(frozen=True, slots=True)
class TimeframeView:
    timeframe: Timeframe
    role: TimeframeRole
    trend: Trend | None
    available: bool
    note: str

    @property
    def role_label(self) -> str:
        return ROLE_LABELS[self.role]


@dataclass(frozen=True, slots=True)
class MultiTimeframeView:
    views: tuple[TimeframeView, ...]
    macro_trend: Trend | None
    alignment_score: float
    """-1 (tudo apontando para baixo) a +1 (tudo para cima), ponderado por
    papel. Perto de 0 significa desacordo, nao neutralidade."""

    dominant_direction: Trend
    reasons: tuple[str, ...]

    def view_for(self, timeframe: Timeframe) -> TimeframeView | None:
        return next((view for view in self.views if view.timeframe == timeframe), None)

    @property
    def coverage(self) -> float:
        available = sum(1 for view in self.views if view.available)
        return available / len(self.views) if self.views else 0.0

    def agrees_with(self, direction: Trend) -> bool:
        """O alinhamento aponta para o mesmo lado da direcao proposta."""
        if direction == Trend.UP:
            return self.alignment_score > 0
        if direction == Trend.DOWN:
            return self.alignment_score < 0
        return abs(self.alignment_score) < 0.2


def _trend_value(trend: Trend | None) -> float:
    if trend == Trend.UP:
        return 1.0
    if trend == Trend.DOWN:
        return -1.0
    return 0.0


def ensure_entry_timeframe(timeframe: Timeframe) -> None:
    """Guarda de arquitetura: falha alto e cedo se alguem tentar usar H1
    (ou qualquer timeframe de contexto) para gerar entrada."""
    if timeframe not in ENTRY_TIMEFRAMES:
        raise UnsupportedEntryTimeframeError(
            f"{timeframe.value} fornece contexto, nunca entrada. Timeframes de "
            f"entrada: {sorted(item.value for item in ENTRY_TIMEFRAMES)}."
        )


def analyze_timeframes(features_by_timeframe: dict[Timeframe, pd.DataFrame]) -> MultiTimeframeView:
    """Monta a visao por papel a partir das matrizes de features ja prontas.

    Um timeframe ausente ou com dados insuficientes vira `available=False`
    com a lacuna declarada — nunca e preenchido com uma tendencia neutra
    que o alinhamento trataria como concordancia.
    """
    views: list[TimeframeView] = []
    reasons: list[str] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for timeframe, role in TIMEFRAME_ROLES.items():
        features = features_by_timeframe.get(timeframe)
        if features is None or features.empty:
            views.append(
                TimeframeView(
                    timeframe=timeframe,
                    role=role,
                    trend=None,
                    available=False,
                    note=f"{timeframe.value} sem dados — papel {ROLE_LABELS[role]} vazio.",
                )
            )
            reasons.append(
                f"{ROLE_LABELS[role]} ({timeframe.value}) indisponivel — a lacuna "
                "reduz o alinhamento, nunca conta como concordancia."
            )
            continue

        try:
            trend = classify_latest_regime(features).trend
            note = f"{timeframe.value}: {trend.value}"
        except ValueError:
            views.append(
                TimeframeView(
                    timeframe=timeframe,
                    role=role,
                    trend=None,
                    available=False,
                    note=f"{timeframe.value} com historico insuficiente para regime.",
                )
            )
            reasons.append(
                f"{ROLE_LABELS[role]} ({timeframe.value}) sem historico suficiente."
            )
            continue

        views.append(
            TimeframeView(
                timeframe=timeframe, role=role, trend=trend, available=True, note=note
            )
        )
        weight = ROLE_WEIGHTS[role]
        weighted_sum += _trend_value(trend) * weight
        weight_total += weight

    # Divide pelo peso TOTAL possivel (nao so o disponivel): um H1 ausente
    # deve derrubar o alinhamento, nao ser normalizado para fora do calculo.
    alignment = weighted_sum / sum(ROLE_WEIGHTS.values())

    macro_view = next(
        (view for view in views if view.role == TimeframeRole.MACRO and view.available), None
    )
    macro_trend = macro_view.trend if macro_view is not None else None

    if alignment > 0.2:
        dominant = Trend.UP
    elif alignment < -0.2:
        dominant = Trend.DOWN
    else:
        dominant = Trend.SIDEWAYS

    if weight_total > 0:
        reasons.insert(
            0,
            f"Alinhamento multi-timeframe {alignment:+.2f} "
            f"({', '.join(view.note for view in views if view.available)}).",
        )
    else:
        reasons.insert(0, "Nenhum timeframe disponivel para alinhamento.")

    return MultiTimeframeView(
        views=tuple(views),
        macro_trend=macro_trend,
        alignment_score=round(alignment, 4),
        dominant_direction=dominant,
        reasons=tuple(reasons),
    )
