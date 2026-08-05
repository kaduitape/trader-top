"""Teto diario e validade do cache configuraveis fora do `.env`.

Sao os dois numeros que decidem a fatura da API. O que estes testes
protegem: que o `.env` continue valendo como padrao (instalacao nova nao
precisa configurar nada) e que um valor corrompido no banco nunca vire
"sem teto" — o modo exato em que a cota acabou da ultima vez.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.api_settings import (
    BUDGET_LIMIT_SETTING,
    CACHE_TTL_SETTING,
    load_api_settings,
    save_api_settings,
    validate_api_settings,
)


@pytest.fixture(autouse=True)
def _limpa(db_session):
    """O banco de teste e compartilhado: sem limpar nos DOIS lados, um teste
    que grava o teto muda o resultado do proximo."""
    def apagar() -> None:
        repo = SystemSettingRepository(db_session)
        repo.set(BUDGET_LIMIT_SETTING, "")
        repo.set(CACHE_TTL_SETTING, "")
        db_session.commit()

    apagar()
    yield
    apagar()


def _settings(**overrides) -> Settings:
    base = {
        "app_env": "test",
        "app_secret_key": "chave-de-teste-1234",
        "news_daily_call_budget": 300,
        "news_cache_ttl_seconds": 600.0,
    }
    base.update(overrides)
    return Settings(**base)


def test_without_anything_saved_the_env_wins(db_session) -> None:
    resolved = load_api_settings(db_session, _settings())

    assert resolved.daily_budget == 300
    assert resolved.cache_ttl_seconds == 600.0
    assert resolved.budget_source == "env"
    assert resolved.ttl_source == "env"


def test_the_dashboard_value_overrides_the_env(db_session) -> None:
    save_api_settings(db_session, daily_budget=50, cache_ttl_seconds=1800)
    db_session.commit()

    resolved = load_api_settings(db_session, _settings())

    assert resolved.daily_budget == 50
    assert resolved.cache_ttl_seconds == 1800.0
    assert resolved.budget_source == "dashboard"


def test_each_field_falls_back_on_its_own(db_session) -> None:
    """Configurar so o teto nao pode arrastar o cache junto."""
    save_api_settings(db_session, daily_budget=10)
    db_session.commit()

    resolved = load_api_settings(db_session, _settings())

    assert resolved.daily_budget == 10
    assert resolved.budget_source == "dashboard"
    assert resolved.cache_ttl_seconds == 600.0
    assert resolved.ttl_source == "env"


def test_zero_is_a_real_value_not_an_absent_one(db_session) -> None:
    """Zero significa "sem teto" no modulo de orcamento — precisa chegar
    la como escolha, e nao ser confundido com campo em branco."""
    save_api_settings(db_session, daily_budget=0)
    db_session.commit()

    resolved = load_api_settings(db_session, _settings())

    assert resolved.daily_budget == 0
    assert resolved.budget_source == "dashboard"


def test_a_corrupt_value_falls_back_instead_of_removing_the_ceiling(db_session) -> None:
    SystemSettingRepository(db_session).set(BUDGET_LIMIT_SETTING, "ilimitado")
    SystemSettingRepository(db_session).set(CACHE_TTL_SETTING, "")
    db_session.commit()

    resolved = load_api_settings(db_session, _settings())

    assert resolved.daily_budget == 300
    assert resolved.budget_source == "env"
    assert resolved.cache_ttl_seconds == 600.0


# --- validacao -------------------------------------------------------------


def test_valid_values_pass() -> None:
    assert validate_api_settings(daily_budget=100, cache_ttl_seconds=600) is None


def test_omitted_fields_pass() -> None:
    assert validate_api_settings(daily_budget=None, cache_ttl_seconds=None) is None


@pytest.mark.parametrize("budget", [-1, 5_001])
def test_a_budget_out_of_range_is_rejected(budget: int) -> None:
    assert validate_api_settings(daily_budget=budget, cache_ttl_seconds=None) is not None


@pytest.mark.parametrize("ttl", [-1, 86_401])
def test_a_ttl_out_of_range_is_rejected(ttl: int) -> None:
    assert validate_api_settings(daily_budget=None, cache_ttl_seconds=ttl) is not None
