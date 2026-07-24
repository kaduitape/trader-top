from app.core.config import get_settings
from app.core.enums import SystemMode


def test_settings_load_from_environment() -> None:
    settings = get_settings()
    assert settings.app_env == "test"
    assert settings.is_test_env is True


def test_database_url_uses_sqlite_in_test_env() -> None:
    settings = get_settings()
    assert settings.database_url.startswith("sqlite")


def test_system_mode_defaults_to_disabled() -> None:
    settings = get_settings()
    # Nao definimos SYSTEM_MODE no ambiente de teste; o padrao seguro e
    # sempre DISABLED (nunca um modo ativo por omissao).
    assert settings.system_mode == SystemMode.DISABLED


def test_aisa_api_settings_default_to_unconfigured() -> None:
    settings = get_settings()
    # Sem preencher .env, nenhuma chave existe -- o provedor de
    # noticias/fundamentos (Fase 18.6) deve cair no stub "nao configurado",
    # nunca falhar a inicializacao por causa disso.
    assert settings.aisa_api_key is None
    assert settings.aisa_api_base_url is None


def test_analysis_defaults() -> None:
    settings = get_settings()
    assert settings.analysis_default_timeframe == "M15"
    assert settings.analysis_default_threshold == 90.0


def test_dashboard_auth_disabled_defaults_to_false() -> None:
    # Bypass de desenvolvimento (login do dashboard) nunca pode vir
    # habilitado por omissao -- exige opt-in explicito no .env.
    settings = get_settings()
    assert settings.dashboard_auth_disabled is False
