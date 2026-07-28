"""Tela unica de operacao (`/dashboard/trading`) e o atalho embutido em
outras telas (`/dashboard/trading/quick`).

O ponto destes testes e que simplificar a tela NAO afrouxou nada: ligar
continua exigindo moeda sincronizada, conector online e — a guarda que mais
importa — conta do MetaTrader do mesmo tipo do modo escolhido, nas duas
direcoes (DEMO com conta real e REAL com conta demo sao recusados).
"""

from __future__ import annotations

import pytest

from app.core.enums import SystemMode
from app.core.security import hash_password
from app.database.repositories.audit_log_repository import AuditLogRepository
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
from app.mt5.sync_settings import (
    MT5SyncConfig,
    MT5SyncStatus,
    load_sync_config,
    save_sync_config,
    save_sync_status,
    utc_now_iso,
)

SYMBOL = "EURUSD"


def _reset_state(db_session) -> None:
    save_trading_automation_config(db_session, TradingAutomationConfig())
    save_autopilot_status(db_session, AutopilotStatus())
    save_sync_status(db_session, MT5SyncStatus())
    # Ligar o robo tambem entra no plano de coleta; sem restaurar o plano,
    # o proximo teste herdaria a sincronizacao ligada.
    save_sync_config(db_session, MT5SyncConfig())
    if get_current_mode(db_session) != SystemMode.DISABLED:
        set_mode(db_session, SystemMode.DISABLED, reason="reset de teste")
    db_session.commit()


@pytest.fixture(autouse=True)
def reset_trading_state(db_session):
    """A suite compartilha um unico banco em memoria; sem este reset o
    estado deixado aqui (modo REAL_ENABLED, automacao ligada, status
    publicado) decidiria o resultado de testes de outros arquivos — e o
    inverso tambem. Por isso limpa antes E depois."""
    _reset_state(db_session)
    yield
    _reset_state(db_session)


@pytest.fixture
def logged_in(client, db_session, request):
    username = f"trade_{abs(hash(request.node.name)) % 10**8}"
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


def connect_account(db_session, *, is_demo: bool) -> None:
    save_sync_status(
        db_session,
        MT5SyncStatus(
            state="ONLINE",
            worker_online=True,
            connected=True,
            heartbeat_at=utc_now_iso(),
            account_is_demo=is_demo,
        ),
    )
    db_session.commit()


def start(logged_in, *, symbol: str = SYMBOL, mode: str = "DEMO", action: str = "start"):
    return logged_in.post(
        "/dashboard/trading",
        data={"symbol": symbol, "mode": mode, "action": action},
        follow_redirects=False,
    )


def test_page_requires_authentication(client) -> None:
    response = client.get("/dashboard/trading", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_status_endpoint_requires_authentication(client) -> None:
    response = client.get("/dashboard/trading/status", follow_redirects=False)
    assert response.status_code in (302, 401)


def test_old_screens_redirect_to_the_single_one(logged_in) -> None:
    for old in ("/dashboard/autopilot", "/dashboard/settings/trading"):
        response = logged_in.get(old, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/dashboard/trading"


def test_page_offers_the_symbol_and_both_account_types(logged_in, db_session) -> None:
    seed_symbol(db_session)
    response = logged_in.get("/dashboard/trading")
    assert response.status_code == 200
    assert SYMBOL in response.text
    assert 'value="DEMO"' in response.text
    assert 'value="REAL"' in response.text


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
            volume_label="Forte",
        ),
    )
    db_session.commit()

    payload = logged_in.get("/dashboard/trading/status").json()
    # `symbol` vem da CONFIGURACAO (o que o operador escolheu) e
    # `broker_symbol` do que o worker de fato resolveu na corretora.
    assert payload["broker_symbol"] == SYMBOL
    assert payload["phase"] == AutopilotPhase.ANALYZING.value
    assert payload["phase_label"] == "Analisando a oportunidade"
    assert payload["playbook_label"] == "Tendencia com pullback"
    assert payload["mode"] == "DEMO"
    assert "activities" in payload


def test_stale_status_is_reported_instead_of_pretending_to_work(
    logged_in, db_session
) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)
    start(logged_in)

    payload = logged_in.get("/dashboard/trading/status").json()
    assert payload["enabled"] is True
    assert payload["status_fresh"] is False
    assert payload["working"] is False
    assert "aguardando" in payload["headline"].lower()


def test_starting_in_demo_walks_the_mode_ladder_and_plans_the_sync(
    logged_in, db_session
) -> None:
    seed_symbol(db_session, "EURGBP")
    connect_account(db_session, is_demo=True)

    response = start(logged_in, symbol="EURGBP")

    assert response.status_code == 303
    assert "saved=1" in response.headers["location"]
    config = load_trading_automation_config(db_session)
    assert config.enabled
    assert config.autopilot
    assert config.symbol == "EURGBP"
    assert config.mode == "DEMO"
    # O operador clicou uma vez; a escada de modos e percorrida pelo sistema.
    assert get_current_mode(db_session) == SystemMode.DEMO

    sync_config = load_sync_config(db_session)
    assert "EURGBP" in sync_config.symbols
    assert sync_config.enabled


