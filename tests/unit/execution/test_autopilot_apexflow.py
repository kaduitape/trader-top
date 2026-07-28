"""Piloto automatico com o ApexFlow AI no comando (`engine="apexflow"`).

Verifica o que muda e, principalmente, o que NAO muda: o cerebro da decisao
e outro, mas os portoes de risco, a conta demo e o registro de auditoria
continuam sendo exatamente os mesmos.
"""

from __future__ import annotations

import pytest

from app.apexflow.config import ApexFlowConfig, save_apexflow_config
from app.database.repositories.apexflow_decision_repository import (
    ApexFlowDecisionRepository,
)
from app.execution.automation_settings import ENGINE_APEXFLOW, TradingAutomationConfig
from app.execution.autopilot_status import AutopilotPhase
from tests.unit.execution.test_autopilot import (  # noqa: F401  (fixture reset_mode)
    DEMO_ACCOUNT,
    NOW,
    REAL_ACCOUNT,
    SYMBOL,
    enable_demo,
    make_client,
    reset_mode,
    run,
    seed_candles,
    seed_full_market,
    seed_symbol,
)


def apexflow_config(**overrides) -> TradingAutomationConfig:
    fields = {
        "enabled": True,
        "symbol": SYMBOL,
        "engine": ENGINE_APEXFLOW,
        "timeframe": "M5",
        **overrides,
    }
    return TradingAutomationConfig(**fields)


def seed_apexflow_market(db_session):
    """Candles nos quatro timeframes de papel do ApexFlow (H1 macro ate M1)."""
    symbol_id = seed_symbol(db_session)
    for timeframe, minutes in (("H1", 60), ("M15", 15), ("M5", 5), ("M1", 1)):
        seed_candles(
            db_session,
            symbol_id,
            timeframe,
            count=300,
            minutes=minutes,
            end=NOW,
            trend_step=0.00005,
        )
    return symbol_id


def test_engine_selection_defaults_to_playbook() -> None:
    assert TradingAutomationConfig().engine == "playbook"


def test_apexflow_cycle_runs_and_records_the_decision(db_session) -> None:
    symbol_id = seed_apexflow_market(db_session)
    enable_demo(db_session)
    save_apexflow_config(db_session, ApexFlowConfig(enabled=True, min_atr_points=1.0))

    result, status = run(db_session, config=apexflow_config())

    assert result.ran
    # Sem ticks sinteticos o fluxo nao pode ser confirmado: o motor se
    # abstem, que e o comportamento correto (nunca forcar entrada).
    assert result.phase == AutopilotPhase.STANDING_ASIDE
    assert "ApexFlow" in status.playbook_label

    records = ApexFlowDecisionRepository(db_session).list_recent(symbol_id=symbol_id)
    assert len(records) == 1
    assert records[0].action == "NO_TRADE"
    assert records[0].feature_vector  # vetor completo guardado para auditoria


def test_abstention_is_recorded_with_probabilities(db_session) -> None:
    symbol_id = seed_apexflow_market(db_session)
    enable_demo(db_session)
    save_apexflow_config(db_session, ApexFlowConfig(enabled=True, min_atr_points=1.0))

    run(db_session, config=apexflow_config())

    record = ApexFlowDecisionRepository(db_session).list_recent(symbol_id=symbol_id)[0]
    total = (
        float(record.probability_buy)
        + float(record.probability_sell)
        + float(record.probability_abstain)
    )
    assert total == pytest.approx(1.0, abs=0.001)
    assert record.model_version
    assert record.feature_version.startswith("apexflow-")


def test_apexflow_never_sends_an_order_on_a_real_account(db_session) -> None:
    seed_apexflow_market(db_session)
    enable_demo(db_session)
    save_apexflow_config(db_session, ApexFlowConfig(enabled=True, min_atr_points=1.0))

    result, _ = run(db_session, config=apexflow_config(), account=REAL_ACCOUNT)

    assert result.phase == AutopilotPhase.BLOCKED
    assert "modo DEMO com conta REAL" in (result.blocking_error or "")


def test_apexflow_requires_demo_mode(db_session) -> None:
    seed_apexflow_market(db_session)
    save_apexflow_config(db_session, ApexFlowConfig(enabled=True))

    result, _ = run(db_session, config=apexflow_config())

    assert result.phase == AutopilotPhase.BLOCKED
    assert "DEMO" in (result.blocking_error or "")


def test_apexflow_sends_no_order_without_full_confluence(db_session) -> None:
    seed_apexflow_market(db_session)
    enable_demo(db_session)
    save_apexflow_config(db_session, ApexFlowConfig(enabled=True, min_atr_points=1.0))

    client = make_client()
    from app.execution.autopilot import run_autopilot_cycle
    from tests.unit.execution.test_autopilot_status import publisher_for

    run_autopilot_cycle(
        db_session,
        client,
        config=apexflow_config(),
        account=DEMO_ACCOUNT,
        publisher=publisher_for(db_session),
        available_symbols=[SYMBOL],
        now=NOW,
    )
    assert client.order_send_calls == []


def test_h1_configuration_falls_back_to_an_entry_timeframe(db_session) -> None:
    """H1 e timeframe de contexto: configurar entrada nele nao pode derrubar
    o ciclo, cai para um timeframe de entrada valido."""
    seed_apexflow_market(db_session)
    enable_demo(db_session)
    save_apexflow_config(db_session, ApexFlowConfig(enabled=True, min_atr_points=1.0))

    _, status = run(db_session, config=apexflow_config(timeframe="H1"))

    assert status.timeframe in ("M1", "M5", "M15")
