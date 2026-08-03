"""Relatorio "por que nao operei hoje".

O valor esta na FRACAO, nao na contagem: um motivo em 100% dos ciclos e
configuracao para corrigir; em 8%, e o mercado. Na tela de status, sem
contar, os dois pareciam a mesma coisa.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.execution.blocker_stats import (
    BLOCKER_STATS_SETTING,
    load_blocker_stats,
    normalize_reason,
    record_cycle,
)

HOJE = datetime(2026, 6, 3, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _zera(db_session):
    SystemSettingRepository(db_session).set(BLOCKER_STATS_SETTING, "")
    db_session.commit()


def test_no_cycles_yet_reports_nothing(db_session) -> None:
    stats = load_blocker_stats(db_session, now=HOJE)
    assert stats.cycles == 0
    assert stats.dominant is None


def test_the_dominant_reason_is_the_one_that_repeats(db_session) -> None:
    for _ in range(9):
        record_cycle(db_session, blockers=["Cobertura insuficiente nos timeframes"], now=HOJE)
    record_cycle(db_session, blockers=["Volume nao favoravel (score 51.0, minimo 60)"], now=HOJE)
    db_session.commit()

    stats = load_blocker_stats(db_session, now=HOJE)

    assert stats.cycles == 10
    assert stats.dominant.reason == "Cobertura insuficiente nos timeframes"
    assert stats.dominant.count == 9
    assert stats.dominant.share == pytest.approx(0.9)


def test_variable_details_do_not_split_the_same_reason(db_session) -> None:
    """"Volume nao favoravel (score 51.3)" e "(score 48.9)" sao o mesmo
    problema — somam, nao viram duas linhas."""
    record_cycle(db_session, blockers=["Volume nao favoravel (score 51.3, minimo 60)"], now=HOJE)
    record_cycle(db_session, blockers=["Volume nao favoravel (score 48.9, minimo 60)"], now=HOJE)
    db_session.commit()

    stats = load_blocker_stats(db_session, now=HOJE)

    assert len(stats.reasons) == 1
    assert stats.reasons[0].count == 2


def test_a_cycle_that_entered_still_counts(db_session) -> None:
    """Sem contar os ciclos aprovados, toda fracao daria 100% e o relatorio
    nao diria nada."""
    record_cycle(db_session, blockers=["Volume nao favoravel"], now=HOJE)
    for _ in range(3):
        record_cycle(db_session, blockers=[], now=HOJE)
    db_session.commit()

    stats = load_blocker_stats(db_session, now=HOJE)

    assert stats.cycles == 4
    assert stats.reasons[0].share == pytest.approx(0.25)


def test_the_same_reason_twice_in_one_cycle_counts_once(db_session) -> None:
    record_cycle(
        db_session,
        blockers=["Volume nao favoravel (score 51)", "Volume nao favoravel (score 51)"],
        now=HOJE,
    )
    db_session.commit()

    assert load_blocker_stats(db_session, now=HOJE).reasons[0].count == 1


def test_a_new_day_starts_over(db_session) -> None:
    record_cycle(db_session, blockers=["Volume nao favoravel"], now=HOJE)
    db_session.commit()

    stats = load_blocker_stats(db_session, now=HOJE + timedelta(days=1))

    assert stats.cycles == 0
    assert stats.reasons == ()


def test_normalization_keeps_a_reason_without_details_intact() -> None:
    assert normalize_reason("Spread acima do teto") == "Spread acima do teto"
    assert normalize_reason("Volume nao favoravel (score 51.0)") == "Volume nao favoravel"
