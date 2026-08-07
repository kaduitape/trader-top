"""Operar ou nao durante evento economico.

Isto morava so no `.env`, o que significa que mudar exigia acesso ao
servidor. E uma decisao de ESTRATEGIA, nao de infraestrutura: tem operador
que foge de payroll e tem operador que so opera nele.

O que estes testes protegem: que o padrao PROTEJA (campo em branco nunca
pode virar "opere durante a noticia") e que desligar seja escolha
registrada.
"""

from __future__ import annotations

import pytest

from app.calendar_feed.policy import (
    AVOID_EVENTS_SETTING,
    BEFORE_SETTING,
    MIN_IMPACT_SETTING,
    load_calendar_policy,
    save_calendar_policy,
    validate_calendar_policy,
)
from app.core.config import Settings
from app.database.repositories.system_setting_repository import SystemSettingRepository


@pytest.fixture(autouse=True)
def _limpa(db_session):
    def apagar() -> None:
        repo = SystemSettingRepository(db_session)
        for chave in (AVOID_EVENTS_SETTING, BEFORE_SETTING, "calendar_blackout_after_minutes", MIN_IMPACT_SETTING):
            repo.set(chave, "")
        db_session.commit()

    apagar()
    yield
    apagar()


def _settings(**over) -> Settings:
    base = {
        "app_env": "test",
        "app_secret_key": "chave-de-teste-1234",
        "calendar_blackout_before_minutes": 30,
        "calendar_blackout_after_minutes": 15,
        "calendar_min_impact": "HIGH",
    }
    base.update(over)
    return Settings(**base)


def test_the_default_protects(db_session) -> None:
    """Nada configurado tem que significar EVITAR. O padrao de um sistema
    que arrisca dinheiro nao pode ser o comportamento mais arriscado."""
    politica = load_calendar_policy(db_session, _settings())

    assert politica.avoid_events is True
    assert politica.source == "env"


def test_the_env_values_are_the_fallback(db_session) -> None:
    politica = load_calendar_policy(
        db_session, _settings(calendar_blackout_before_minutes=45)
    )

    assert politica.minutes_before == 45


def test_the_panel_can_turn_the_block_off(db_session) -> None:
    save_calendar_policy(
        db_session, avoid_events=False, minutes_before=30, minutes_after=15,
        min_impact="HIGH",
    )
    db_session.commit()

    politica = load_calendar_policy(db_session, _settings())

    assert politica.avoid_events is False
    assert politica.source == "dashboard"


def test_the_panel_values_override_the_env(db_session) -> None:
    save_calendar_policy(
        db_session, avoid_events=True, minutes_before=60, minutes_after=45,
        min_impact="MEDIUM",
    )
    db_session.commit()

    politica = load_calendar_policy(db_session, _settings())

    assert politica.minutes_before == 60
    assert politica.minutes_after == 45
    assert politica.min_impact == "MEDIUM"


def test_the_horizon_is_the_widest_side(db_session) -> None:
    save_calendar_policy(
        db_session, avoid_events=True, minutes_before=60, minutes_after=15,
        min_impact="HIGH",
    )
    db_session.commit()

    assert load_calendar_policy(db_session, _settings()).horizon_minutes == 60


def test_a_corrupt_value_does_not_disable_the_block(db_session) -> None:
    """Registro ilegivel nao pode liberar operacao em payroll por acidente."""
    SystemSettingRepository(db_session).set(AVOID_EVENTS_SETTING, "talvez")
    db_session.commit()

    assert load_calendar_policy(db_session, _settings()).avoid_events is True


# --- validacao -------------------------------------------------------------


def test_valid_values_pass() -> None:
    assert validate_calendar_policy(
        minutes_before=30, minutes_after=15, min_impact="HIGH"
    ) is None


@pytest.mark.parametrize("minutos", [-1, 241])
def test_a_window_out_of_range_is_rejected(minutos: int) -> None:
    assert validate_calendar_policy(
        minutes_before=minutos, minutes_after=15, min_impact="HIGH"
    ) is not None


def test_an_unknown_impact_is_rejected() -> None:
    assert validate_calendar_policy(
        minutes_before=30, minutes_after=15, min_impact="ENORME"
    ) is not None


def test_the_impact_is_case_insensitive() -> None:
    assert validate_calendar_policy(
        minutes_before=30, minutes_after=15, min_impact="high"
    ) is None
