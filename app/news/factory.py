"""Fabrica dos provedores MarketPulse/AIsa e fallbacks sem configuracao.

A chave pode vir do dashboard (prioridade) ou do ambiente. Quando presente,
ativa os clientes reais de noticias e metricas financeiras; falhas externas
viram fatores neutros e explicitos, nunca dados fabricados.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.aisa import AisaFundamentalsProvider, AisaMarketPulseClient, AisaNewsProvider
from app.news.provider import FundamentalsProvider, NewsProvider
from app.news.unconfigured import UnconfiguredFundamentalsProvider, UnconfiguredNewsProvider

AISA_API_KEY_SETTING = "aisa_api_key"
AISA_API_BASE_URL_SETTING = "aisa_api_base_url"

def _resolve_api_key(session: Session, settings: Settings) -> str | None:
    persisted = SystemSettingRepository(session).get(AISA_API_KEY_SETTING)
    if persisted:
        return persisted
    return settings.aisa_api_key


def _resolve_base_url(session: Session, settings: Settings) -> str | None:
    persisted = SystemSettingRepository(session).get(AISA_API_BASE_URL_SETTING)
    return persisted or settings.aisa_api_base_url


def _client(session: Session, settings: Settings, api_key: str) -> AisaMarketPulseClient:
    return AisaMarketPulseClient(
        api_key=api_key,
        base_url=_resolve_base_url(session, settings),
    )


def get_news_provider(session: Session, settings: Settings) -> NewsProvider:
    api_key = _resolve_api_key(session, settings)
    if not api_key:
        return UnconfiguredNewsProvider()
    return AisaNewsProvider(_client(session, settings, api_key))


def get_fundamentals_provider(session: Session, settings: Settings) -> FundamentalsProvider:
    api_key = _resolve_api_key(session, settings)
    if not api_key:
        return UnconfiguredFundamentalsProvider()
    return AisaFundamentalsProvider(_client(session, settings, api_key))
