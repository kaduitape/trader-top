"""AI Decision Engine (`app.apexflow.decision`).

Concentra os comportamentos que protegem dinheiro:

- a saida e SEMPRE uma de tres (COMPRAR/VENDER/NAO OPERAR);
- as probabilidades somam 1;
- um veto duro nunca e sobreposto pela probabilidade, nem por um modelo
  treinado com 99% de confianca;
- abaixo do limite de confianca a resposta e sempre NAO OPERAR.
"""

from __future__ import annotations

import pytest

from app.apexflow.config import ApexFlowConfig
from app.apexflow.context import MarketContextState
from app.apexflow.decision import (
    DecisionAction,
    ScorecardModel,
    collect_evidence,
    collect_vetoes,
    decide,
)
from app.apexflow.features import FEATURE_NAMES, build_feature_vector
from app.apexflow.liquidity import LiquidityState
from app.apexflow.momentum import MomentumState
from app.apexflow.spread import SpreadVerdict
from app.apexflow.tick_flow import TickDirection, compute_tick_flow
from app.apexflow.volatility import VolatilityState
from app.market.regimes import Trend
from app.market.sessions import evaluate_symbol_session
from tests.unit.apexflow.conftest import (
    NOW,
    POINT,
    flow_metrics,
    liquidity_reading,
    make_features,
    market_context,
    momentum_reading,
    mtf_view,
    spread_reading,
    volatility_reading,
    volume_reading,
)


def build_vector(**overrides):
    base = {
        "symbol": "EURUSD",
        "timeframe": "M5",
        "features": make_features(step=0.0006, amplitude=0.0008, count=200),
        "flow": flow_metrics(),
        "spread": spread_reading(),
        "volatility": volatility_reading(),
        "momentum": momentum_reading(),
        "liquidity": liquidity_reading(),
        "mtf": mtf_view(0.6),
        "session": evaluate_symbol_session("EURUSD", now=NOW),
        "volume": volume_reading(),
        "context": market_context(),
        "patterns": [],
        "now": NOW,
    }
    base.update(overrides)
    return build_feature_vector(**base)


def run(*, config=None, model=None, **overrides):
    parts = {
        "context": market_context(),
        "momentum": momentum_reading(),
        "mtf": mtf_view(0.6),
        "liquidity": liquidity_reading(),
        "spread": spread_reading(),
        "volatility": volatility_reading(),
        "flow": flow_metrics(),
    }
    parts.update(overrides)
    vector = build_vector(
        flow=parts["flow"],
        spread=parts["spread"],
        volatility=parts["volatility"],
        momentum=parts["momentum"],
        liquidity=parts["liquidity"],
        mtf=parts["mtf"],
        context=parts["context"],
    )
    return decide(
        vector,
        config=config or ApexFlowConfig(min_confidence=0.55),
        model=model,
        now=NOW,
        **parts,
    )


# --- Feature vector --------------------------------------------------------


def test_vector_has_stable_names_and_order() -> None:
    vector = build_vector()
    assert vector.names == FEATURE_NAMES
    assert len(vector.as_list()) == len(FEATURE_NAMES)
    assert list(vector.as_dict()) == list(FEATURE_NAMES)


def test_vector_distinguishes_missing_from_zero() -> None:
    no_flow = compute_tick_flow([], point=POINT, now=NOW)
    vector = build_vector(flow=no_flow)
    mask = vector.missing_mask()
    index = FEATURE_NAMES.index("flow_ticks_per_second")
    assert mask[index] is True
    assert vector.as_list()[index] == 0.0
    assert vector.completeness < 1.0


def test_vector_is_versioned() -> None:
    assert build_vector().version.startswith("apexflow-")


# --- Tres saidas, probabilidades coerentes --------------------------------


def test_probabilities_always_sum_to_one() -> None:
    for mtf_score in (-0.9, -0.3, 0.0, 0.3, 0.9):
        decision = run(mtf=mtf_view(mtf_score))
        total = (
            decision.probability_buy
            + decision.probability_sell
            + decision.probability_abstain
        )
        assert total == pytest.approx(1.0, abs=0.001)


def test_action_is_always_one_of_three() -> None:
    for context_state in MarketContextState:
        decision = run(context=market_context(context_state))
        assert decision.action in (
            DecisionAction.BUY,
            DecisionAction.SELL,
            DecisionAction.NO_TRADE,
        )


def test_bullish_evidence_leans_to_buy() -> None:
    decision = run(
        mtf=mtf_view(0.9),
        momentum=momentum_reading(direction=TickDirection.UP),
        flow=flow_metrics(direction=TickDirection.UP),
    )
    assert decision.probability_buy > decision.probability_sell


def test_bearish_evidence_leans_to_sell() -> None:
    decision = run(
        context=market_context(trend=Trend.DOWN),
        mtf=mtf_view(-0.9),
        momentum=momentum_reading(direction=TickDirection.DOWN),
        flow=flow_metrics(direction=TickDirection.DOWN),
    )
    assert decision.probability_sell > decision.probability_buy


