"""Testes do dashboard HTML (Fase 12). `client` (fixture de conftest.py)
e um `TestClient` real da aplicacao FastAPI — os cookies de sessao
persistem entre chamadas no mesmo teste, como um navegador real."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.google_oauth import GoogleIdentity
from app.core.security import hash_password
from app.database.repositories.audit_log_repository import AuditLogRepository
from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.paper_trade_repository import PaperTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.user_repository import UserRepository
from app.execution.order_state import OrderState
from app.mt5.symbol_mapper import SymbolSpecification
from app.mt5.sync_settings import load_sync_config


def _create_user(db_session, username: str, password: str) -> None:
    repo = UserRepository(db_session)
    role = repo.get_or_create_role("ADMIN")
    repo.create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password(password),
        roles=[role],
    )
    db_session.commit()


def _login(client, username: str, password: str):
    return client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )


def test_dashboard_redirects_to_login_when_unauthenticated(client) -> None:
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_dashboard_auth_disabled_bypasses_login(
    client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "dashboard_auth_disabled", True)

    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "dev" in response.text

    dev_user = UserRepository(db_session).get_by_username("dev")
    assert dev_user is not None
    assert {role.name for role in dev_user.roles} == {"ADMIN"}


def test_login_page_renders(client) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert "form" in response.text.lower()


def test_login_with_invalid_credentials_shows_error(client, db_session) -> None:
    _create_user(db_session, "dash_bad_login", "correct-password")

    response = _login(client, "dash_bad_login", "wrong-password")

    assert response.status_code == 401
    assert "invalid" in response.text.lower() or "invalidos" in response.text.lower()


def test_login_failure_is_audited(client, db_session) -> None:
    _create_user(db_session, "dash_audit_fail", "correct-password")

    _login(client, "dash_audit_fail", "wrong-password")

    entries = AuditLogRepository(db_session).list_recent(limit=20)
    assert any(e.action == "login" and e.result == "FAILURE" for e in entries)


def test_login_success_sets_cookie_and_redirects_to_dashboard(client, db_session) -> None:
    _create_user(db_session, "dash_good_login", "correct-password")

    response = _login(client, "dash_good_login", "correct-password")

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert client.cookies.get("access_token") is not None


def test_google_login_redirects_and_sets_flow_cookies(client, monkeypatch) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "client-secret")
    monkeypatch.setattr(
        settings, "google_oauth_redirect_uri", "http://localhost/auth/google/callback"
    )

    response = client.get("/auth/google", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/")
    assert client.cookies.get("google_oauth_state")
    assert client.cookies.get("google_oauth_nonce")


def test_google_callback_logs_in_existing_user(client, db_session, monkeypatch) -> None:
    from app.api.routes import web_auth
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "client-secret")
    monkeypatch.setattr(
        settings, "google_oauth_redirect_uri", "http://localhost/auth/google/callback"
    )
    _create_user(db_session, "google_user", "unused-password")
    user = UserRepository(db_session).get_by_username("google_user")
    assert user is not None
    user.email = "person@gmail.com"
    db_session.commit()
    monkeypatch.setattr(
        web_auth,
        "exchange_code_for_identity",
        lambda **_kwargs: GoogleIdentity(subject="google-sub", email="person@gmail.com"),
    )
    client.cookies.set("google_oauth_state", "expected-state")
    client.cookies.set("google_oauth_nonce", "expected-nonce")

    response = client.get(
        "/auth/google/callback?code=one-time-code&state=expected-state",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert client.cookies.get("access_token")


def test_google_callback_rejects_state_mismatch(client, monkeypatch) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "client-secret")
    monkeypatch.setattr(
        settings, "google_oauth_redirect_uri", "http://localhost/auth/google/callback"
    )
    client.cookies.set("google_oauth_state", "expected-state")
    client.cookies.set("google_oauth_nonce", "expected-nonce")

    response = client.get(
        "/auth/google/callback?code=one-time-code&state=wrong-state",
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert client.cookies.get("access_token") is None


def test_authenticated_dashboard_home_renders(client, db_session) -> None:
    from app.database.repositories.system_setting_repository import get_current_mode

    _create_user(db_session, "dash_home_user", "correct-password")
    _login(client, "dash_home_user", "correct-password")

    response = client.get("/dashboard")

    assert response.status_code == 200
    # O modo e um valor global compartilhado por toda a suite de testes
    # (outros arquivos de teste tambem o alteram) -- verifica que o valor
    # ATUAL aparece, em vez de assumir o padrao DISABLED.
    current_mode = get_current_mode(db_session).value
    assert current_mode in response.text


def test_dashboard_paper_trades_lists_recent_trades(client, db_session) -> None:
    _create_user(db_session, "dash_paper_user", "correct-password")

    spec = SymbolSpecification(
        name="DASH_PAPER_SYM",
        description="Test",
        digits=5,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=2,
        trade_mode=4,
        visible=True,
    )
    symbol = SymbolRepository(db_session).upsert_from_specification(spec)
    PaperTradeRepository(db_session).open_position(
        symbol_id=symbol.id,
        timeframe="M1",
        strategy_name="ema_crossover_baseline",
        model_version="rule-based",
        signal_id="sig-dash-1",
        direction="LONG",
        entry_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        entry_price=Decimal("1.10000000"),
        stop_loss=Decimal("1.09000000"),
        take_profit=Decimal("1.11000000"),
        volume=Decimal("0.01000000"),
    )
    db_session.commit()

    _login(client, "dash_paper_user", "correct-password")
    response = client.get("/dashboard/paper-trades")

    assert response.status_code == 200
    assert "DASH_PAPER_SYM" in response.text
    assert "LONG" in response.text


def test_dashboard_live_trades_lists_recent_trades(client, db_session) -> None:
    _create_user(db_session, "dash_live_user", "correct-password")

    spec = SymbolSpecification(
        name="DASH_LIVE_SYM",
        description="Test",
        digits=5,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=2,
        trade_mode=4,
        visible=True,
    )
    symbol = SymbolRepository(db_session).upsert_from_specification(spec)
    LiveTradeRepository(db_session).create(
        symbol_id=symbol.id,
        timeframe="M1",
        strategy_name="ema_crossover_baseline",
        model_version="rule-based",
        signal_id="sig-dash-2",
        direction="SHORT",
        order_state=OrderState.RISK_REJECTED,
        signal_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        rejection_reason="circuit breaker ativo",
    )
    db_session.commit()

    _login(client, "dash_live_user", "correct-password")
    response = client.get("/dashboard/live-trades")

    assert response.status_code == 200
    assert "DASH_LIVE_SYM" in response.text
    assert "circuit breaker ativo" in response.text


def test_dashboard_models_renders_when_registry_is_empty(
    client, db_session, tmp_path, monkeypatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ml_models_dir", str(tmp_path / "models"))
    _create_user(db_session, "dash_models_user", "correct-password")
    _login(client, "dash_models_user", "correct-password")

    response = client.get("/dashboard/models")

    assert response.status_code == 200
    assert "Nenhum modelo registrado" in response.text


def test_dashboard_audit_log_lists_entries(client, db_session) -> None:
    _create_user(db_session, "dash_audit_user", "correct-password")
    AuditLogRepository(db_session).record(action="test_action", detail="dashboard test entry")
    db_session.commit()

    _login(client, "dash_audit_user", "correct-password")
    response = client.get("/dashboard/audit-log")

    assert response.status_code == 200
    assert "test_action" in response.text
    assert "dashboard test entry" in response.text


def test_logout_clears_cookie_and_redirects(client, db_session) -> None:
    _create_user(db_session, "dash_logout_user", "correct-password")
    _login(client, "dash_logout_user", "correct-password")

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"

    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_dashboard_rejects_expired_or_tampered_cookie(client, db_session) -> None:
    _create_user(db_session, "dash_tamper_user", "correct-password")
    _login(client, "dash_tamper_user", "correct-password")

    client.cookies.set("access_token", "not-a-real-jwt")
    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_dashboard_drift_page_lists_events(client, db_session) -> None:
    from app.database.repositories.drift_event_repository import DriftEventRepository

    _create_user(db_session, "dash_drift_user", "correct-password")
    DriftEventRepository(db_session).record(
        drift_type="FEATURE",
        severity="CRITICAL",
        metric_name="rsi_14",
        current_value=0.31,
        baseline_value=0.0,
        threshold_value=0.25,
        detail="PSI=0.3100",
        model_version="20260101T000000000000",
    )
    db_session.commit()

    _login(client, "dash_drift_user", "correct-password")
    response = client.get("/dashboard/drift")

    assert response.status_code == 200
    assert "rsi_14" in response.text
    assert "CRITICAL" in response.text
    assert "PSI=0.3100" in response.text


def test_dashboard_home_shows_recent_drift_section(client, db_session) -> None:
    from app.database.repositories.drift_event_repository import DriftEventRepository

    _create_user(db_session, "dash_home_drift_user", "correct-password")
    DriftEventRepository(db_session).record(
        drift_type="DATA_FEED",
        severity="CRITICAL",
        metric_name="feed_age_seconds",
        current_value=999.0,
        detail="dados atrasados (999s)",
    )
    db_session.commit()

    _login(client, "dash_home_drift_user", "correct-password")
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "feed_age_seconds" in response.text


def _pick_valid_target(current):
    """`system_mode` e global/compartilhado por toda a suite (mesma razao
    documentada nas Fases 10/12/15) -- em vez de assumir um modo de
    partida fixo, escolhe uma transicao que `validate_transition` SEMPRE
    aceita a partir de qualquer `current` possivel: avancar um passo se
    ainda estiver em DISABLED, ou retroceder para DISABLED em qualquer
    outro caso (permitido a partir de todo estado ativo, e o unico
    destino permitido a partir de EMERGENCY_STOP)."""
    from app.core.enums import SystemMode

    if current == SystemMode.DISABLED:
        return SystemMode.DATA_ONLY
    return SystemMode.DISABLED


def test_dashboard_mode_redirects_to_login_when_unauthenticated(client) -> None:
    response = client.get("/dashboard/mode", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_dashboard_mode_page_renders_current_mode_and_options(client, db_session) -> None:
    from app.database.repositories.system_setting_repository import get_current_mode

    _create_user(db_session, "dash_mode_view_user", "correct-password")
    _login(client, "dash_mode_view_user", "correct-password")

    response = client.get("/dashboard/mode")

    import re

    from app.core.enums import SystemMode
    from app.core.system_mode import SystemModeError, validate_transition

    assert response.status_code == 200
    current = get_current_mode(db_session)
    assert current.value in response.text
    # A pagina so pode oferecer transicoes que o backend aceitaria: com o
    # modo REAL liberado, o que protege o operador nao e esconder REAL_*, e
    # sim nunca permitir pular degraus da escada.
    offered = re.findall(r'<option value="([A-Z_]+)">', response.text)
    assert offered
    for value in offered:
        try:
            validate_transition(current, SystemMode(value))
        except SystemModeError as exc:  # pragma: no cover - so falha se regredir
            raise AssertionError(f"{current.value} -> {value} nao e permitido") from exc


def test_dashboard_mode_change_wrong_confirmation_does_not_change_mode(client, db_session) -> None:
    from app.database.repositories.system_setting_repository import get_current_mode

    _create_user(db_session, "dash_mode_wrong_confirm", "correct-password")
    _login(client, "dash_mode_wrong_confirm", "correct-password")

    current = get_current_mode(db_session)
    target = _pick_valid_target(current)

    response = client.post(
        "/dashboard/mode",
        data={"target_mode": target.value, "confirm_text": "algo-errado", "reason": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert get_current_mode(db_session) == current


def test_dashboard_mode_change_success_updates_mode_and_audit_log(client, db_session) -> None:
    from app.database.repositories.audit_log_repository import AuditLogRepository
    from app.database.repositories.system_setting_repository import get_current_mode
    from app.database.repositories.user_repository import UserRepository

    _create_user(db_session, "dash_mode_success", "correct-password")
    _login(client, "dash_mode_success", "correct-password")

    current = get_current_mode(db_session)
    target = _pick_valid_target(current)

    response = client.post(
        "/dashboard/mode",
        data={
            "target_mode": target.value,
            "confirm_text": target.value.lower(),
            "reason": "teste automatizado",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/mode?changed_to={target.value}"
    assert get_current_mode(db_session) == target

    user = UserRepository(db_session).get_by_username("dash_mode_success")
    entries = AuditLogRepository(db_session).list_recent(limit=20)
    assert any(
        e.action == "system_mode_change"
        and e.user_id == user.id
        and "teste automatizado" in (e.detail or "")
        for e in entries
    )


def test_dashboard_mode_change_rejects_invalid_transition(client, db_session) -> None:
    """O formulario so oferece transicoes validas, mas o backend tem que
    recusar de qualquer forma -- o HTML nao e a unica linha de defesa.
    REAL_ENABLED nunca e alcancavel sem passar por REAL_LOCKED."""
    from app.database.repositories.system_setting_repository import get_current_mode

    _create_user(db_session, "dash_mode_invalid", "correct-password")
    _login(client, "dash_mode_invalid", "correct-password")

    current = get_current_mode(db_session)

    response = client.post(
        "/dashboard/mode",
        data={"target_mode": "REAL_ENABLED", "confirm_text": "REAL_ENABLED", "reason": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert get_current_mode(db_session) == current


def test_dashboard_market_data_shows_candle_and_tick_summary(client, db_session) -> None:
    _create_user(db_session, "dash_market_data_user", "correct-password")

    spec = SymbolSpecification(
        name="DASH_MARKET_SYM",
        description="Test",
        digits=5,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=2,
        trade_mode=4,
        visible=True,
    )
    symbol = SymbolRepository(db_session).upsert_from_specification(spec)
    from app.database.repositories.candle_repository import CandleRepository
    from app.mt5.market_data import RawCandle

    CandleRepository(db_session).bulk_upsert(
        symbol.id,
        "M1",
        [
            RawCandle(
                open_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
                open=1.1,
                high=1.2,
                low=1.0,
                close=1.15,
                tick_volume=10,
                spread=2,
                real_volume=0,
            )
        ],
    )
    db_session.commit()

    _login(client, "dash_market_data_user", "correct-password")
    response = client.get("/dashboard/market-data")

    assert response.status_code == 200
    assert "DASH_MARKET_SYM" in response.text
    assert "M1" in response.text


def test_dashboard_market_data_page_renders_when_empty(client, db_session) -> None:
    _create_user(db_session, "dash_market_data_empty_user", "correct-password")
    _login(client, "dash_market_data_empty_user", "correct-password")

    response = client.get("/dashboard/market-data")

    assert response.status_code == 200


def test_dashboard_analysis_redirects_to_login_when_unauthenticated(client) -> None:
    response = client.get("/dashboard/analysis", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_dashboard_analysis_lists_synced_symbols(client, db_session) -> None:
    _create_user(db_session, "dash_analysis_list_user", "correct-password")
    SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name="EURUSD.PRO",
            description="Euro vs US Dollar",
            digits=5,
            point=0.00001,
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
    _login(client, "dash_analysis_list_user", "correct-password")

    response = client.get("/dashboard/analysis")

    assert response.status_code == 200
    assert "Análise de moedas" in response.text
    assert "EURUSD.PRO" in response.text
    assert "Score mínimo" in response.text


def test_dashboard_markets_includes_xauusd_and_sync_status(client, db_session) -> None:
    _create_user(db_session, "dash_markets_user", "correct-password")
    SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name="XAUUSD.a",
            description="Gold vs US Dollar",
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
    _login(client, "dash_markets_user", "correct-password")

    response = client.get("/dashboard/markets")

    assert response.status_code == 200
    assert "Central de mercados" in response.text
    assert "XAU" in response.text
    assert "XAUUSD.a" in response.text
    assert "Pronto" in response.text
    assert "EURUSD" in response.text


def test_dashboard_markets_redirects_to_login_when_unauthenticated(client) -> None:
    response = client.get("/dashboard/markets", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_dashboard_mt5_renders_automatic_controls_and_xauusd(client, db_session) -> None:
    _create_user(db_session, "dash_mt5_user", "correct-password")
    _login(client, "dash_mt5_user", "correct-password")

    response = client.get("/dashboard/mt5")

    assert response.status_code == 200
    assert "Conexão MetaTrader 5" in response.text
    assert "Ativar automação" in response.text
    assert "XAU/USD" in response.text
    assert "Matriz automática" in response.text
    assert "Copiar comando" not in response.text


def test_dashboard_mt5_can_save_symbols_without_cli_commands(client, db_session) -> None:
    _create_user(db_session, "dash_mt5_config_user", "correct-password")
    _login(client, "dash_mt5_config_user", "correct-password")

    response = client.post(
        "/dashboard/mt5/config",
        data={
            "symbols": ["XAUUSD", "EURJPY"],
            "interval_seconds": "30",
            "candle_backfill_count": "3000",
            "collect_ticks": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    config = load_sync_config(db_session)
    assert config.symbols == ("XAUUSD", "EURJPY")
    assert config.interval_seconds == 30
    assert config.candle_backfill_count == 3000


def test_dashboard_mt5_start_action_enables_worker_plan(client, db_session) -> None:
    _create_user(db_session, "dash_mt5_start_user", "correct-password")
    _login(client, "dash_mt5_start_user", "correct-password")

    response = client.post(
        "/dashboard/mt5/action",
        data={"requested_action": "start"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    config = load_sync_config(db_session)
    assert config.enabled is True
    assert config.sync_request_id


def test_dashboard_mt5_installer_is_downloadable_launcher(client, db_session) -> None:
    _create_user(db_session, "dash_mt5_installer_user", "correct-password")
    _login(client, "dash_mt5_installer_user", "correct-password")

    response = client.get("/dashboard/mt5/installer")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "EncodedCommand" in response.text


def test_analysis_selector_shows_pending_catalog_pairs_without_enabling_them(
    client, db_session
) -> None:
    _create_user(db_session, "dash_analysis_catalog_user", "correct-password")
    _login(client, "dash_analysis_catalog_user", "correct-password")

    response = client.get("/dashboard/analysis")

    assert response.status_code == 200
    assert "USD/TRY" in response.text
    assert "sincronize no MT5" in response.text


def test_dashboard_analysis_returns_safe_do_not_operate_with_insufficient_data(
    client, db_session
) -> None:
    from app.database.repositories.candle_repository import CandleRepository
    from app.mt5.market_data import RawCandle

    _create_user(db_session, "dash_analysis_run_user", "correct-password")
    symbol = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name="GBPUSD.PRO",
            description="British Pound vs US Dollar",
            digits=5,
            point=0.00001,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100_000.0,
            spread=2,
            trade_mode=4,
            visible=True,
        )
    )
    CandleRepository(db_session).bulk_upsert(
        symbol.id,
        "M15",
        [
            RawCandle(
                open_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
                open=1.25,
                high=1.26,
                low=1.24,
                close=1.255,
                tick_volume=100,
                spread=2,
                real_volume=0,
            )
        ],
    )
    db_session.commit()
    _login(client, "dash_analysis_run_user", "correct-password")

    response = client.get("/dashboard/analysis?symbol=GBPUSD.PRO&timeframe=M15")

    assert response.status_code == 200
    assert "GBPUSD.PRO" in response.text
    assert "NÃO OPERAR" in response.text
    assert "Níveis não liberados" in response.text
    assert "SEM_DADOS" not in response.text


def test_dashboard_analysis_rejects_unknown_symbol(client, db_session) -> None:
    _create_user(db_session, "dash_analysis_unknown_user", "correct-password")
    _login(client, "dash_analysis_unknown_user", "correct-password")

    response = client.get("/dashboard/analysis?symbol=NOT-A-BROKER-SYMBOL")

    assert response.status_code == 404
    assert "Ativo indisponivel" in response.text


def test_dashboard_settings_aisa_redirects_to_login_when_unauthenticated(client) -> None:
    response = client.get("/dashboard/settings/aisa", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_dashboard_settings_aisa_shows_not_configured_by_default(client, db_session) -> None:
    _create_user(db_session, "dash_aisa_default_user", "correct-password")
    _login(client, "dash_aisa_default_user", "correct-password")

    response = client.get("/dashboard/settings/aisa")

    assert response.status_code == 200
    assert "NAO CONFIGURADA" in response.text


def test_dashboard_settings_aisa_save_persists_masked_key_and_audits_without_secret(
    client, db_session
) -> None:
    from app.database.repositories.audit_log_repository import AuditLogRepository
    from app.database.repositories.system_setting_repository import SystemSettingRepository
    from app.database.repositories.user_repository import UserRepository
    from app.news.factory import AISA_API_KEY_SETTING

    _create_user(db_session, "dash_aisa_save_user", "correct-password")
    _login(client, "dash_aisa_save_user", "correct-password")

    response = client.post(
        "/dashboard/settings/aisa",
        data={"api_key": "super-secret-token-1234", "api_base_url": "https://api.aisa.one"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/settings/aisa?saved=1"
    assert (
        SystemSettingRepository(db_session).get(AISA_API_KEY_SETTING) == "super-secret-token-1234"
    )

    view = client.get("/dashboard/settings/aisa")
    assert "CONFIGURADA" in view.text
    assert "1234" in view.text  # ultimos 4 caracteres, mascarado
    assert "super-secret-token-1234" not in view.text  # nunca reexibida em texto puro

    user = UserRepository(db_session).get_by_username("dash_aisa_save_user")
    entries = AuditLogRepository(db_session).list_recent(limit=20)
    matching = [e for e in entries if e.action == "aisa_settings_change" and e.user_id == user.id]
    assert len(matching) == 1
    assert "super-secret-token-1234" not in (matching[0].detail or "")


def test_dashboard_settings_aisa_remove_key_clears_it(client, db_session) -> None:
    from app.database.repositories.system_setting_repository import SystemSettingRepository
    from app.news.factory import AISA_API_KEY_SETTING

    _create_user(db_session, "dash_aisa_remove_user", "correct-password")
    _login(client, "dash_aisa_remove_user", "correct-password")

    client.post("/dashboard/settings/aisa", data={"api_key": "some-key-value"})
    assert SystemSettingRepository(db_session).get(AISA_API_KEY_SETTING) == "some-key-value"

    client.post("/dashboard/settings/aisa", data={"remove_key": "on"})
    assert SystemSettingRepository(db_session).get(AISA_API_KEY_SETTING) == ""

    view = client.get("/dashboard/settings/aisa")
    assert "NAO CONFIGURADA" in view.text


def test_dashboard_settings_aisa_save_with_no_fields_is_rejected(client, db_session) -> None:
    _create_user(db_session, "dash_aisa_empty_user", "correct-password")
    _login(client, "dash_aisa_empty_user", "correct-password")

    response = client.post("/dashboard/settings/aisa", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_dashboard_scanner_ranks_the_collected_symbols(client, db_session) -> None:
    """A tela de radar e somente leitura: mostra o ranking, nao opera."""
    _create_user(db_session, "dash_scanner_user", "correct-password")
    _login(client, "dash_scanner_user", "correct-password")

    response = client.get("/dashboard/scanner")

    assert response.status_code == 200
    assert "Radar de oportunidades" in response.text
    assert "Modo observação" in response.text
