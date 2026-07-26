"""Learning Engine: persistencia de decisoes e metricas de desempenho."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.apexflow.config import (
    ApexFlowConfig,
    load_apexflow_config,
    save_apexflow_config,
)
from app.apexflow.decision import decide
from app.apexflow.features import build_feature_vector
from app.apexflow.journal import load_feature_vector, record_decision, record_outcome
from app.database.repositories.apexflow_decision_repository import (
    MIN_TRADES_FOR_STATISTICS,
    ApexFlowDecisionRepository,
)
from app.database.repositories.symbol_repository import SymbolRepository
from app.market.sessions import evaluate_symbol_session
from app.mt5.symbol_mapper import SymbolSpecification
from tests.unit.apexflow.conftest import (
    NOW,
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


def seed_symbol(db_session, name: str) -> int:
    symbol = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=name,
            description="Test",
            digits=5,
            point=0.0001,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100_000.0,
            spread=2,
            trade_mode=4,
            visible=True,
        )
    )
    db_session.flush()
    return symbol.id


def make_decision_and_vector():
    vector = build_feature_vector(
        symbol="EURUSD",
        timeframe="M5",
        features=make_features(step=0.0006, amplitude=0.0008, count=200),
        flow=flow_metrics(),
        spread=spread_reading(),
        volatility=volatility_reading(),
        momentum=momentum_reading(),
        liquidity=liquidity_reading(),
        mtf=mtf_view(0.6),
        session=evaluate_symbol_session("EURUSD", now=NOW),
        volume=volume_reading(),
        context=market_context(),
        patterns=[],
        now=NOW,
    )
    decision = decide(
        vector,
        context=market_context(),
        momentum=momentum_reading(),
        mtf=mtf_view(0.6),
        liquidity=liquidity_reading(),
        spread=spread_reading(),
        volatility=volatility_reading(),
        flow=flow_metrics(),
        config=ApexFlowConfig(min_confidence=0.55),
        now=NOW,
    )
    return decision, vector


# --- Configuracao ----------------------------------------------------------


def test_config_defaults_to_eighty_percent_confidence() -> None:
    assert ApexFlowConfig().min_confidence == 0.80


def test_config_round_trip(db_session) -> None:
    save_apexflow_config(db_session, ApexFlowConfig(enabled=True, min_confidence=0.85))
    loaded = load_apexflow_config(db_session)
    assert loaded.enabled
    assert loaded.min_confidence == 0.85


def test_out_of_range_values_fall_back_to_defaults(db_session) -> None:
    from app.database.repositories.system_setting_repository import SystemSettingRepository

    SystemSettingRepository(db_session).set(
        "apexflow_config", json.dumps({"min_confidence": 5.0, "max_drawdown_pct": -3})
    )
    loaded = load_apexflow_config(db_session)
    assert loaded.min_confidence == ApexFlowConfig().min_confidence
    assert loaded.max_drawdown_pct == ApexFlowConfig().max_drawdown_pct


# --- Persistencia ----------------------------------------------------------


def test_decision_is_recorded_with_the_full_feature_vector(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_JOURNAL_1")
    decision, vector = make_decision_and_vector()
    record = record_decision(
        db_session,
        decision,
        vector,
        symbol_id=symbol_id,
        timeframe="M5",
        context=market_context(),
        session_state=evaluate_symbol_session("EURUSD", now=NOW),
        volume=volume_reading(),
    )
    assert record.id is not None
    assert record.action == decision.action.value
    assert float(record.confidence) == pytest.approx(decision.confidence)
    stored = load_feature_vector(record)
    assert set(stored) == set(vector.as_dict())


def test_abstentions_are_recorded_too(db_session) -> None:
    """Sem as abstencoes o historico ficaria enviesado e seria impossivel
    avaliar depois se o robo deixou passar boas oportunidades."""
    symbol_id = seed_symbol(db_session, "EURUSD_JOURNAL_2")
    decision, vector = make_decision_and_vector()
    record_decision(
        db_session, decision, vector, symbol_id=symbol_id, timeframe="M5",
        context=market_context(),
    )
    records = ApexFlowDecisionRepository(db_session).list_recent(symbol_id=symbol_id)
    assert len(records) == 1
    assert records[0].action in ("BUY", "SELL", "NO_TRADE")


def test_outcome_attaches_to_the_originating_decision(db_session) -> None:
    from app.database.repositories.live_trade_repository import LiveTradeRepository
    from app.execution.order_state import OrderState

    symbol_id = seed_symbol(db_session, "EURUSD_JOURNAL_3")
    trade = LiveTradeRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe="M5",
        strategy_name="apexflow",
        model_version="test",
        signal_id="sig-1",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=datetime(2026, 7, 22, 14, 0),
    )
    decision, vector = make_decision_and_vector()
    record_decision(
        db_session, decision, vector, symbol_id=symbol_id, timeframe="M5",
        context=market_context(), live_trade_id=trade.id,
    )

    assert record_outcome(db_session, live_trade_id=trade.id, net_pnl=42.5, r_multiple=2.1)
    record = ApexFlowDecisionRepository(db_session).get_by_live_trade(trade.id)
    assert record is not None
    assert float(record.result_net_pnl) == pytest.approx(42.5)
    assert float(record.result_r_multiple) == pytest.approx(2.1)


def test_outcome_for_an_unknown_trade_reports_false_instead_of_raising(db_session) -> None:
    assert not record_outcome(db_session, live_trade_id=999_999, net_pnl=1.0)


def test_missing_vector_returns_empty_not_zeros(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_JOURNAL_4")
    record = ApexFlowDecisionRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe="M5",
        decided_at=NOW,
        action="NO_TRADE",
        probability_buy=0.1,
        probability_sell=0.1,
        probability_abstain=0.8,
        confidence=0.8,
        min_confidence=0.8,
        model_version="scorecard-1",
        feature_version="apexflow-1",
        completeness=1.0,
        context_state="TRENDING",
    )
    assert load_feature_vector(record) == {}


# --- Metricas de desempenho ------------------------------------------------


def add_closed(db_session, symbol_id: int, pnl: float, *, offset: int) -> None:
    repository = ApexFlowDecisionRepository(db_session)
    record = repository.create(
        symbol_id=symbol_id,
        timeframe="M5",
        decided_at=NOW + timedelta(minutes=offset),
        action="BUY",
        probability_buy=0.9,
        probability_sell=0.05,
        probability_abstain=0.05,
        confidence=0.9,
        min_confidence=0.8,
        model_version="scorecard-1",
        feature_version="apexflow-1",
        completeness=1.0,
        context_state="TRENDING",
    )
    repository.attach_result(record, net_pnl=pnl)


def test_statistics_are_withheld_below_the_minimum_sample(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_PERF_1")
    add_closed(db_session, symbol_id, 10.0, offset=1)
    summary = ApexFlowDecisionRepository(db_session).performance(symbol_id=symbol_id)
    assert summary.closed_trades == 1
    assert not summary.has_statistics
    assert summary.win_rate is None
    assert summary.profit_factor is None
    assert summary.expectancy is None


def test_statistics_appear_once_there_is_enough_sample(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_PERF_2")
    for index in range(MIN_TRADES_FOR_STATISTICS):
        add_closed(db_session, symbol_id, 20.0 if index % 2 == 0 else -10.0, offset=index)
    summary = ApexFlowDecisionRepository(db_session).performance(symbol_id=symbol_id)
    assert summary.has_statistics
    assert summary.win_rate == pytest.approx(3 / 5)
    assert summary.profit_factor == pytest.approx(60 / 20)
    assert summary.expectancy == pytest.approx(40 / 5)
    assert summary.net_pnl == pytest.approx(40.0)


def test_abstention_rate_is_reported(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_PERF_3")
    decision, vector = make_decision_and_vector()
    # Limite de confianca inalcancavel: forca NAO OPERAR sem depender do
    # resultado numerico do scorecard.
    abstained = decide(
        vector,
        context=market_context(),
        momentum=momentum_reading(),
        mtf=mtf_view(0.6),
        liquidity=liquidity_reading(),
        spread=spread_reading(),
        volatility=volatility_reading(),
        flow=flow_metrics(),
        config=ApexFlowConfig(min_confidence=0.99),
        now=NOW,
    )
    assert abstained.action.value == "NO_TRADE"
    for _ in range(3):
        record_decision(
            db_session, abstained, vector, symbol_id=symbol_id, timeframe="M5",
            context=market_context(),
        )
    add_closed(db_session, symbol_id, 10.0, offset=50)
    summary = ApexFlowDecisionRepository(db_session).performance(symbol_id=symbol_id)
    assert summary.total_decisions == 4
    assert summary.abstention_rate is not None
    assert 0.0 < summary.abstention_rate < 1.0


def test_count_since_filters_by_time(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_PERF_4")
    add_closed(db_session, symbol_id, 10.0, offset=0)
    add_closed(db_session, symbol_id, 10.0, offset=120)
    repository = ApexFlowDecisionRepository(db_session)
    assert repository.count_since(since=NOW - timedelta(days=1), symbol_id=symbol_id) == 2
    assert (
        repository.count_since(since=NOW + timedelta(minutes=60), symbol_id=symbol_id) == 1
    )


def test_decided_at_accepts_timezone_aware_datetimes(db_session) -> None:
    symbol_id = seed_symbol(db_session, "EURUSD_PERF_5")
    decision, vector = make_decision_and_vector()
    record = record_decision(
        db_session, decision, vector, symbol_id=symbol_id, timeframe="M5",
        context=market_context(),
    )
    assert record.decided_at is not None
    assert decision.generated_at.tzinfo is UTC or decision.generated_at.tzinfo is not None
