from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import Settings
from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.aisa import AisaFundamentalsProvider, AisaNewsProvider
from app.news.factory import (
    AISA_API_KEY_SETTING,
    get_assessment_cache,
    get_fundamentals_provider,
    get_news_provider,
    reset_assessment_cache,
)
from app.news.unconfigured import UnconfiguredFundamentalsProvider, UnconfiguredNewsProvider


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _inner(provider: object) -> object:
    """O provedor real no fim da cadeia cache -> orcamento -> cliente HTTP.
    As duas camadas sao transparentes para quem consome."""
    while hasattr(provider, "_inner"):
        provider = provider._inner  # type: ignore[attr-defined]
    return provider


@pytest.fixture(autouse=True)
def _clean_cache():
    """O cache e por processo: sem limpar, um teste enxergaria o TTL
    configurado por outro."""
    reset_assessment_cache()
    yield
    reset_assessment_cache()


def test_no_key_anywhere_returns_unconfigured_provider(db_session) -> None:
    settings = _settings(aisa_api_key=None)

    assert isinstance(get_news_provider(db_session, settings), UnconfiguredNewsProvider)
    assert isinstance(
        get_fundamentals_provider(db_session, settings), UnconfiguredFundamentalsProvider
    )


def test_key_from_env_settings_enables_marketpulse_providers(db_session) -> None:
    settings = _settings(aisa_api_key="some-real-key")

    assert isinstance(_inner(get_news_provider(db_session, settings)), AisaNewsProvider)
    assert isinstance(
        _inner(get_fundamentals_provider(db_session, settings)), AisaFundamentalsProvider
    )


def test_key_persisted_via_dashboard_takes_priority_over_env(db_session) -> None:
    settings = _settings(aisa_api_key=None)
    SystemSettingRepository(db_session).set(AISA_API_KEY_SETTING, "key-from-dashboard")

    assert isinstance(_inner(get_news_provider(db_session, settings)), AisaNewsProvider)


def test_empty_string_key_is_treated_as_not_configured(db_session) -> None:
    settings = _settings(aisa_api_key="")
    assert isinstance(get_news_provider(db_session, settings), UnconfiguredNewsProvider)


def test_every_caller_shares_the_same_cache(db_session) -> None:
    """Duas requisicoes do dashboard criam provedores novos; se cada um
    tivesse o proprio cache, o cache nao economizaria nada."""
    settings = _settings(aisa_api_key="some-real-key")

    first = get_news_provider(db_session, settings)
    second = get_news_provider(db_session, settings)

    # O guarda de cobertura envolve o provedor com cache; o cache
    # compartilhado esta uma camada abaixo dele.
    assert first._inner._cache is second._inner._cache  # type: ignore[attr-defined]


def test_the_configured_ttl_reaches_the_cache(db_session) -> None:
    settings = _settings(aisa_api_key="some-real-key", news_cache_ttl_seconds=42.0)

    get_news_provider(db_session, settings)

    assert get_assessment_cache(settings).ttl_seconds == 42.0


def test_changing_the_ttl_rebuilds_the_cache(db_session) -> None:
    dez_minutos = get_assessment_cache(_settings(news_cache_ttl_seconds=600.0))
    desligado = get_assessment_cache(_settings(news_cache_ttl_seconds=0.0))

    assert dez_minutos is not desligado
    assert desligado.ttl_seconds == 0.0


def test_a_new_request_reuses_the_answer_instead_of_calling_the_api_again(
    db_session, monkeypatch
) -> None:
    """O cenario real: cada carregamento da tela de analise constroi um
    provedor novo. Sem cache compartilhado, cada F5 era uma chamada HTTP.

    Usa um ticker de acao de proposito: par de moedas nao chega mais a
    camada HTTP (a API da AIsa nao cobre cambio), entao mediria o guarda de
    cobertura em vez do cache.
    """
    calls: list[str] = []

    def fake_get(url, **kwargs):  # noqa: ANN001, ANN003
        calls.append(url)
        return httpx.Response(
            200,
            json={"data": [{"title": "manchete", "sentiment": 0.4}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    settings = _settings(aisa_api_key="some-real-key")
    now = datetime.now(UTC)

    get_news_provider(db_session, settings).fetch_assessment("AAPL", now=now)
    get_news_provider(db_session, settings).fetch_assessment("AAPL", now=now)
    get_news_provider(db_session, settings).fetch_assessment("AAPL", now=now)

    assert len(calls) == 1
