"""Motor de score composto (Fase 18.7).

Cada `score_*` traduz fenomenos JA DETECTADOS (estrutura, price action,
liquidez, volume, noticias, fundamentos) numa nota 0-100 + justificativa —
os modulos de deteccao (`structure.py`/`price_action.py`/`smc.py`/
`volume_analysis.py`) continuam puramente descritivos, sem nenhuma nocao de
"bom"/"ruim". Essa separacao entre FATOS (deteccao) e JULGAMENTO (score) e
a mesma ja usada em `app.market.data_quality` (`check_candles`/`check_ticks`
vs. `compute_score`).

`FactorScore.rationale` NUNCA fica vazio e o total (`OpportunityScore.
total_score`) NUNCA aparece sem os 7 fatores que o compoem — mesmo
principio anti-numero-unico do prompt mestre, ja aplicado ao score de
qualidade de dados e as metricas de backtest."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

from app.market.price_action import CandlestickPattern, PatternDirection
from app.market.smc import FairValueGap, LiquiditySweep, OrderBlock, PremiumDiscountZone
from app.market.structure import StructureEvent, StructureEventType, StructureLabel
from app.market.volume_analysis import VolumeEvent, VolumeEventKind
from app.mt5.market_data import Timeframe
from app.news.provider import FundamentalsAssessment, NewsAssessment


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    structure: float = 0.30
    price_action: float = 0.25
    liquidity: float = 0.15
    volume: float = 0.10
    news: float = 0.10
    fundamentals: float = 0.05
    correlation: float = 0.05

    def __post_init__(self) -> None:
        total = (
            self.structure
            + self.price_action
            + self.liquidity
            + self.volume
            + self.news
            + self.fundamentals
            + self.correlation
        )
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"Pesos devem somar 1.0 (soma atual: {total}).")


@dataclass(frozen=True, slots=True)
class FactorScore:
    name: str
    raw_score: float
    rationale: list[str] = field(default_factory=list)
    weight: float = 0.0
    weighted_contribution: float = 0.0


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    symbol: str
    timeframe: Timeframe
    generated_at: datetime
    factors: list[FactorScore]
    total_score: float
    threshold: float
    recommendation: Literal["ENTER", "DO_NOT_ENTER"]
    reasons_below_threshold: list[str]


def _clip(value: float) -> float:
    return max(0.0, min(100.0, value))


def score_structure(
    events: Sequence[StructureEvent], labels: Sequence[StructureLabel]
) -> FactorScore:
    if not events:
        return FactorScore(
            name="structure",
            raw_score=50.0,
            rationale=["Nenhum evento de estrutura (BOS/CHoCH/MSS) detectado — score neutro."],
        )

    latest = max(events, key=lambda e: e.index)
    if latest.type == StructureEventType.MSS:
        score = 90.0
        rationale = [
            f"Ultimo evento: MSS ({latest.direction.value}) — reversao de estrutura "
            "confirmada por um segundo rompimento na nova direcao."
        ]
    elif latest.type == StructureEventType.BOS:
        score = 70.0
        rationale = [
            f"Ultimo evento: BOS ({latest.direction.value}) — rompimento a favor da "
            "tendencia vigente (continuacao)."
        ]
    else:
        score = 40.0
        rationale = [
            f"Ultimo evento: CHoCH ({latest.direction.value}) — mudanca de carater "
            "ainda NAO confirmada, sinal isolado e mais fraco."
        ]

    if len(labels) >= 2:
        last_two = sorted(labels, key=lambda lab: lab.swing.index)[-2:]
        trend_labels = {lab.label for lab in last_two}
        if trend_labels <= {"HH", "HL"} or trend_labels <= {"LH", "LL"}:
            score += 10.0
            rationale.append("Estrutura HH/HL (ou LH/LL) consistente nas ultimas barras.")

    return FactorScore(name="structure", raw_score=_clip(score), rationale=rationale)


def score_price_action(patterns: Sequence[CandlestickPattern]) -> FactorScore:
    if not patterns:
        return FactorScore(
            name="price_action",
            raw_score=50.0,
            rationale=["Nenhum padrao de candle detectado — score neutro."],
        )

    latest = max(patterns, key=lambda p: p.index)
    score = 50.0 if latest.direction == PatternDirection.NEUTRAL else 50.0 + latest.strength * 40.0

    rationale = [
        f"Padrao mais recente: {latest.name.value} ({latest.direction.value}), "
        f"forca {latest.strength:.2f}."
    ]
    return FactorScore(name="price_action", raw_score=_clip(score), rationale=rationale)


def score_liquidity(
    order_blocks: Sequence[OrderBlock],
    fvgs: Sequence[FairValueGap],
    sweeps: Sequence[LiquiditySweep],
    pd_zone: PremiumDiscountZone | None,
) -> FactorScore:
    unmitigated_obs = [ob for ob in order_blocks if not ob.mitigated]
    unfilled_fvgs = [g for g in fvgs if not g.filled]
    confirmed_sweeps = [s for s in sweeps if s.reversal_confirmed]

    if not order_blocks and not fvgs and not sweeps:
        return FactorScore(
            name="liquidity",
            raw_score=50.0,
            rationale=[
                "Nenhuma zona de liquidez (order block/FVG/sweep) detectada — score neutro."
            ],
        )

    score = 50.0
    score += min(20.0, 10.0 * len(unmitigated_obs))
    score += min(20.0, 10.0 * len(unfilled_fvgs))
    score += min(30.0, 15.0 * len(confirmed_sweeps))

    rationale = [
        f"{len(unmitigated_obs)} order block(s) nao mitigado(s), "
        f"{len(unfilled_fvgs)} FVG(s) nao preenchido(s), "
        f"{len(confirmed_sweeps)} varredura(s) de liquidez com reversao confirmada."
    ]
    if pd_zone is not None:
        rationale.append(
            f"Zona de equilibrio: {pd_zone.equilibrium:.5f} "
            f"(premium {pd_zone.premium_zone}, discount {pd_zone.discount_zone})."
        )

    return FactorScore(name="liquidity", raw_score=_clip(score), rationale=rationale)


def score_volume(events: Sequence[VolumeEvent]) -> FactorScore:
    if not events:
        return FactorScore(
            name="volume",
            raw_score=50.0,
            rationale=["Nenhum evento de volume relevante detectado — score neutro."],
        )

    divergences = [
        e
        for e in events
        if e.kind in (VolumeEventKind.BULLISH_DIVERGENCE, VolumeEventKind.BEARISH_DIVERGENCE)
    ]
    exhaustions = [e for e in events if e.kind == VolumeEventKind.EXHAUSTION]

    score = 50.0
    score += min(30.0, 15.0 * len(divergences))
    score += min(20.0, 10.0 * len(exhaustions))

    rationale = [
        f"{len(divergences)} divergencia(s) volume/RSI x preco, "
        f"{len(exhaustions)} sinal(is) de exaustao detectado(s)."
    ]
    return FactorScore(name="volume", raw_score=_clip(score), rationale=rationale)


def score_news(assessment: NewsAssessment) -> FactorScore:
    return FactorScore(
        name="news", raw_score=_clip(assessment.score_contribution), rationale=[assessment.message]
    )


def score_fundamentals(assessment: FundamentalsAssessment) -> FactorScore:
    return FactorScore(
        name="fundamentals",
        raw_score=_clip(assessment.score_contribution),
        rationale=[assessment.message],
    )


def score_correlation() -> FactorScore:
    return FactorScore(
        name="correlation",
        raw_score=50.0,
        rationale=[
            "Correlacao entre ativos fora do escopo desta fase (ver "
            "app/market/features.py — exige alinhamento multi-simbolo nao "
            "implementado ainda) — contribui neutro, nunca fabricado."
        ],
    )


def compute_opportunity_score(
    *,
    symbol: str,
    timeframe: Timeframe,
    generated_at: datetime,
    structure: FactorScore,
    price_action: FactorScore,
    liquidity: FactorScore,
    volume: FactorScore,
    news: FactorScore,
    fundamentals: FactorScore,
    correlation: FactorScore,
    weights: ScoreWeights = ScoreWeights(),
    threshold: float = 90.0,
) -> OpportunityScore:
    named_inputs = (
        (structure, weights.structure),
        (price_action, weights.price_action),
        (liquidity, weights.liquidity),
        (volume, weights.volume),
        (news, weights.news),
        (fundamentals, weights.fundamentals),
        (correlation, weights.correlation),
    )

    factors = [
        replace(factor, weight=weight, weighted_contribution=factor.raw_score * weight)
        for factor, weight in named_inputs
    ]
    total_score = sum(f.weighted_contribution for f in factors)
    recommendation: Literal["ENTER", "DO_NOT_ENTER"] = (
        "ENTER" if total_score >= threshold else "DO_NOT_ENTER"
    )

    reasons_below_threshold: list[str] = []
    if recommendation == "DO_NOT_ENTER":
        reasons_below_threshold.append(
            f"Score total {total_score:.1f} abaixo do limiar minimo {threshold:.1f}."
        )
        for f in factors:
            if f.raw_score < 60.0:
                reasons_below_threshold.append(
                    f"{f.name}: score baixo ({f.raw_score:.1f}) — {'; '.join(f.rationale)}"
                )

    return OpportunityScore(
        symbol=symbol,
        timeframe=timeframe,
        generated_at=generated_at,
        factors=factors,
        total_score=total_score,
        threshold=threshold,
        recommendation=recommendation,
        reasons_below_threshold=reasons_below_threshold,
    )
