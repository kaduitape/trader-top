"""Configuracao tipada da aplicacao.

Toda variavel de ambiente usada pelo sistema passa por aqui. Nenhum outro
modulo deve chamar `os.environ` diretamente — isso garante que a validacao
(via Pydantic) e o valor por omissao fiquem centralizados e testaveis, e que
segredos nunca sejam lidos de forma ad-hoc espalhada pelo codigo.

A aplicacao falha rapido (ValidationError na inicializacao) se uma variavel
obrigatoria estiver ausente, em vez de operar com valores indefinidos.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import SystemMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Aplicacao ---------------------------------------------------------
    app_name: str = "MT5 AI Scalper"
    app_env: str = "development"
    app_debug: bool = True
    app_secret_key: str = Field(default="CHANGE_ME_in_dot_env", min_length=8)
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    system_mode: SystemMode = SystemMode.DISABLED

    # --- Autenticacao / JWT --------------------------------------------------
    auth_jwt_algorithm: str = "HS256"
    auth_access_token_expire_minutes: int = 30

    # ATENCAO: bypass temporario de desenvolvimento -- nunca True em producao.
    # Quando True, `get_current_user_for_web` (dashboard HTML) libera acesso
    # sem cookie/login, autenticando automaticamente como um usuario "dev"
    # (criado sob demanda). Nao afeta `/api/*` (JWT via Authorization
    # continua exigido normalmente).
    dashboard_auth_disabled: bool = False

    # --- Banco de dados ------------------------------------------------------
    db_driver: str = "mysql+pymysql"
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "mt5_ai_scalper"
    db_user: str = "CHANGE_ME"
    db_password: str = "CHANGE_ME"
    db_pool_size: int = 5
    db_pool_max_overflow: int = 10
    db_echo: bool = False

    # --- Logging ---------------------------------------------------------------
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_json: bool = True

    # --- MetaTrader 5 (conector somente leitura a partir da Fase 2) ------------
    mt5_terminal_path: str | None = None
    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_timeout_ms: int = 10_000
    mt5_max_reconnect_attempts: int = 5
    mt5_reconnect_backoff_seconds: float = 2.0
    mt5_heartbeat_interval_seconds: float = 30.0

    # --- Qualidade de dados e retencao (Fase 3) --------------------------------
    quality_max_spread_points: float = 50.0
    quality_max_feed_delay_seconds: int = 300
    quality_min_score: int = 70
    tick_retention_days: int = 30

    # --- Machine learning (Fase 8) ---------------------------------------------
    ml_datasets_dir: str = "datasets"
    ml_models_dir: str = "models"

    # --- Noticias/fundamentos externos (Fase 18.6) ------------------------------
    # Fallback via .env; a chave tambem pode ser configurada em runtime via
    # /dashboard/settings/aisa (persistida em system_settings, tem prioridade
    # sobre este valor -- ver app/news/factory.py).
    aisa_api_key: str | None = None
    aisa_api_base_url: str | None = None

    # --- Motor de analise Price Action / SMC / multi-timeframe (Fase 18) --------
    analysis_default_timeframe: str = "M15"
    analysis_default_threshold: float = 90.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_test_env(self) -> bool:
        return self.app_env.lower() == "test"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """URL de conexao SQLAlchemy.

        Em ambiente de teste (`APP_ENV=test`), usa SQLite em memoria — o
        projeto ainda nao tem um MySQL 8 disponivel neste momento (ver
        docs/assumptions.md secao 2.2). A URL de producao continua sendo
        MySQL, montada a partir das variaveis DB_*.
        """
        if self.is_test_env:
            return "sqlite+pysqlite:///:memory:"
        return (
            f"{self.db_driver}://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    """Retorna a instancia (cacheada) de configuracao da aplicacao."""
    return Settings()
