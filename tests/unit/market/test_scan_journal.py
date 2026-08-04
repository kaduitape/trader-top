"""Diario da varredura em modo observacao.

O valor do diario nao esta em guardar escolhas: esta em permitir responder,
semanas depois, se as escolhas do scanner foram melhores que operar um par
fixo. A margem para o segundo colocado e o numero que diz se o ranking esta
discriminando alguma coisa ou praticamente sorteando.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.market.scan_journal import (
    SCAN_JOURNAL_SETTING,
    load_observations,
    record_scan,
    summarize,
)
from app.market.scanner import ScanCandidate, ScanResult

NOW = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _limpa(db_session):
    SystemSettingRepository(db_session).set(SCAN_JOURNAL_SETTING, "")
    db_session.commit()


def candidato(symbol: str, score: float, *, blocked: str | None = None) -> ScanCandidate:
    return ScanCandidate(
        symbol=symbol,
        score=score,
        session_score=100.0,
        volume_score=75.0,
        cost_score=90.0,
        session_label="Melhor horario",
        volume_label="Acima do normal",
        spread_points=8.0,
        atr_points=200.0,
        spread_atr_ratio=0.04,
        blocked_reason=blocked,
    )


def resultado(*candidatos: ScanCandidate, at: datetime = NOW) -> ScanResult:
    return ScanResult(generated_at=at, candidates=tuple(candidatos))


def test_nothing_is_recorded_when_there_is_no_candidate(db_session) -> None:
    """Um diario cheio de "nao havia nada" enterraria as escolhas reais."""
    gravado = record_scan(db_session, resultado(candidato("EURUSD", 80.0, blocked="Fechado")))
    db_session.commit()

    assert gravado is None
    assert load_observations(db_session) == []


def test_the_winner_is_recorded_with_its_context(db_session) -> None:
    record_scan(db_session, resultado(candidato("EURUSD", 88.0)))
    db_session.commit()

    observacoes = load_observations(db_session)

    assert len(observacoes) == 1
    assert observacoes[0].symbol == "EURUSD"
    assert observacoes[0].score == 88.0
    assert observacoes[0].volume == "Acima do normal"


def test_the_runner_up_is_recorded_too(db_session) -> None:
    record_scan(
        db_session, resultado(candidato("EURUSD", 88.0), candidato("XAUUSD", 71.0))
    )
    db_session.commit()

    observacao = load_observations(db_session)[0]

    assert observacao.runner_up == "XAUUSD"
    assert observacao.runner_up_score == 71.0


def test_a_blocked_candidate_never_becomes_the_runner_up(db_session) -> None:
    record_scan(
        db_session,
        resultado(
            candidato("EURUSD", 88.0),
            candidato("GBPUSD", 80.0, blocked="Evento de alto impacto"),
        ),
    )
    db_session.commit()

    assert load_observations(db_session)[0].runner_up is None


def test_observations_accumulate(db_session) -> None:
    for i in range(3):
        record_scan(db_session, resultado(candidato(f"PAR{i}", 80.0 + i)))
    db_session.commit()

    assert len(load_observations(db_session)) == 3


def test_a_corrupt_journal_starts_over_instead_of_breaking(db_session) -> None:
    SystemSettingRepository(db_session).set(SCAN_JOURNAL_SETTING, "isso nao e json")
    db_session.commit()

    assert load_observations(db_session) == []


# --- resumo ---------------------------------------------------------------


def test_an_empty_journal_summarizes_to_zero(db_session) -> None:
    resumo = summarize(db_session)

    assert resumo.total == 0
    assert resumo.average_score is None


def test_the_summary_counts_choices_per_symbol(db_session) -> None:
    for _ in range(3):
        record_scan(db_session, resultado(candidato("EURUSD", 80.0)))
    record_scan(db_session, resultado(candidato("XAUUSD", 90.0)))
    db_session.commit()

    resumo = summarize(db_session)

    assert resumo.total == 4
    assert resumo.by_symbol[0] == ("EURUSD", 3)


def test_a_narrow_margin_shows_the_ranking_is_barely_choosing(db_session) -> None:
    """Se o primeiro quase empata com o segundo sempre, o ranking nao esta
    discriminando — e a complexidade nao se paga."""
    record_scan(
        db_session, resultado(candidato("EURUSD", 80.0), candidato("GBPUSD", 79.5))
    )
    record_scan(
        db_session, resultado(candidato("EURUSD", 81.0), candidato("GBPUSD", 80.5))
    )
    db_session.commit()

    resumo = summarize(db_session)

    assert resumo.average_margin == pytest.approx(0.5)
