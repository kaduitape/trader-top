"""Teto diario de chamadas a MarketPulse.

O cache evita repeticao dentro de um processo; o orcamento e o que garante
que a cota nao acabe de novo — inclusive quando o worker reinicia (cache
vazio) ou quando web e conector consultam em paralelo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.budget import BUDGET_SETTING, BudgetedProvider, read_usage, record_call
from app.news.provider import NewsAssessment, ProviderStatus
from app.news.unconfigured import skipped_news

HOJE = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _zera_contador(db_session):
    """O contador e persistido de proposito, e a suite compartilha um unico
    banco: sem zerar, o consumo de um teste decidiria o resultado do outro."""
    SystemSettingRepository(db_session).set(BUDGET_SETTING, "")
    db_session.commit()


class CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        self.calls += 1
        return NewsAssessment(
            status=ProviderStatus.OK, score_contribution=70.0, message="ok"
        )


def budgeted(inner, *, limit: int) -> BudgetedProvider:
    return BudgetedProvider(
        inner, limit=limit, skipped_factory=skipped_news, kind="noticias"
    )


def test_usage_starts_at_zero(db_session) -> None:
    usage = read_usage(db_session, limit=100, now=HOJE)
    assert usage.calls == 0
    assert usage.remaining == 100
    assert not usage.exhausted


def test_calls_accumulate_within_the_same_day(db_session) -> None:
    for _ in range(3):
        record_call(db_session, limit=100, now=HOJE)
    db_session.commit()

    assert read_usage(db_session, limit=100, now=HOJE).calls == 3


def test_a_new_utc_day_starts_the_count_over(db_session) -> None:
    """Sem tarefa agendada para limpar nada: a virada do dia zera sozinha."""
    record_call(db_session, limit=100, now=HOJE)
    db_session.commit()

    amanha = HOJE + timedelta(days=1)
    assert read_usage(db_session, limit=100, now=amanha).calls == 0


def test_limit_zero_means_no_ceiling(db_session) -> None:
    usage = read_usage(db_session, limit=0, now=HOJE)
    assert not usage.exhausted
    assert usage.remaining is None


def test_provider_stops_calling_after_the_ceiling(db_session) -> None:
    inner = CountingProvider()
    provider = budgeted(inner, limit=2)

    provider.fetch_assessment("EURUSD", now=HOJE)
    provider.fetch_assessment("EURUSD", now=HOJE)
    terceira = provider.fetch_assessment("EURUSD", now=HOJE)

    assert inner.calls == 2
    assert terceira.status == ProviderStatus.SKIPPED
    assert "Orcamento diario" in terceira.message


def test_the_ceiling_never_fabricates_data(db_session) -> None:
    """Esgotar a cota faz o fator SAIR do calculo, nunca virar um numero
    inventado nem um erro que assusta sem explicar."""
    provider = budgeted(CountingProvider(), limit=1)
    provider.fetch_assessment("EURUSD", now=HOJE)

    bloqueada = provider.fetch_assessment("EURUSD", now=HOJE)

    assert bloqueada.status == ProviderStatus.SKIPPED
    assert bloqueada.items == []
    assert bloqueada.score_contribution == 50.0


def test_no_limit_configured_never_touches_the_counter(db_session) -> None:
    inner = CountingProvider()
    provider = budgeted(inner, limit=0)

    for _ in range(5):
        provider.fetch_assessment("EURUSD", now=HOJE)

    assert inner.calls == 5
    assert read_usage(db_session, limit=10, now=HOJE).calls == 0


def test_a_corrupt_record_does_not_unlock_unlimited_spending(db_session) -> None:
    SystemSettingRepository(db_session).set(BUDGET_SETTING, "isso nao e json")
    db_session.commit()

    usage = read_usage(db_session, limit=5, now=HOJE)

    assert usage.calls == 0
    assert usage.limit == 5
