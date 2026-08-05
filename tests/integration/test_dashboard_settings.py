"""Configuracao pelo painel, sem linha de comando.

Estes testes cobrem o que a CLI fazia e a web nao fazia: gravar amostra do
radar, ligar o modo observacao, e ajustar teto/cache da API paga. O criterio
e sempre o mesmo — se para usar uma funcao o operador precisa abrir um
terminal, a funcao nao esta pronta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.database.repositories.user_repository import UserRepository
from app.market.scan_journal import SCAN_JOURNAL_SETTING, load_observations
from app.market.scan_settings import (
    INTERVAL_MAX_MINUTES,
    SCAN_OBSERVATION_SETTING,
    ObservationConfig,
    load_observation_config,
    save_observation_config,
)
from app.mt5.market_data import RawCandle
from app.mt5.symbol_mapper import SymbolSpecification
from app.news.api_settings import (
    BUDGET_LIMIT_SETTING,
    CACHE_TTL_SETTING,
    load_api_settings,
)

SYMBOL = "EURUSD"


def _reset(db_session) -> None:
    repo = SystemSettingRepository(db_session)
    repo.set(SCAN_JOURNAL_SETTING, "")
    repo.set(SCAN_OBSERVATION_SETTING, "")
    repo.set(BUDGET_LIMIT_SETTING, "")
    repo.set(CACHE_TTL_SETTING, "")
    db_session.commit()


@pytest.fixture(autouse=True)
def reset_state(db_session):
    _reset(db_session)
    yield
    _reset(db_session)


@pytest.fixture
def logged_in(client, db_session, request):
    username = f"cfg_{abs(hash(request.node.name)) % 10**8}"
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


def seed_tradable_symbol(db_session, name: str = SYMBOL) -> None:
    """Simbolo com candles suficientes para o scanner aprovar algo.

    Sem isso a varredura nao teria candidato e o teste de gravacao mediria o
    caminho vazio achando que mediu o cheio.
    """
    symbol = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=name,
            description="Euro vs Dollar",
            digits=5,
            point=0.00001,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100_000.0,
            spread=8,
            trade_mode=4,
            visible=True,
        )
    )
    agora = datetime.now(UTC)
    preco = 1.10
    candles = []
    for i in range(300):
        preco *= 1.0001
        candles.append(
            RawCandle(
                open_time=agora - timedelta(minutes=15 * (300 - i)),
                open=preco,
                high=preco * 1.0006,
                low=preco * 0.9994,
                close=preco * 1.0002,
                tick_volume=1000,
                spread=8,
                real_volume=0,
            )
        )
    CandleRepository(db_session).bulk_upsert(symbol.id, "M15", candles)
    db_session.commit()


# --- indice de configuracoes ----------------------------------------------


def test_the_hub_lists_every_configurable_area(logged_in) -> None:
    """A pergunta que a tela existe para responder e "onde fica X?"."""
    response = logged_in.get("/dashboard/settings")

    assert response.status_code == 200
    corpo = response.text
    for destino in (
        "/dashboard/trading",
        "/dashboard/mt5",
        "/dashboard/settings/aisa",
        "/dashboard/scanner",
        "/dashboard/mode",
    ):
        assert destino in corpo


def test_the_hub_requires_login(client) -> None:
    response = client.get("/dashboard/settings", follow_redirects=False)

    assert response.status_code in (302, 303, 307)


# --- modo observacao pelo painel ------------------------------------------


def test_turning_observation_on_persists_the_choice(logged_in, db_session) -> None:
    response = logged_in.post(
        "/dashboard/scanner/observation",
        data={"action": "start", "interval_minutes": "45"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    config = load_observation_config(db_session)
    assert config.enabled is True
    assert config.interval_minutes == 45


def test_turning_observation_off_keeps_the_history(logged_in, db_session) -> None:
    """Desligar nao pode apagar a marca do ultimo registro: religar depois
    voltaria a gravar como se nunca tivesse gravado."""
    save_observation_config(
        db_session,
        ObservationConfig(
            enabled=True, interval_minutes=30, last_recorded_at="2026-07-07T14:00:00+00:00"
        ),
    )
    db_session.commit()

    logged_in.post(
        "/dashboard/scanner/observation",
        data={"action": "stop", "interval_minutes": "30"},
        follow_redirects=False,
    )

    config = load_observation_config(db_session)
    assert config.enabled is False
    assert config.last_recorded_at == "2026-07-07T14:00:00+00:00"


def test_an_absurd_interval_is_clamped_instead_of_accepted(logged_in, db_session) -> None:
    logged_in.post(
        "/dashboard/scanner/observation",
        data={"action": "start", "interval_minutes": "999999"},
        follow_redirects=False,
    )

    assert load_observation_config(db_session).interval_minutes == INTERVAL_MAX_MINUTES


def test_the_toggle_is_audited(logged_in, db_session) -> None:
    from app.database.repositories.audit_log_repository import AuditLogRepository

    logged_in.post(
        "/dashboard/scanner/observation",
        data={"action": "start", "interval_minutes": "30"},
        follow_redirects=False,
    )

    acoes = [item.action for item in AuditLogRepository(db_session).list_recent(limit=5)]
    assert "scanner_observation_toggle" in acoes


# --- gravar agora ----------------------------------------------------------


def test_the_button_records_a_sample(logged_in, db_session) -> None:
    """Era exatamente isto que exigia `scanner run --record`."""
    seed_tradable_symbol(db_session)

    response = logged_in.post("/dashboard/scanner/record", follow_redirects=False)

    assert response.status_code == 303
    observacoes = load_observations(db_session)
    assert len(observacoes) == 1
    assert observacoes[0].symbol == SYMBOL


def test_with_nothing_approved_the_button_says_so_instead_of_faking_success(
    logged_in, db_session
) -> None:
    """Varredura sem candidato nao vira amostra — a tela tem que admitir."""
    # A suite compartilha um banco so. Sem zerar os candles, um simbolo
    # semeado por outro teste faria a varredura aprovar algo, e este caso
    # estaria medindo exatamente o caminho oposto ao que descreve.
    from sqlalchemy import delete

    from app.database.models.candle import Candle

    db_session.execute(delete(Candle))
    db_session.commit()

    response = logged_in.post("/dashboard/scanner/record", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert load_observations(db_session) == []


def test_the_scanner_page_offers_the_buttons_and_no_command(logged_in) -> None:
    response = logged_in.get("/dashboard/scanner")

    assert response.status_code == 200
    assert "/dashboard/scanner/record" in response.text
    assert "/dashboard/scanner/observation" in response.text
    assert "app.cli" not in response.text


# --- ajustes da API --------------------------------------------------------


def test_the_budget_can_be_changed_from_the_panel(logged_in, db_session) -> None:
    response = logged_in.post(
        "/dashboard/settings/aisa",
        data={"daily_budget": "40", "cache_ttl_seconds": "1200"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    from app.core.config import get_settings

    resolved = load_api_settings(db_session, get_settings())
    assert resolved.daily_budget == 40
    assert resolved.cache_ttl_seconds == 1200.0


def test_a_budget_out_of_range_is_refused_with_a_reason(logged_in, db_session) -> None:
    response = logged_in.post(
        "/dashboard/settings/aisa",
        data={"daily_budget": "-5"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert SystemSettingRepository(db_session).get(BUDGET_LIMIT_SETTING) in (None, "")


def test_the_api_page_exposes_the_budget_field(logged_in) -> None:
    response = logged_in.get("/dashboard/settings/aisa")

    assert response.status_code == 200
    assert 'name="daily_budget"' in response.text
    assert 'name="cache_ttl_seconds"' in response.text
