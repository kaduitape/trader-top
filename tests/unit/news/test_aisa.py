from datetime import UTC, datetime

import httpx
import pytest

from app.news.aisa import AisaFundamentalsProvider, AisaNewsProvider
from app.news.provider import ProviderStatus

_NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


class _StubClient:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, *, params: dict):
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        return self.payload


def test_news_provider_parses_marketpulse_envelope_and_sentiment() -> None:
    client = _StubClient(
        {
            "data": [
                {
                    "title": "Central bank signals a stable path",
                    "published_at": "2026-07-23T11:30:00Z",
                    "sentiment": 0.6,
                    "impact": "HIGH",
                    "currency": "USD",
                }
            ]
        }
    )

    result = AisaNewsProvider(client).fetch_assessment("EURUSD", now=_NOW)  # type: ignore[arg-type]

    assert result.status == ProviderStatus.OK
    assert result.score_contribution == pytest.approx(80.0)
    assert result.items[0].impact == "HIGH"
    assert result.items[0].currency == "USD"
    assert client.calls == [("financial/news", {"ticker": "EURUSD", "limit": 20})]


def test_news_provider_keeps_factor_neutral_on_transport_error() -> None:
    request = httpx.Request("GET", "https://api.aisa.one/apis/v1/financial/news")
    client = _StubClient(error=httpx.ConnectError("offline", request=request))

    result = AisaNewsProvider(client).fetch_assessment("EURUSD", now=_NOW)  # type: ignore[arg-type]

    assert result.status == ProviderStatus.ERROR
    assert result.score_contribution == 50.0
    assert result.items == []


def test_fundamentals_provider_scores_only_metrics_present_in_response() -> None:
    client = _StubClient(
        {
            "data": [
                {
                    "revenue_growth": 0.12,
                    "net_profit_margin": 0.18,
                    "return_on_equity": 0.22,
                    "return_on_invested_capital": 0.14,
                    "price_to_earnings_ratio": 24,
                    "enterprise_value_over_ebitda": 26,
                }
            ]
        }
    )

    result = AisaFundamentalsProvider(client).fetch_assessment("AAPL", now=_NOW)  # type: ignore[arg-type]

    assert result.status == ProviderStatus.OK
    assert result.score_contribution == pytest.approx(100 * 5 / 6)
    assert "EV/EBITDA: desfavoravel" in result.message


def test_fundamentals_provider_never_invents_missing_metrics() -> None:
    client = _StubClient({"data": [{"ticker": "EURUSD"}]})

    result = AisaFundamentalsProvider(client).fetch_assessment("EURUSD", now=_NOW)  # type: ignore[arg-type]

    assert result.status == ProviderStatus.ERROR
    assert result.score_contribution == 50.0
    assert "sem metricas reconhecidas" in result.message
