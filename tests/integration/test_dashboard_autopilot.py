"""Pagina e endpoint de status do piloto automatico.

Cobre em especial que a tela simplificada NAO e um caminho mais permissivo
para ligar o robo: os mesmos portoes de `/dashboard/settings/trading`
(confirmacao digitada, modo DEMO, worker/conta demo) continuam valendo.
"""

from __future__ import annotations

import pytest

from app.core.enums import SystemMode
from app.core.security import hash_password
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.system_setting_repository import get_current_mode, set_mode
from app.database.repositories.user_repository import UserRepository
from app.execution.automation_settings import (
    TradingAutomationConfig,
    load_trading_automation_config,
    save_trading_automation_config,
)
from app.execution.autopilot_status import (
    AutopilotPhase,
    AutopilotStatus,
    save_autopilot_status,
)
from app.mt5.symbol_mapper import SymbolSpecification
from app.mt5.sync_settings import MT5SyncStatus, load_sync_config, save_sync_status, utc_now_iso

SYMBOL = "EURUSD"


@pytest.fixture(autouse=True)
def reset_autopilot_state(db_session):
    """A suite compartilha um unico banco em memoria; sem este reset o
    estado deixado por um teste anterior (modo DEMO, automacao ligada,
    status publicado) decidiria o resultado do proximo."""
    save_trading_automation_config(db_session, TradingAutomationConfig())
    save_autopilot_status(db_session, AutopilotStatus())
    save_sync_status(db_session, MT5SyncStatus())
    if get_current_mode(db_session) != SystemMode.DISABLED:
        set_mode(db_session, SystemMode.DISABLED, reason="reset de teste")
    db_session.commit()


@pytest.fixture
def logged_in(client, db_session, request):
    # A suite compartilha um unico banco em memoria entre os testes, entao
    # cada teste precisa do proprio usuario (username/email sao unicos).
    username = f"pilot_{abs(hash(request.node.name)) % 10**8}"
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


def seed_symbol(db_session, name: str = SYMBOL) -> None:
    SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=name,
            description="Euro vs Dollar",
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
    db_session.commit()


def make_ready(db_session) -> None:
    """Modo DEMO + worker online com conta demo — tudo que o backend exige."""
    for target in (
        SystemMode.DATA_ONLY,
        SystemMode.BACKTEST,
        SystemMode.REPLAY,
        SystemMode.PAPER,
        SystemMode.DEMO,
    ):
        set_mode(db_session, target, reason="test")
    save_sync_status(
        db_session,
        MT5SyncStatus(
            state="ONLINE",
            worker_online=True,
            connected=True,
            heartbeat_at=utc_now_iso(),
            account_is_demo=True,
        ),
    )
    db_session.commit()


def test_page_requires_authentication(client) -> None:
    response = client.get("/dashboard/autopilot", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_status_endpoint_requires_authentication(client) -> None:
    response = client.get("/dashboard/autopilot/status", follow_redirects=False)
    assert response.status_code in (302, 401)


def test_page_lists_available_symbols(logged_in, db_session) -> None:
    seed_symbol(db_session)
    response = logged_in.get("/dashboard/autopilot")
    assert response.status_code == 200
    assert SYMBOL in response.text
    assert "Piloto autom" in response.text


def test_status_endpoint_returns_live_payload(logged_in, db_session) -> None:
    seed_symbol(db_session)
    save_autopilot_status(
        db_session,
        AutopilotStatus(
            enabled=True,
            phase=AutopilotPhase.ANALYZING.value,
            headline="Analisando EURUSD",
            symbol=SYMBOL,
            broker_symbol=SYMBOL,
            playbook_label="Tendencia com pullback",
            timeframe="M15",
            analysis_score=88.0,
            session_label="Melhor horario para este par",
            volume_label="Forte",
        ),
    )
    db_session.commit()

    payload = logged_in.get("/dashboard/autopilot/status").json()
    # `symbol` vem da CONFIGURACAO (o que o operador escolheu) e
    # `broker_symbol` do que o worker de fato resolveu na corretora — os
    # dois campos existem justamente para nao se confundirem.
    assert payload["broker_symbol"] == SYMBOL
    assert payload["phase"] == AutopilotPhase.ANALYZING.value
    assert payload["phase_label"] == "Analisando a oportunidade"
    assert payload["playbook_label"] == "Tendencia com pullback"
    assert "activities" in payload


def test_stale_status_is_reported_instead_of_pretending_to_work(
    logged_in, db_session
) -> None:
    seed_symbol(db_session)
    make_ready(db_session)
    logged_in.post(
        "/dashboard/autopilot",
        data={"symbol": SYMBOL, "enabled": "1", "confirm_text": "DEMO"},
        follow_redirects=False,
    )
    payload = logged_in.get("/dashboard/autopilot/status").json()
    assert payload["enabled"] is True
    assert payload["status_fresh"] is False
    assert payload["working"] is False
    assert "aguardando" in payload["headline"].lower()


def test_enabling_requires_typed_confirmation(logged_in, db_session) -> None:
    seed_symbol(db_session)
    make_ready(db_session)
    response = logged_in.post(
        "/dashboard/autopilot",
        data={"symbol": SYMBOL, "enabled": "1", "confirm_text": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled


def test_enabling_requires_demo_mode(logged_in, db_session) -> None:
    seed_symbol(db_session)
    response = logged_in.post(
        "/dashboard/autopilot",
        data={"symbol": SYMBOL, "enabled": "1", "confirm_text": "DEMO"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled


def test_unknown_symbol_is_rejected(logged_in, db_session) -> None:
    seed_symbol(db_session)
    make_ready(db_session)
    response = logged_in.post(
        "/dashboard/autopilot",
        data={"symbol": "USDTRY", "enabled": "", "confirm_text": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]


def test_enabling_activates_autopilot_and_adds_symbol_to_sync_plan(
    logged_in, db_session
) -> None:
    seed_symbol(db_session, "EURGBP")
    make_ready(db_session)
    response = logged_in.post(
        "/dashboard/autopilot",
        data={"symbol": "EURGBP", "enabled": "1", "confirm_text": "DEMO"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "saved=1" in response.headers["location"]

    config = load_trading_automation_config(db_session)
    assert config.enabled
    assert config.autopilot
    assert config.symbol == "EURGBP"

    sync_config = load_sync_config(db_session)
    assert "EURGBP" in sync_config.symbols
    assert sync_config.enabled


def test_disabling_never_requires_confirmation(logged_in, db_session) -> None:
    seed_symbol(db_session)
    make_ready(db_session)
    logged_in.post(
        "/dashboard/autopilot",
        data={"symbol": SYMBOL, "enabled": "1", "confirm_text": "DEMO"},
        follow_redirects=False,
    )
    response = logged_in.post(
        "/dashboard/autopilot",
        data={"symbol": SYMBOL, "enabled": "", "confirm_text": ""},
        follow_redirects=False,
    )
    assert "saved=1" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled


def test_toggle_is_audited(logged_in, db_session) -> None:
    from app.database.repositories.audit_log_repository import AuditLogRepository

    seed_symbol(db_session)
    make_ready(db_session)
    logged_in.post(
        "/dashboard/autopilot",
        data={"symbol": SYMBOL, "enabled": "1", "confirm_text": "DEMO"},
        follow_redirects=False,
    )
    actions = [entry.action for entry in AuditLogRepository(db_session).list_recent(limit=10)]
    assert "autopilot_toggle" in actions
