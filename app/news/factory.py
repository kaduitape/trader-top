"""Fabrica dos provedores MarketPulse/AIsa e fallbacks sem configuracao.

A chave pode vir do dashboard (prioridade) ou do ambiente. Quando presente,
ativa os clientes reais de noticias e metricas financeiras; falhas externas
viram fatores neutros e explicitos, nunca dados fabricados.
"""

from __future__ import annotations

from threading import Lock

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.aisa import AisaFundamentalsProvider, AisaMarketPulseClient, AisaNewsProvider
from app.news.budget import BudgetedProvider, read_usage
from app.news.cache import (
    AssessmentCache,
    CachedFundamentalsProvider,
    CachedNewsProvider,
)
from app.news.provider import FundamentalsProvider, NewsProvider
from app.news.unconfigured import (
    UnconfiguredFundamentalsProvider,
    UnconfiguredNewsProvider,
    skipped_fundamentals,
    skipped_news,
)

AISA_API_KEY_SETTING = "aisa_api_key"
AISA_API_BASE_URL_SETTING = "aisa_api_base_url"

# Um cache por processo, compartilhado por todas as requisicoes/ciclos: e o
# que faz recarregar a tela de analise (ou rodar o piloto a cada 15s) parar
# de virar uma chamada HTTP nova a cada vez.
_CACHE: AssessmentCache | None = None
_CACHE_LOCK = Lock()


def get_assessment_cache(settings: Settings) -> AssessmentCache:
    """Instancia unica, criada na primeira chamada com o TTL configurado."""
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None or _CACHE.ttl_seconds != settings.news_cache_ttl_seconds:
            _CACHE = AssessmentCache(ttl_seconds=settings.news_cache_ttl_seconds)
        return _CACHE


def reset_assessment_cache() -> None:
    """Descarta o cache — usado pelos testes e ao trocar a chave da API."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None

def _resolve_api_key(session: Session, settings: Settings) -> str | None:
    persisted = SystemSettingRepository(session).get(AISA_API_KEY_SETTING)
    if persisted:
        return persisted
    return settings.aisa_api_key


def _resolve_base_url(session: Session, settings: Settings) -> str | None:
    persisted = SystemSettingRepository(session).get(AISA_API_BASE_URL_SETTING)
    return persisted or settings.aisa_api_base_url


def _namespace(session: Session, settings: Settings) -> str:
    """Separa o cache por endpoint: trocar a URL da API nunca pode servir
    resposta guardada do endpoint anterior."""
    return _resolve_base_url(session, settings) or "default"


def _client(session: Session, settings: Settings, api_key: str) -> AisaMarketPulseClient:
    return AisaMarketPulseClient(
        api_key=api_key,
        base_url=_resolve_base_url(session, settings),
    )


def get_news_provider(session: Session, settings: Settings) -> NewsProvider:
    api_key = _resolve_api_key(session, settings)
    if not api_key:
        # Sem chave nao existe chamada HTTP para economizar.
        return UnconfiguredNewsProvider()
    # Ordem importa: cache por fora (acerto nao gasta orcamento), teto
    # diario por dentro (so requisicao real conta).
    return CachedNewsProvider(
        BudgetedProvider(
            AisaNewsProvider(_client(session, settings, api_key)),
            limit=settings.news_daily_call_budget,
            skipped_factory=skipped_news,
            kind="noticias",
        ),
        get_assessment_cache(settings),
        namespace=_namespace(session, settings),
    )


def get_fundamentals_provider(session: Session, settings: Settings) -> FundamentalsProvider:
    api_key = _resolve_api_key(session, settings)
    if not api_key:
        return UnconfiguredFundamentalsProvider()
    return CachedFundamentalsProvider(
        BudgetedProvider(
            AisaFundamentalsProvider(_client(session, settings, api_key)),
            limit=settings.news_daily_call_budget,
            skipped_factory=skipped_fundamentals,
            kind="fundamentos",
        ),
        get_assessment_cache(settings),
        namespace=_namespace(session, settings),
    )


def get_budget_usage(session: Session, settings: Settings):
    """Consumo do dia, para o painel mostrar quanto ainda resta."""
    return read_usage(session, limit=settings.news_daily_call_budget)
