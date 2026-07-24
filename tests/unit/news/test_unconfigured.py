from datetime import UTC, datetime

from app.news.provider import ProviderStatus
from app.news.unconfigured import UnconfiguredFundamentalsProvider, UnconfiguredNewsProvider

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_unconfigured_news_provider_is_always_neutral_and_honest() -> None:
    provider = UnconfiguredNewsProvider()

    for symbol in ("EURUSD", "GBPUSD", ""):
        assessment = provider.fetch_assessment(symbol, now=_NOW)
        assert assessment.status == ProviderStatus.NOT_CONFIGURED
        assert assessment.score_contribution == 50.0
        assert assessment.items == []
        assert assessment.message


def test_unconfigured_fundamentals_provider_is_always_neutral_and_honest() -> None:
    provider = UnconfiguredFundamentalsProvider()

    assessment = provider.fetch_assessment("EURUSD", now=_NOW)
    assert assessment.status == ProviderStatus.NOT_CONFIGURED
    assert assessment.score_contribution == 50.0
    assert assessment.message
