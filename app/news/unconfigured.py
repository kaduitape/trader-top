"""Implementacoes honestas de `NewsProvider`/`FundamentalsProvider` para
quando nenhuma chave de API esta configurada — nunca fabricam noticia,
sentimento ou fundamento algum."""

from __future__ import annotations

from datetime import datetime

from app.news.provider import FundamentalsAssessment, NewsAssessment, ProviderStatus

_MESSAGE_NEWS = (
    "AISA_API_KEY nao configurada — fator de noticias contribui neutro " "(nenhum dado inventado)."
)
_MESSAGE_FUNDAMENTALS = (
    "AISA_API_KEY nao configurada — fator de fundamentos contribui neutro "
    "(nenhum dado inventado)."
)


class UnconfiguredNewsProvider:
    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        return NewsAssessment(
            status=ProviderStatus.NOT_CONFIGURED,
            score_contribution=50.0,
            items=[],
            message=_MESSAGE_NEWS,
        )


class UnconfiguredFundamentalsProvider:
    def fetch_assessment(self, symbol: str, *, now: datetime) -> FundamentalsAssessment:
        return FundamentalsAssessment(
            status=ProviderStatus.NOT_CONFIGURED,
            score_contribution=50.0,
            message=_MESSAGE_FUNDAMENTALS,
        )


def skipped_news(reason: str) -> NewsAssessment:
    """Fator de noticias quando a API NAO foi consultada de proposito."""
    return NewsAssessment(
        status=ProviderStatus.SKIPPED,
        score_contribution=50.0,
        items=[],
        message=reason,
    )


def skipped_fundamentals(reason: str) -> FundamentalsAssessment:
    return FundamentalsAssessment(
        status=ProviderStatus.SKIPPED,
        score_contribution=50.0,
        message=reason,
    )
