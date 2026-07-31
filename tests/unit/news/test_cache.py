"""Cache das respostas da MarketPulse.

O que importa aqui nao e "guardar", e guardar SEM mentir: falha nunca vira
cache, o valor reaproveitado diz a propria idade, e o prazo expira de
verdade.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.news.cache import (
    AssessmentCache,
    CachedFundamentalsProvider,
    CachedNewsProvider,
)
from app.news.provider import (
    FundamentalsAssessment,
    NewsAssessment,
    NewsItem,
    ProviderStatus,
)

NOW = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)


class FakeClock:
    """Relogio manual: o teste decide quando o tempo passa."""

    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class CountingNewsProvider:
    def __init__(self, *statuses: ProviderStatus) -> None:
        # Um status por chamada; a ultima se repete se acabar a lista.
        self._statuses = list(statuses) or [ProviderStatus.OK]
        self.calls = 0

    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        status = self._statuses[min(self.calls, len(self._statuses) - 1)]
        self.calls += 1
        return NewsAssessment(
            status=status,
            score_contribution=62.0,
            items=[
                NewsItem(
                    headline=f"manchete {self.calls} de {symbol}",
                    published_at=now,
                    impact="MEDIUM",
                    currency="USD",
                    sentiment=0.24,
                )
            ],
            message=f"chamada numero {self.calls}",
        )


class CountingFundamentalsProvider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_assessment(self, symbol: str, *, now: datetime) -> FundamentalsAssessment:
        self.calls += 1
        return FundamentalsAssessment(
            status=ProviderStatus.OK,
            score_contribution=55.0,
            message=f"chamada numero {self.calls}",
        )


def make_news(inner, clock, *, ttl: float = 600.0) -> CachedNewsProvider:
    return CachedNewsProvider(
        inner, AssessmentCache(ttl_seconds=ttl, clock=clock), namespace="teste"
    )


def test_second_call_within_the_window_does_not_hit_the_api() -> None:
    inner = CountingNewsProvider()
    clock = FakeClock()
    provider = make_news(inner, clock)

    first = provider.fetch_assessment("EURUSD", now=NOW)
    clock.advance(60)
    second = provider.fetch_assessment("EURUSD", now=NOW)

    assert inner.calls == 1
    assert second.score_contribution == first.score_contribution
    assert second.items == first.items


def test_the_reused_answer_says_it_came_from_cache_and_how_old_it_is() -> None:
    """Regra do projeto: nunca apresentar dado velho como se fosse fresco."""
    inner = CountingNewsProvider()
    clock = FakeClock()
    provider = make_news(inner, clock)

    provider.fetch_assessment("EURUSD", now=NOW)
    clock.advance(42)
    reused = provider.fetch_assessment("EURUSD", now=NOW)

    assert "cache MarketPulse: 42s" in reused.message
    assert "chamada numero 1" in reused.message


def test_expired_entry_goes_back_to_the_api() -> None:
    inner = CountingNewsProvider()
    clock = FakeClock()
    provider = make_news(inner, clock, ttl=600.0)

    provider.fetch_assessment("EURUSD", now=NOW)
    clock.advance(601)
    fresh = provider.fetch_assessment("EURUSD", now=NOW)

    assert inner.calls == 2
    assert "cache" not in fresh.message


def test_a_failure_is_never_cached() -> None:
    """Congelar uma instabilidade da API por 10 minutos esconderia o
    problema e deixaria o fator neutro sem ninguem perceber."""
    inner = CountingNewsProvider(ProviderStatus.ERROR, ProviderStatus.ERROR, ProviderStatus.OK)
    clock = FakeClock()
    provider = make_news(inner, clock)

    provider.fetch_assessment("EURUSD", now=NOW)
    provider.fetch_assessment("EURUSD", now=NOW)
    assert inner.calls == 2

    provider.fetch_assessment("EURUSD", now=NOW)  # agora responde OK
    provider.fetch_assessment("EURUSD", now=NOW)  # esta sim vem do cache
    assert inner.calls == 3


def test_each_symbol_has_its_own_entry() -> None:
    inner = CountingNewsProvider()
    clock = FakeClock()
    provider = make_news(inner, clock)

    provider.fetch_assessment("EURUSD", now=NOW)
    provider.fetch_assessment("XAUUSD", now=NOW)
    provider.fetch_assessment("EURUSD", now=NOW)

    assert inner.calls == 2


def test_symbol_lookup_ignores_case() -> None:
    inner = CountingNewsProvider()
    provider = make_news(inner, FakeClock())

    provider.fetch_assessment("eurusd", now=NOW)
    provider.fetch_assessment("EURUSD", now=NOW)

    assert inner.calls == 1


def test_ttl_zero_disables_the_cache_entirely() -> None:
    inner = CountingNewsProvider()
    provider = make_news(inner, FakeClock(), ttl=0)

    provider.fetch_assessment("EURUSD", now=NOW)
    provider.fetch_assessment("EURUSD", now=NOW)

    assert inner.calls == 2


def test_news_and_fundamentals_do_not_share_entries() -> None:
    """Os dois usam a mesma chave (simbolo); sem namespace um serviria a
    resposta do outro."""
    clock = FakeClock()
    cache = AssessmentCache(ttl_seconds=600.0, clock=clock)
    news_inner = CountingNewsProvider()
    fundamentals_inner = CountingFundamentalsProvider()
    news = CachedNewsProvider(news_inner, cache, namespace="teste")
    fundamentals = CachedFundamentalsProvider(fundamentals_inner, cache, namespace="teste")

    news_result = news.fetch_assessment("EURUSD", now=NOW)
    fundamentals_result = fundamentals.fetch_assessment("EURUSD", now=NOW)

    assert isinstance(news_result, NewsAssessment)
    assert isinstance(fundamentals_result, FundamentalsAssessment)
    assert news_inner.calls == 1
    assert fundamentals_inner.calls == 1


def test_different_endpoints_do_not_share_entries() -> None:
    clock = FakeClock()
    cache = AssessmentCache(ttl_seconds=600.0, clock=clock)
    inner = CountingNewsProvider()
    producao = CachedNewsProvider(inner, cache, namespace="https://api.exemplo/v1")
    homologacao = CachedNewsProvider(inner, cache, namespace="https://sandbox.exemplo/v1")

    producao.fetch_assessment("EURUSD", now=NOW)
    homologacao.fetch_assessment("EURUSD", now=NOW)

    assert inner.calls == 2


def test_fundamentals_are_cached_too() -> None:
    inner = CountingFundamentalsProvider()
    clock = FakeClock()
    provider = CachedFundamentalsProvider(
        inner, AssessmentCache(ttl_seconds=600.0, clock=clock), namespace="teste"
    )

    provider.fetch_assessment("EURUSD", now=NOW)
    clock.advance(120)
    reused = provider.fetch_assessment("EURUSD", now=NOW)

    assert inner.calls == 1
    assert "cache MarketPulse: 120s" in reused.message


def test_hit_and_miss_counters_are_available_for_diagnosis() -> None:
    inner = CountingNewsProvider()
    cache = AssessmentCache(ttl_seconds=600.0, clock=FakeClock())
    provider = CachedNewsProvider(inner, cache, namespace="teste")

    provider.fetch_assessment("EURUSD", now=NOW)  # miss
    provider.fetch_assessment("EURUSD", now=NOW)  # hit
    provider.fetch_assessment("EURUSD", now=NOW)  # hit

    assert (cache.hits, cache.misses) == (2, 1)
