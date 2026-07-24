from app.core.config import Settings
from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.aisa import AisaFundamentalsProvider, AisaNewsProvider
from app.news.factory import (
    AISA_API_KEY_SETTING,
    get_fundamentals_provider,
    get_news_provider,
)
from app.news.unconfigured import UnconfiguredFundamentalsProvider, UnconfiguredNewsProvider


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_no_key_anywhere_returns_unconfigured_provider(db_session) -> None:
    settings = _settings(aisa_api_key=None)

    assert isinstance(get_news_provider(db_session, settings), UnconfiguredNewsProvider)
    assert isinstance(
        get_fundamentals_provider(db_session, settings), UnconfiguredFundamentalsProvider
    )


def test_key_from_env_settings_enables_marketpulse_providers(db_session) -> None:
    settings = _settings(aisa_api_key="some-real-key")

    assert isinstance(get_news_provider(db_session, settings), AisaNewsProvider)
    assert isinstance(get_fundamentals_provider(db_session, settings), AisaFundamentalsProvider)


def test_key_persisted_via_dashboard_takes_priority_over_env(db_session) -> None:
    settings = _settings(aisa_api_key=None)
    SystemSettingRepository(db_session).set(AISA_API_KEY_SETTING, "key-from-dashboard")

    assert isinstance(get_news_provider(db_session, settings), AisaNewsProvider)


def test_empty_string_key_is_treated_as_not_configured(db_session) -> None:
    settings = _settings(aisa_api_key="")
    assert isinstance(get_news_provider(db_session, settings), UnconfiguredNewsProvider)