def test_conflicting_evidence_raises_abstention_not_a_coin_flip() -> None:
    aligned = run(
        mtf=mtf_view(0.9),
        momentum=momentum_reading(direction=TickDirection.UP),
        flow=flow_metrics(direction=TickDirection.UP),
    )
    conflicted = run(
        mtf=mtf_view(0.9),
        momentum=momentum_reading(direction=TickDirection.DOWN),
        flow=flow_metrics(direction=TickDirection.DOWN),
    )
    assert conflicted.probability_abstain > aligned.probability_abstain


# --- Vetos duros -----------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"context": market_context(MarketContextState.EXPLOSIVE)},
        {"context": market_context(MarketContextState.DEAD)},
        {"spread": spread_reading(SpreadVerdict.TOO_WIDE)},
        {"volatility": volatility_reading(VolatilityState.INSUFFICIENT)},
        {"liquidity": liquidity_reading(LiquidityState.STOP_HUNT_ACTIVE)},
        {"flow": flow_metrics(tick_count=3, ticks_per_second=None)},
    ],
)
def test_each_hard_veto_forces_no_trade(override) -> None:
    decision = run(**override)
    assert decision.action == DecisionAction.NO_TRADE
    assert decision.vetoes


def test_veto_beats_a_maximally_confident_model() -> None:
    """A propriedade de seguranca mais importante do motor."""

    class AlwaysBuyModel:
        version = "test-always-buy"

        def predict(self, vector):
            return (0.99, 0.005, 0.005)

    decision = run(
        model=AlwaysBuyModel(),
        spread=spread_reading(SpreadVerdict.TOO_WIDE),
    )
    assert decision.action == DecisionAction.NO_TRADE
    assert decision.probability_buy == pytest.approx(0.99)
    assert decision.vetoes


def test_low_completeness_is_a_veto() -> None:
    config = ApexFlowConfig(min_confidence=0.55, min_feature_completeness=0.99)
    decision = run(config=config, flow=compute_tick_flow([], point=POINT, now=NOW))
    assert decision.action == DecisionAction.NO_TRADE
    assert any("sensores" in veto for veto in decision.vetoes)


# --- Limite de confianca ---------------------------------------------------


def test_confidence_below_threshold_never_trades() -> None:
    config = ApexFlowConfig(min_confidence=0.99)
    decision = run(config=config, mtf=mtf_view(0.9))
    assert decision.action == DecisionAction.NO_TRADE
    assert not decision.vetoes
    assert any("abaixo do minimo" in reason for reason in decision.reasons)


def test_confident_and_aligned_evidence_produces_an_entry() -> None:
    class ConfidentBuyModel:
        version = "test-confident-buy"

        def predict(self, vector):
            return (0.9, 0.05, 0.05)

    decision = run(
        model=ConfidentBuyModel(),
        config=ApexFlowConfig(min_confidence=0.80),
        mtf=mtf_view(0.9),
    )
    assert decision.action == DecisionAction.BUY
    assert decision.is_entry
    assert decision.confidence >= 0.80


def test_entry_requires_multi_timeframe_agreement() -> None:
    class ConfidentBuyModel:
        version = "test-confident-buy"

        def predict(self, vector):
            return (0.95, 0.03, 0.02)

    decision = run(
        model=ConfidentBuyModel(),
        config=ApexFlowConfig(min_confidence=0.80, min_mtf_alignment=0.5),
        mtf=mtf_view(-0.9),  # macro aponta para baixo, modelo quer comprar
    )
    assert decision.action == DecisionAction.NO_TRADE
    assert any("Alinhamento" in reason for reason in decision.reasons)


# --- Auditoria -------------------------------------------------------------


def test_decision_always_records_probabilities_and_versions() -> None:
    decision = run()
    assert decision.model_version
    assert decision.feature_version.startswith("apexflow-")
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.reasons


def test_evidence_is_explainable() -> None:
    evidence = collect_evidence(
        context=market_context(),
        momentum=momentum_reading(),
        mtf=mtf_view(0.6),
        liquidity=liquidity_reading(),
        flow=flow_metrics(),
        volatility=volatility_reading(),
    )
    assert evidence
    for item in evidence:
        assert item.rationale
        assert -1.0 <= item.direction <= 1.0
        assert item.weight >= 0.0


def test_scorecard_abstains_without_any_evidence() -> None:
    model = ScorecardModel([])
    buy, sell, abstain = model.predict(build_vector())
    assert abstain == pytest.approx(1.0)
    assert buy == 0.0 and sell == 0.0


def test_no_vetoes_on_a_healthy_market() -> None:
    vetoes = collect_vetoes(
        context=market_context(),
        spread=spread_reading(),
        volatility=volatility_reading(),
        liquidity=liquidity_reading(),
        flow=flow_metrics(),
        vector=build_vector(),
        config=ApexFlowConfig(min_feature_completeness=0.5),
    )
    assert vetoes == ()


def test_exhausted_momentum_flips_the_evidence_direction() -> None:
    evidence = collect_evidence(
        context=market_context(),
        momentum=momentum_reading(MomentumState.EXHAUSTED, direction=TickDirection.UP),
        mtf=mtf_view(0.6),
        liquidity=liquidity_reading(),
        flow=flow_metrics(),
        volatility=volatility_reading(),
    )
    exhaustion = next(item for item in evidence if item.name == "momentum_exhaustion")
    # Movimento de alta exausto favorece a VENDA, nao a continuidade.
    assert exhaustion.direction < 0
