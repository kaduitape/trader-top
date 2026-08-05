"""Modo observacao: a intencao mora no banco, a execucao no worker.

O que estes testes protegem: que ligar o modo produza a primeira amostra
imediatamente (ninguem liga uma coisa para olhar tela vazia por meia hora)
e que o intervalo nao possa ser configurado para valores que fariam o
diario virar ruido ou nunca gravar nada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.market.scan_settings import (
    DEFAULT_INTERVAL_MINUTES,
    INTERVAL_MAX_MINUTES,
    INTERVAL_MIN_MINUTES,
    SCAN_OBSERVATION_SETTING,
    ObservationConfig,
    clamp_interval,
    is_due,
    load_observation_config,
    mark_recorded,
    save_observation_config,
)

NOW = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _limpa(db_session):
    """Banco de teste compartilhado: limpar so no fim deixaria o primeiro
    teste dependendo de quem rodou antes dele."""
    def apagar() -> None:
        SystemSettingRepository(db_session).set(SCAN_OBSERVATION_SETTING, "")
        db_session.commit()

    apagar()
    yield
    apagar()


def test_the_default_is_off(db_session) -> None:
    """Observar grava linhas no banco sozinho: tem que ser escolha."""
    config = load_observation_config(db_session)

    assert config.enabled is False
    assert config.interval_minutes == DEFAULT_INTERVAL_MINUTES


def test_it_survives_a_round_trip(db_session) -> None:
    save_observation_config(db_session, ObservationConfig(enabled=True, interval_minutes=45))
    db_session.commit()

    config = load_observation_config(db_session)

    assert config.enabled is True
    assert config.interval_minutes == 45


def test_a_corrupt_record_falls_back_to_off(db_session) -> None:
    """Configuracao ilegivel nao pode ligar gravacao automatica por acidente."""
    SystemSettingRepository(db_session).set(SCAN_OBSERVATION_SETTING, "{isso nao e json")
    db_session.commit()

    assert load_observation_config(db_session).enabled is False


def test_the_interval_is_clamped_to_the_accepted_range() -> None:
    assert clamp_interval(1) == INTERVAL_MIN_MINUTES
    assert clamp_interval(99_999) == INTERVAL_MAX_MINUTES
    assert clamp_interval(60) == 60


def test_a_saved_interval_out_of_range_is_read_back_clamped(db_session) -> None:
    save_observation_config(
        db_session, ObservationConfig(enabled=True, interval_minutes=99_999)
    )
    db_session.commit()

    assert load_observation_config(db_session).interval_minutes == INTERVAL_MAX_MINUTES


# --- quando gravar ---------------------------------------------------------


def test_nothing_is_due_while_observation_is_off() -> None:
    assert is_due(ObservationConfig(enabled=False), now=NOW) is False


def test_the_first_sample_is_due_right_away() -> None:
    """Quem acabou de ligar precisa ver algo acontecer."""
    assert is_due(ObservationConfig(enabled=True), now=NOW) is True


def test_nothing_is_due_before_the_interval_elapses() -> None:
    config = ObservationConfig(
        enabled=True,
        interval_minutes=30,
        last_recorded_at=(NOW - timedelta(minutes=10)).isoformat(),
    )

    assert is_due(config, now=NOW) is False


def test_it_is_due_again_once_the_interval_elapses() -> None:
    config = ObservationConfig(
        enabled=True,
        interval_minutes=30,
        last_recorded_at=(NOW - timedelta(minutes=31)).isoformat(),
    )

    assert is_due(config, now=NOW) is True


def test_an_unreadable_timestamp_does_not_freeze_recording_forever() -> None:
    """Marca corrompida nao pode deixar o modo ligado sem nunca gravar."""
    config = ObservationConfig(enabled=True, last_recorded_at="ontem de tarde")

    assert is_due(config, now=NOW) is True


def test_marking_a_record_pushes_the_next_one_forward(db_session) -> None:
    atualizado = mark_recorded(
        db_session, ObservationConfig(enabled=True, interval_minutes=30), now=NOW
    )
    db_session.commit()

    assert is_due(atualizado, now=NOW) is False
    assert atualizado.next_due_at() == NOW + timedelta(minutes=30)
    assert load_observation_config(db_session).last_recorded_at == atualizado.last_recorded_at
