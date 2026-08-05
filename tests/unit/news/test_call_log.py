"""Registro de quando a API paga foi consultada.

O contador de orcamento diz *quanto* foi gasto. Este registro diz *quando,
por que e a mando de quem* — a pergunta que levou a descobrir que abrir a
tela de analise gastava cota com o robo desligado.

O que estes testes protegem: que a ORIGEM chegue no registro (sem ela a
lista nao responde nada), que acerto de cache nunca entre (a lista e o que
a API cobrou), e que falhar ao registrar nunca derrube a analise.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.budget import BUDGET_SETTING, BudgetedProvider
from app.news.cache import AssessmentCache, CachedNewsProvider
from app.news.call_log import (
    CALL_LOG_SETTING,
    ORIGIN_PANEL,
    ORIGIN_ROBOT,
    ORIGIN_UNKNOWN,
    calls_from,
    current_origin,
    load_calls,
    record_api_call,
    summarize_calls,
)
from app.news.provider import NewsAssessment, ProviderStatus

NOW = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _limpa(db_session):
    def apagar() -> None:
        repo = SystemSettingRepository(db_session)
        repo.set(CALL_LOG_SETTING, "")
        # O contador de orcamento tambem e compartilhado: sem zerar, um
        # teste com teto baixo herdaria o consumo dos anteriores e mediria
        # o caminho do orcamento esgotado sem saber.
        repo.set(BUDGET_SETTING, "")
        db_session.commit()

    apagar()
    yield
    apagar()


class _Fake:
    """Provedor que conta quantas vezes foi realmente chamado."""

    def __init__(self, status: ProviderStatus = ProviderStatus.OK) -> None:
        self.chamadas = 0
        self._status = status

    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        self.chamadas += 1
        return NewsAssessment(status=self._status, score_contribution=60.0)


class _Explode:
    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        raise RuntimeError("a rede caiu")


def _skipped(message: str) -> NewsAssessment:
    return NewsAssessment(status=ProviderStatus.SKIPPED, score_contribution=50.0, message=message)


# --- contexto de origem ----------------------------------------------------


def test_without_a_declared_origin_it_says_so_instead_of_guessing() -> None:
    assert current_origin() == ORIGIN_UNKNOWN


def test_the_origin_applies_only_inside_the_block() -> None:
    with calls_from(ORIGIN_PANEL):
        assert current_origin() == ORIGIN_PANEL
    assert current_origin() == ORIGIN_UNKNOWN


def test_the_origin_is_restored_even_when_the_block_raises() -> None:
    with pytest.raises(RuntimeError), calls_from(ORIGIN_ROBOT):
        raise RuntimeError("falha no meio da analise")

    assert current_origin() == ORIGIN_UNKNOWN


# --- gravacao --------------------------------------------------------------


def test_a_call_is_recorded_with_its_context(db_session) -> None:
    record_api_call(
        db_session,
        kind="noticias",
        symbol="EURUSD",
        outcome="OK",
        duration_ms=340,
        origin=ORIGIN_PANEL,
        now=NOW,
    )
    db_session.commit()

    registros = load_calls(db_session)

    assert len(registros) == 1
    assert registros[0].symbol == "EURUSD"
    assert registros[0].origin == ORIGIN_PANEL
    assert registros[0].duration_ms == 340
    assert registros[0].failed is False


def test_a_corrupt_log_starts_over_instead_of_breaking(db_session) -> None:
    SystemSettingRepository(db_session).set(CALL_LOG_SETTING, "isso nao e json")
    db_session.commit()

    assert load_calls(db_session) == []


def test_the_summary_counts_per_origin(db_session) -> None:
    for _ in range(3):
        record_api_call(
            db_session, kind="noticias", symbol="EURUSD", outcome="OK",
            duration_ms=100, origin=ORIGIN_PANEL, now=NOW,
        )
    record_api_call(
        db_session, kind="fundamentos", symbol="EURUSD", outcome="ERROR",
        duration_ms=200, origin=ORIGIN_ROBOT, now=NOW,
    )
    db_session.commit()

    resumo = summarize_calls(db_session)

    assert resumo.total == 4
    assert resumo.failures == 1
    assert resumo.by_origin[0] == (ORIGIN_PANEL, 3)


# --- integracao com o caminho real -----------------------------------------


def test_a_real_call_through_the_budget_is_logged(db_session) -> None:
    provider = BudgetedProvider(
        _Fake(), limit=10, skipped_factory=_skipped, kind="noticias"
    )

    with calls_from(ORIGIN_ROBOT):
        provider.fetch_assessment("EURUSD", now=NOW)

    registros = load_calls(db_session)
    assert len(registros) == 1
    assert registros[0].origin == ORIGIN_ROBOT
    assert registros[0].kind == "noticias"


def test_a_cache_hit_never_reaches_the_log(db_session) -> None:
    """A lista tem que significar "o que a API cobrou". Se acerto de cache
    entrasse, ela passaria a medir intencao em vez de gasto."""
    inner = _Fake()
    cached = CachedNewsProvider(
        BudgetedProvider(inner, limit=10, skipped_factory=_skipped, kind="noticias"),
        AssessmentCache(ttl_seconds=600.0),
        namespace="teste",
    )

    cached.fetch_assessment("EURUSD", now=NOW)
    cached.fetch_assessment("EURUSD", now=NOW)

    assert inner.chamadas == 1
    assert len(load_calls(db_session)) == 1


def test_an_exhausted_budget_is_not_logged_as_a_call(db_session) -> None:
    """Consulta que nao aconteceu nao pode aparecer como gasto."""
    provider = BudgetedProvider(
        _Fake(), limit=1, skipped_factory=_skipped, kind="noticias"
    )

    provider.fetch_assessment("EURUSD", now=NOW)
    provider.fetch_assessment("GBPUSD", now=NOW)

    registros = load_calls(db_session)
    assert len(registros) == 1
    assert registros[0].symbol == "EURUSD"


def test_a_failed_call_is_logged_too(db_session) -> None:
    """Registrar so o que deu certo faria o log mentir justamente no dia
    problematico."""
    provider = BudgetedProvider(
        _Explode(), limit=10, skipped_factory=_skipped, kind="noticias"
    )

    with pytest.raises(RuntimeError):
        provider.fetch_assessment("EURUSD", now=NOW)

    registros = load_calls(db_session)
    assert len(registros) == 1
    assert registros[0].failed is True


def test_an_error_response_is_logged_with_its_status(db_session) -> None:
    provider = BudgetedProvider(
        _Fake(ProviderStatus.ERROR), limit=10, skipped_factory=_skipped, kind="noticias"
    )

    provider.fetch_assessment("EURUSD", now=NOW)

    registros = load_calls(db_session)
    assert registros[0].outcome == ProviderStatus.ERROR.value
    assert registros[0].failed is True


def test_calls_are_logged_even_without_a_daily_ceiling(db_session) -> None:
    """Teto zero significa "sem limite", nao "sem registro" — e justamente
    nesse modo que saber para onde foi a cota importa mais."""
    provider = BudgetedProvider(
        _Fake(), limit=0, skipped_factory=_skipped, kind="fundamentos"
    )

    provider.fetch_assessment("EURUSD", now=NOW)

    assert len(load_calls(db_session)) == 1
