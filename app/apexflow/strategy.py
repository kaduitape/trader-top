"""Adaptador da decisao ApexFlow para a pipeline de execucao existente.

O motor de decisao nao conhece ordens, e o motor de execucao nao conhece
feature vectors. Este adaptador e a unica peca que fala as duas linguas:
traduz um `ApexFlowDecision` de COMPRAR/VENDER em um `Signal` com niveis
concretos, e nada mais.

Os niveis vem de `app.market.trade_levels.compute_trade_levels` — o stop e
o mais apertado entre a distancia por ATR e um nivel de estrutura real
(order block / swing oposto), nunca um nivel inventado — e o alvo respeita
o `risk_reward_min` da configuracao, esticado pela volatilidade
(`app.apexflow.risk.dynamic_take_profit`).

NAO OPERAR nunca vira sinal: `generate_signal` devolve `None`, que e o
resultado normal e mais frequente.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.apexflow.config import ApexFlowConfig
from app.apexflow.decision import ApexFlowDecision, DecisionAction
from app.apexflow.engine import ApexFlowAnalysis
from app.apexflow.risk import dynamic_take_profit
from app.market.price_action import PatternDirection
from app.market.structure import SwingKind, detect_swings
from app.market.trade_levels import TradeLevels, compute_trade_levels
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy

APEXFLOW_STRATEGY_NAME = "apexflow"
"""Nome estavel para cursor, contadores do dia e busca de posicao aberta —
nunca muda com o contexto ou o modelo, senao cada mudanca zeraria os
limites de risco."""

SIGNAL_VALIDITY_MINUTES = 5
"""Uma decisao de fluxo envelhece rapido: passados 5 minutos o mercado que
a justificou ja e outro."""


def find_structure_stop(
    analysis: ApexFlowAnalysis, *, direction: SignalDirection, entry_price: float
) -> float | None:
    """Nivel de estrutura REAL mais proximo, do lado certo do preco.

    Prioriza swings detectados nas candles analisadas e cai para order
    blocks nao mitigados quando nao ha swing utilizavel. Devolve `None` se
    nenhum nivel do lado correto existir — nesse caso o stop fica puramente
    por ATR, nunca em um nivel inventado.
    """
    swings = detect_swings(list(analysis.candles)) if analysis.candles else []
    blocks = analysis.liquidity.unmitigated_order_blocks

    if direction == SignalDirection.LONG:
        candidates = [
            swing.price
            for swing in swings
            if swing.kind == SwingKind.LOW and swing.price < entry_price
        ]
        candidates += [
            block.low
            for block in blocks
            if block.direction == PatternDirection.BULLISH and block.low < entry_price
        ]
        return max(candidates) if candidates else None

    candidates = [
        swing.price
        for swing in swings
        if swing.kind == SwingKind.HIGH and swing.price > entry_price
    ]
    candidates += [
        block.high
        for block in blocks
        if block.direction == PatternDirection.BEARISH and block.high > entry_price
    ]
    return min(candidates) if candidates else None


def build_trade_levels(
    analysis: ApexFlowAnalysis,
    *,
    direction: SignalDirection,
    entry_price: float,
    point: float,
    config: ApexFlowConfig,
) -> TradeLevels | None:
    """Niveis concretos para a decisao. `None` quando o ATR e desconhecido —
    sem escala de risco nao existe stop honesto."""
    atr_points = analysis.volatility.atr_points
    if atr_points is None or atr_points <= 0:
        return None

    levels = compute_trade_levels(
        direction=direction,
        entry_price=entry_price,
        atr=atr_points * point,
        structure_stop_price=find_structure_stop(
            analysis, direction=direction, entry_price=entry_price
        ),
    )

    # O alvo final respeita o risco/retorno minimo e estica com a
    # volatilidade atual, em vez do multiplo fixo do calculo consultivo.
    return replace(
        levels,
        take_profit_3=dynamic_take_profit(
            direction=direction,
            entry_price=entry_price,
            stop_loss=levels.stop_loss,
            volatility=analysis.volatility,
            point=point,
            config=config,
        ),
    )


class ApexFlowStrategy(Strategy):
    """Converte uma decisao ja tomada em (no maximo) um sinal.

    A decisao NAO e tomada aqui: ela chega pronta. Assim o motor de decisao
    continua testavel sem execucao, e a execucao continua testavel sem
    mercado.
    """

    name = APEXFLOW_STRATEGY_NAME

    def __init__(
        self,
        analysis: ApexFlowAnalysis,
        *,
        expected_open_time: datetime,
        point: float,
        config: ApexFlowConfig,
    ) -> None:
        self._analysis = analysis
        self._expected_open_time = expected_open_time
        self._point = point
        self._config = config
        self._emitted = False
        self.levels: TradeLevels | None = None
        self.skip_reason: str | None = None

    @property
    def decision(self) -> ApexFlowDecision:
        return self._analysis.decision

    def generate_signal(self, state: MarketState) -> Signal | None:
        if self._emitted:
            return None
        decision = self._analysis.decision
        if decision.action == DecisionAction.NO_TRADE:
            self.skip_reason = (
                decision.vetoes[0] if decision.vetoes else decision.reasons[0]
                if decision.reasons
                else "Sem confianca suficiente para operar."
            )
            return None

        current_open_time = state.current.get("open_time")
        if current_open_time is None:
            return None
        current_time = current_open_time.to_pydatetime()
        if current_time != self._expected_open_time:
            return None

        direction = (
            SignalDirection.LONG
            if decision.action == DecisionAction.BUY
            else SignalDirection.SHORT
        )
        entry_price = float(state.current["close"])
        levels = build_trade_levels(
            self._analysis,
            direction=direction,
            entry_price=entry_price,
            point=self._point,
            config=self._config,
        )
        if levels is None:
            self.skip_reason = (
                "ATR indisponivel: sem escala de risco nao existe stop honesto, "
                "entao nenhuma ordem e enviada."
            )
            return None

        self._emitted = True
        self.levels = levels
        generated_at = decision.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)

        return Signal(
            symbol=self._analysis.symbol,
            strategy_name=self.name,
            direction=direction,
            generated_at=generated_at,
            reference_price=entry_price,
            stop_loss=levels.stop_loss,
            take_profit=levels.take_profit_3,
            valid_until=generated_at + timedelta(minutes=SIGNAL_VALIDITY_MINUTES),
            reason=(
                f"ApexFlow AI {decision.label} com {decision.confidence * 100:.1f}% "
                f"de confianca ({self._analysis.context.label}, alinhamento "
                f"{self._analysis.mtf.alignment_score:+.2f})."
            )[:1000],
            regime_required=self._analysis.context.state.value,
            confidence=decision.confidence * 100,
            features_used={
                "probability_buy": decision.probability_buy,
                "probability_sell": decision.probability_sell,
                "probability_abstain": decision.probability_abstain,
                "completeness": decision.completeness,
                "mtf_alignment": self._analysis.mtf.alignment_score,
            },
            model_version=decision.model_version,
        )