def test_starting_in_real_reaches_real_enabled(logged_in, db_session) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=False)

    response = start(logged_in, mode="REAL")

    assert "saved=1" in response.headers["location"]
    assert load_trading_automation_config(db_session).mode == "REAL"
    assert get_current_mode(db_session) == SystemMode.REAL_ENABLED


def test_demo_mode_refuses_a_real_account(logged_in, db_session) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=False)

    response = start(logged_in, mode="DEMO")

    assert "error=" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled
    assert get_current_mode(db_session) == SystemMode.DISABLED


def test_real_mode_refuses_a_demo_account(logged_in, db_session) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)

    response = start(logged_in, mode="REAL")

    assert "error=" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled
    assert get_current_mode(db_session) == SystemMode.DISABLED


def test_starting_requires_the_connector_online(logged_in, db_session) -> None:
    seed_symbol(db_session)

    response = start(logged_in)

    assert "error=" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled


def test_unknown_symbol_is_rejected(logged_in, db_session) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)

    response = start(logged_in, symbol="USDTRY")

    assert "error=" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled


def test_unknown_mode_is_rejected(logged_in, db_session) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)

    response = start(logged_in, mode="TALVEZ")

    assert "error=" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled


def test_stopping_always_works_even_with_the_connector_offline(
    logged_in, db_session
) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)
    start(logged_in)
    save_sync_status(db_session, MT5SyncStatus())
    db_session.commit()

    response = start(logged_in, action="stop")

    assert "saved=1" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled


def test_start_and_stop_are_audited(logged_in, db_session) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)
    start(logged_in)
    start(logged_in, action="stop")

    actions = [entry.action for entry in AuditLogRepository(db_session).list_recent(limit=10)]
    assert "trading_start" in actions
    assert "trading_stop" in actions


def test_risk_limits_can_be_adjusted_on_the_same_screen(logged_in, db_session) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)

    response = logged_in.post(
        "/dashboard/trading",
        data={
            "symbol": SYMBOL,
            "mode": "DEMO",
            "action": "start",
            "analysis_threshold": "85",
            "risk_per_trade_pct": "0.5",
            "max_spread_points": "40",
        },
        follow_redirects=False,
    )

    assert "saved=1" in response.headers["location"]
    config = load_trading_automation_config(db_session)
    assert config.analysis_threshold == 85.0
    assert config.risk_per_trade_pct == 0.5
    assert config.max_spread_points == 40.0


def test_unsafe_risk_limit_is_rejected(logged_in, db_session) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)

    response = logged_in.post(
        "/dashboard/trading",
        data={
            "symbol": SYMBOL,
            "mode": "DEMO",
            "action": "start",
            "risk_per_trade_pct": "2",
        },
        follow_redirects=False,
    )

    assert "error=" in response.headers["location"]
    assert not load_trading_automation_config(db_session).enabled


def test_quick_start_returns_to_the_screen_it_was_clicked_from(
    logged_in, db_session
) -> None:
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)

    response = logged_in.post(
        "/dashboard/trading/quick",
        data={
            "symbol": SYMBOL,
            "mode": "DEMO",
            "action": "start",
            "origin": "/dashboard/market-data",
        },
        follow_redirects=False,
    )

    assert response.headers["location"] == "/dashboard/market-data?saved=1"
    assert load_trading_automation_config(db_session).enabled


def test_quick_start_ignores_a_foreign_origin(logged_in, db_session) -> None:
    """`origin` vindo do formulario nunca pode virar redirecionamento aberto."""
    seed_symbol(db_session)
    connect_account(db_session, is_demo=True)

    response = logged_in.post(
        "/dashboard/trading/quick",
        data={
            "symbol": SYMBOL,
            "mode": "DEMO",
            "action": "start",
            "origin": "https://evil.example.com",
        },
        follow_redirects=False,
    )

    assert response.headers["location"] == "/dashboard/trading?saved=1"


def test_market_data_page_offers_the_start_button(logged_in, db_session) -> None:
    seed_symbol(db_session)
    response = logged_in.get("/dashboard/market-data")
    assert response.status_code == 200
    assert "/dashboard/trading/quick" in response.text


def test_analysis_page_offers_the_start_button(logged_in, db_session) -> None:
    seed_symbol(db_session)
    response = logged_in.get("/dashboard/analysis")
    assert response.status_code == 200
    assert "/dashboard/trading/quick" in response.text
