"""AI Decision Engine: COMPRAR, VENDER ou NAO OPERAR.

Tres saidas, nunca mais. E a terceira e a resposta PADRAO: o motor so
executa quando a confianca supera o limite configurado E nenhum veto duro
dispara. Nao existe caminho no codigo que force uma entrada para
"aproveitar" um ciclo — quando as evidencias discordam, discordancia vira
probabilidade de abstencao, nao um chute desempatado.

## Arquitetura da decisao

```
        vetos duros  ──────────────────────────────► NAO OPERAR
             │ (nenhum disparou)
             ▼
    modelo de probabilidade ──► p(compra), p(venda), p(abstencao)
             │
             ▼
      confianca >= limite?  ──── nao ──────────────► NAO OPERAR
             │ sim
             ▼
        COMPRAR / VENDER
```

Os **vetos vem antes do modelo e nao podem ser sobrepostos por ele**. Um
modelo com 99% de confianca em COMPRAR continua sendo recusado se o spread
estourou o limite. Essa ordem e a diferenca entre um sistema auditavel e
um que "as vezes" respeita o risco.

## Dois cerebros, mesma interface

- `ScorecardModel` (padrao) — evidencia ponderada, deterministica e
  totalmente explicavel: cada contribuicao aparece em
  `ApexFlowDecision.evidence`. Sem treino, sem overfitting, sem caixa
  preta. E o baseline honesto contra o qual qualquer modelo treinado
  precisa provar que e melhor.
- Qualquer `ProbabilityModel` — um modelo treinado e APROVADO no registro
  (`app.ml.registry`) pode substituir o scorecard sem tocar em nada mais,
  porque consome o mesmo `FeatureVector` versionado.

A probabilidade calculada e sempre registrada (`journal`) para auditoria e
reavaliacao posterior, inclusive nas decisoes de NAO OPERAR — que sao a
maioria e a parte mais informativa do historico.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.apexflow.config import ApexFlowConfig
from app.apexflow.context import MarketContext, MarketContextState
from app.apexflow.features import FeatureVector
from app.apexflow.liquidity import LiquidityReading, LiquidityState
from app.apexflow.momentum import MomentumReading, MomentumState
from app.apexflow.mtf import MultiTimeframeView
from app.apexflow.spread import SpreadReading, SpreadVerdict
from app.apexflow.tick_flow import TickDirection, TickFlowMetrics
from app.apexflow.volatility import VolatilityReading, VolatilityState
from app.market.regimes import Trend


class DecisionAction(enum.StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


ACTION_LABELS: dict[DecisionAction, str] = {
    DecisionAction.BUY: "Comprar",
    DecisionAction.SELL: "Vender",
    DecisionAction.NO_TRADE: "Nao operar",
}


@dataclass(frozen=True, slots=True)
class Evidence:
    """Uma peca de evidencia e o quanto ela pesou.

    `direction` positivo favorece compra, negativo favorece venda, zero e
    neutro (mas ainda entra na conta da abstencao)."""

    name: str
    direction: float
    weight: float
    rationale: str

    @property
    def contribution(self) -> float:
        return self.direction * self.weight


@dataclass(frozen=True, slots=True)
class ApexFlowDecision:
    action: DecisionAction
    probability_buy: float
    probability_sell: float
    probability_abstain: float
    confidence: float
    """Probabilidade da acao escolhida. Para NAO OPERAR, e a probabilidade
    de abstencao."""

    min_confidence: float
    model_version: str
    feature_version: str
    completeness: float
    vetoes: tuple[str, ...]
    reasons: tuple[str, ...]
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def label(self) -> str:
        return ACTION_LABELS[self.action]

    @property
    def is_entry(self) -> bool:
        return self.action in (DecisionAction.BUY, DecisionAction.SELL)

    @property
    def direction_trend(self) -> Trend:
        if self.action == DecisionAction.BUY:
            return Trend.UP
        if self.action == DecisionAction.SELL:
            return Trend.DOWN
        return Trend.SIDEWAYS


class ProbabilityModel(Protocol):
    """Contrato minimo de um cerebro. Devolve (compra, venda, abstencao),
    que devem somar 1."""

    version: str

    def predict(self, vector: FeatureVector) -> tuple[float, float, float]: ...


# --- Vetos duros -----------------------------------------------------------


def collect_vetoes(
    *,
    context: MarketContext,
    spread: SpreadReading,
    volatility: VolatilityReading,
    liquidity: LiquidityReading,
    flow: TickFlowMetrics,
    vector: FeatureVector,
    config: ApexFlowConfig,
) -> tuple[str, ...]:
    """Condicoes em que NENHUMA entrada e aceitavel, qualquer que seja a
    probabilidade calculada."""
    vetoes: list[str] = []

    if not context.is_tradeable:
        vetoes.append(
            f"{context.label}: "
            + (context.blockers[0] if context.blockers else "contexto nao operavel.")
        )
    if spread.verdict != SpreadVerdict.OK:
        vetoes.append(f"{spread.label}: {spread.reasons[-1] if spread.reasons else ''}".strip())
    if volatility.state in (VolatilityState.INSUFFICIENT, VolatilityState.UNKNOWN):
        vetoes.append(
            f"{volatility.label}: sem movimento suficiente para cobrir o custo da "
            "operacao."
        )
    if liquidity.blocks_entry:
        vetoes.append(
            "Manipulacao de liquidez em andamento — o robo aguarda a confirmacao "
            "da rejeicao."
        )
    if not flow.is_reliable:
        vetoes.append(
            "Fluxo de ticks insuficiente para confirmar o preco de execucao "
            f"({flow.tick_count} tick(s) na janela)."
        )
    if vector.completeness < config.min_feature_completeness:
        vetoes.append(
            f"Apenas {vector.completeness * 100:.0f}% dos sensores disponiveis "
            f"(minimo {config.min_feature_completeness * 100:.0f}%) — decidir "
            "assim seria adivinhar."
        )
    return tuple(vetoes)


# --- Scorecard deterministico ---------------------------------------------


def collect_evidence(
    *,
    context: MarketContext,
    momentum: MomentumReading,
    mtf: MultiTimeframeView,
    liquidity: LiquidityReading,
    flow: TickFlowMetrics,
    volatility: VolatilityReading,
) -> tuple[Evidence, ...]:
    """Traduz cada leitura em uma evidencia com sinal e peso.

    Os pesos refletem a hierarquia do sistema: contexto e alinhamento
    macro pesam mais que o gatilho de curtissimo prazo, porque um timing
    perfeito contra o contexto perde dinheiro de forma consistente.
    """
    evidence: list[Evidence] = []

    evidence.append(
        Evidence(
            name="mtf_alignment",
            direction=max(-1.0, min(1.0, mtf.alignment_score * 2)),
            weight=0.30,
            rationale=(
                f"Alinhamento multi-timeframe {mtf.alignment_score:+.2f} "
                f"(cobertura {mtf.coverage * 100:.0f}%)."
            ),
        )
    )

    if context.state == MarketContextState.TRENDING:
        trend_direction = 1.0 if context.trend == Trend.UP else -1.0
        evidence.append(
            Evidence(
                name="context_trend",
                direction=trend_direction,
                weight=0.20 * context.confidence,
                rationale=f"{context.label} a favor de {context.trend.value}.",
            )
        )
    elif context.state == MarketContextState.RANGING:
        evidence.append(
            Evidence(
                name="context_range",
                direction=0.0,
                weight=0.20 * context.confidence,
                rationale=(
                    "Mercado lateral: o contexto nao favorece lado nenhum, o "
                    "gatilho precisa vir da estrutura."
                ),
            )
        )

    momentum_direction = float(momentum.direction)
    if momentum.state == MomentumState.EXHAUSTED:
        evidence.append(
            Evidence(
                name="momentum_exhaustion",
                direction=-momentum_direction,
                weight=0.15,
                rationale=(
                    "Exaustao detectada: o movimento perdeu quem o empurrava, "
                    "favorecendo o lado contrario."
                ),
            )
        )
    elif momentum.favours_continuation:
        evidence.append(
            Evidence(
                name="momentum_continuation",
                direction=momentum_direction,
                weight=0.18,
                rationale=f"{momentum.label} no sentido atual do preco.",
            )
        )
    else:
        evidence.append(
            Evidence(
                name="momentum_weak",
                direction=0.0,
                weight=0.10,
                rationale=f"{momentum.label}: momentum nao sustenta entrada.",
            )
        )

    evidence.append(
        Evidence(
            name="tick_flow_bias",
            direction=float(flow.direction_bias),
            weight=0.12 * (flow.efficiency if flow.efficiency is not None else 0.5),
            rationale=(
                f"Fluxo de ticks {'comprador' if flow.direction_bias == TickDirection.UP else 'vendedor' if flow.direction_bias == TickDirection.DOWN else 'equilibrado'}"
                + (
                    f", eficiencia {flow.efficiency:.2f}."
                    if flow.efficiency is not None
                    else "."
                )
            ),
        )
    )

    if liquidity.state == LiquidityState.SWEEP_REVERSED and liquidity.direction is not None:
        # Sweep de topo (BEARISH) rejeitado favorece VENDA; o detector ja
        # entrega a direcao do evento, nao do rompimento.
        sweep_direction = 1.0 if liquidity.direction.value == "BULLISH" else -1.0
        evidence.append(
            Evidence(
                name="liquidity_sweep_reversed",
                direction=sweep_direction,
                weight=0.15,
                rationale=(
                    "Liquidez tomada e rejeitada: o lado contrario ao rompimento "
                    "ficou sem stops para alimentar continuidade."
                ),
            )
        )
    elif liquidity.state == LiquidityState.FALSE_BREAKOUT and liquidity.direction is not None:
        false_direction = 1.0 if liquidity.direction.value == "BULLISH" else -1.0
        evidence.append(
            Evidence(
                name="liquidity_false_breakout",
                direction=false_direction,
                weight=0.10,
                rationale="Falso rompimento confirmado no sentido oposto ao movimento.",
            )
        )

    if volatility.state == VolatilityState.EXPANDING:
        evidence.append(
            Evidence(
                name="volatility_expansion",
                direction=momentum_direction * 0.5,
                weight=0.08,
                rationale="Volatilidade expandindo a favor do movimento em curso.",
            )
        )

    return tuple(evidence)


class ScorecardModel:
    """Cerebro padrao: evidencia ponderada, deterministica e explicavel."""

    version = "scorecard-1"

    BASE_ABSTAIN = 0.35
    """Peso inicial da abstencao. Comeca alto de proposito: nao operar e o
    estado natural, e a evidencia precisa vencer essa inercia."""

    def __init__(self, evidence: Sequence[Evidence]) -> None:
        self._evidence = tuple(evidence)

    def predict(self, vector: FeatureVector) -> tuple[float, float, float]:
        buy = sum(item.contribution for item in self._evidence if item.contribution > 0)
        sell = -sum(item.contribution for item in self._evidence if item.contribution < 0)

        strongest = max(buy, sell)
        weakest = min(buy, sell)
        # Evidencia contraditoria nao vira empate desempatado por ruido —
        # vira abstencao, que e a resposta honesta.
        conflict = (weakest / strongest) if strongest > 0 else 0.0
        incompleteness = 1.0 - vector.completeness

        abstain = self.BASE_ABSTAIN + conflict * 0.5 + incompleteness * 0.5
        total = buy + sell + abstain
        if total <= 0:
            return (0.0, 0.0, 1.0)
        return (buy / total, sell / total, abstain / total)


# --- Orquestracao ----------------------------------------------------------


def decide(
    vector: FeatureVector,
    *,
    context: MarketContext,
    momentum: MomentumReading,
    mtf: MultiTimeframeView,
    liquidity: LiquidityReading,
    spread: SpreadReading,
    volatility: VolatilityReading,
    flow: TickFlowMetrics,
    config: ApexFlowConfig,
    model: ProbabilityModel | None = None,
    now: datetime | None = None,
) -> ApexFlowDecision:
    """Produz a decisao final: acao, probabilidades e a justificativa.

    A ordem e sempre a mesma e nao e configuravel: vetos duros, depois
    probabilidade, depois limite de confianca. Um `model` treinado
    substitui apenas a etapa do meio.
    """
    resolved_now = now or datetime.now(UTC)
    evidence = collect_evidence(
        context=context,
        momentum=momentum,
        mtf=mtf,
        liquidity=liquidity,
        flow=flow,
        volatility=volatility,
    )
    engine: ProbabilityModel = model or ScorecardModel(evidence)
    probability_buy, probability_sell, probability_abstain = engine.predict(vector)

    vetoes = collect_vetoes(
        context=context,
        spread=spread,
        volatility=volatility,
        liquidity=liquidity,
        flow=flow,
        vector=vector,
        config=config,
    )

    reasons: list[str] = [
        f"Probabilidades — compra {probability_buy * 100:.1f}%, venda "
        f"{probability_sell * 100:.1f}%, abstencao {probability_abstain * 100:.1f}% "
        f"(minimo para operar: {config.min_confidence * 100:.0f}%).",
    ]

    if vetoes:
        return ApexFlowDecision(
            action=DecisionAction.NO_TRADE,
            probability_buy=round(probability_buy, 4),
            probability_sell=round(probability_sell, 4),
            probability_abstain=round(probability_abstain, 4),
            confidence=round(probability_abstain, 4),
            min_confidence=config.min_confidence,
            model_version=engine.version,
            feature_version=vector.version,
            completeness=round(vector.completeness, 4),
            vetoes=vetoes,
            reasons=(
                "Veto duro acionado — nenhuma probabilidade sobrepoe um veto.",
                *reasons,
            ),
            evidence=evidence,
            generated_at=resolved_now,
        )

    if probability_buy >= probability_sell:
        candidate, confidence = DecisionAction.BUY, probability_buy
        candidate_trend = Trend.UP
    else:
        candidate, confidence = DecisionAction.SELL, probability_sell
        candidate_trend = Trend.DOWN

    if confidence < config.min_confidence:
        reasons.append(
            f"Confianca de {confidence * 100:.1f}% abaixo do minimo — o robo "
            "prefere nao operar a forcar uma entrada."
        )
        return ApexFlowDecision(
            action=DecisionAction.NO_TRADE,
            probability_buy=round(probability_buy, 4),
            probability_sell=round(probability_sell, 4),
            probability_abstain=round(probability_abstain, 4),
            confidence=round(probability_abstain, 4),
            min_confidence=config.min_confidence,
            model_version=engine.version,
            feature_version=vector.version,
            completeness=round(vector.completeness, 4),
            vetoes=(),
            reasons=tuple(reasons),
            evidence=evidence,
            generated_at=resolved_now,
        )

    if abs(mtf.alignment_score) < config.min_mtf_alignment or not mtf.agrees_with(
        candidate_trend
    ):
        reasons.append(
            f"Alinhamento multi-timeframe ({mtf.alignment_score:+.2f}) nao apoia "
            f"{ACTION_LABELS[candidate].lower()} com a folga minima exigida "
            f"({config.min_mtf_alignment:.2f})."
        )
        return ApexFlowDecision(
            action=DecisionAction.NO_TRADE,
            probability_buy=round(probability_buy, 4),
            probability_sell=round(probability_sell, 4),
            probability_abstain=round(probability_abstain, 4),
            confidence=round(probability_abstain, 4),
            min_confidence=config.min_confidence,
            model_version=engine.version,
            feature_version=vector.version,
            completeness=round(vector.completeness, 4),
            vetoes=(),
            reasons=tuple(reasons),
            evidence=evidence,
            generated_at=resolved_now,
        )

    reasons.append(
        f"{ACTION_LABELS[candidate]} com {confidence * 100:.1f}% de confianca, "
        f"contexto {context.label.lower()} e alinhamento {mtf.alignment_score:+.2f}."
    )
    reasons.extend(
        f"{item.name}: {item.rationale}"
        for item in sorted(evidence, key=lambda e: abs(e.contribution), reverse=True)[:4]
    )

    return ApexFlowDecision(
        action=candidate,
        probability_buy=round(probability_buy, 4),
        probability_sell=round(probability_sell, 4),
        probability_abstain=round(probability_abstain, 4),
        confidence=round(confidence, 4),
        min_confidence=config.min_confidence,
        model_version=engine.version,
        feature_version=vector.version,
        completeness=round(vector.completeness, 4),
        vetoes=(),
        reasons=tuple(reasons),
        evidence=evidence,
        generated_at=resolved_now,
    )
