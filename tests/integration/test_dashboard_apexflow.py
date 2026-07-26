"""Painel do ApexFlow AI: pagina, polling e configuracao."""

from __future__ import annotations

import pytest

from app.apexflow.config import ApexFlowConfig, load_apexflow_config, save_apexflow_config
from app.core.security import hash_password
from app.database.repositories.apexflow_decision_repository import (
    ApexFlowDecisionRepository,
)
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.user_repository import UserRepository
from app.execution.automation_settings import (
    TradingAutomationConfig,
    load_trading_automation_config,
    save_trading_automation_config,
)
from app.mt5.symbol_mapper import SymbolSpecification

SYMBOL = "XAUUSD"


@pytest.fixture(autouse=True)
def reset_state(db_session):
    save_apexflow_config(db_session, ApexFlowConfig())
    save_trading_automation_config(db_session, TradingAutomationConfig())
    db_session.commit()


@pytest.fixture
def logged_in(client, db_session, request):
    username = f"apex_{abs(hash(request.node.name)) % 10**8}"
    repo = UserRepository(db_session)
    role = repo.get_or_create_role("ADMIN")
    repo.create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password("Sup3rSecret!"),
        roles=[role],
    )
    db_session.commit()
    client.post(
        "/login",
        data={"username": username, "password": "Sup3rSecret!"},
        follow_redirects=False,
    )
    return client


def seed_symbol(db_session) -> int:
    symbol = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=SYMBOL,
            description="Ouro",
            digits=2,
            point=0.01,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100.0,
            spread=20,
            trade_mode=4,
            visible=True,
        )
    )
    db_session.commit()
    return symbol.id


def test_page_requires_authentication(client) -> None:
    response = client.get("/dashboard/apexflow", follow_redirects=False)
    assert response.status_code == 302


def test_page_renders_without_any_decision_yet(logged_in, db_session) -> None:
    response = logged_in.get("/dashboard/apexflow")
    assert response.status_code == 200
    assert "ApexFlow AI" in response.text
    assert "Nenhuma decis" in response.text


def test_status_endpoint_exposes_the_latest_decision(logged_in, db_session) -> None:
    symbol_id = seed_symbol(db_session)
    ApexFlowDecisionRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe="M5",
        decided_at=__import__("datetime").datetime(2026, 7, 22, 14, 0),
        action="NO_TRADE",
        probability_buy=0.20,
        probability_sell=0.10,
        probability_abstain=0.70,
        confidence=0.70,
        min_confidence=0.80,
        model_version="scorecard-1",
        feature_version="apexflow-1",
        completeness=0.95,
        context_state="TRENDING",
        spread_points=18.0,
        atr_points=120.0,
        ticks_per_second=2.5,
        mtf_alignment=0.45,
    )
    db_session.commit()

    payload = logged_in.get("/dashboard/apexflow/status").json()
    assert payload["action"] == "NO_TRADE"
    assert payload["probability_abstain"] == pytest.approx(0.70)
    assert payload["ticks_per_second"] == pytest.approx(2.5)
    assert payload["total_decisions"] == 1


def test_statistics_are_null_until_there_is_enough_sample(logged_in, db_session) -> None:
    seed_symbol(db_session)
    payload = logged_in.get("/dashboard/apexflow/status").json()
    assert payload["has_statistics"] is False
    assert payload["win_rate"] is None
    assert payload["profit_factor"] is None


def test_saving_parameters_switches_the_engine(logged_in, db_session) -> None:
    response = logged_in.post(
        "/dashboard/apexflow",
        data={
            "min_confidence": "0.85",
            "min_atr_points": "25",
            "max_spread_points": "40",
            "risk_reward_min": "2",
            "daily_profit_target_pct": "4",
            "max_drawdown_pct": "6",
            "tick_window_seconds": "180",
            "use_engine": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "saved=1" in response.headers["location"]

    config = load_apexflow_config(db_session)
    assert config.min_confidence == 0.85
    assert config.tick_window_seconds == 180
    assert load_trading_automation_config(db_session).engine == "apexflow"


def test_unchecking_the_engine_returns_to_the_playbook(logged_in, db_session) -> None:
    logged_in.post(
        "/dashboard/apexflow",
        data={
            "min_confidence": "0.85", "min_atr_points": "25", "max_spread_points": "40",
            "risk_reward_min": "2", "daily_profit_target_pct": "4",
            "max_drawdown_pct": "6", "tick_window_seconds": "180", "use_engine": "1",
        },
        follow_redirects=False,
    )
    logged_in.post(
        "/dashboard/apexflow",
        data={
            "min_confidence": "0.85", "min_atr_points": "25", "max_spread_points": "40",
            "risk_reward_min": "2", "daily_profit_target_pct": "4",
            "max_drawdown_pct": "6", "tick_window_seconds": "180", "use_engine": "",
        },
        follow_redirects=False,
    )
    assert load_trading_automation_config(db_session).engine == "playbook"


def test_switching_engine_never_enables_the_robot(logged_in, db_session) -> None:
    """Trocar de cerebro nao pode ser um atalho para ligar a automacao."""
    logged_in.post(
        "/dashboard/apexflow",
        data={
            "min_confidence": "0.85", "min_atr_points": "25", "max_spread_points": "40",
            "risk_reward_min": "2", "daily_profit_target_pct": "4",
            "max_drawdown_pct": "6", "tick_window_seconds": "180", "use_engine": "1",
        },
        follow_redirects=False,
    )
    assert not load_trading_automation_config(db_session).enabled


def test_out_of_range_parameters_are_rejected(logged_in, db_session) -> None:
    response = logged_in.post(
        "/dashboard/apexflow",
        data={
            "min_confidence": "0.10", "min_atr_points": "25", "max_spread_points": "40",
            "risk_reward_min": "2", "daily_profit_target_pct": "4",
            "max_drawdown_pct": "6", "tick_window_seconds": "180", "use_engine": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert load_apexflow_config(db_session).min_confidence == ApexFlowConfig().min_confidence


def test_config_change_is_audited(logged_in, db_session) -> None:
    from app.database.repositories.audit_log_repository import AuditLogRepository

    logged_in.post(
        "/dashboard/apexflow",
        data={
            "min_confidence": "0.85", "min_atr_points": "25", "max_spread_points": "40",
            "risk_reward_min": "2", "daily_profit_target_pct": "4",
            "max_drawdown_pct": "6", "tick_window_seconds": "180", "use_engine": "1",
        },
        follow_redirects=False,
    )
    actions = [entry.action for entry in AuditLogRepository(db_session).list_recent(limit=10)]
    assert "apexflow_config_change" in actions
