from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.monitoring.preflight import (
    CheckStatus,
    check_database,
    check_directory_writable,
    check_migrations_current,
    check_mt5_credentials,
    check_secret_key,
    run_all_checks,
    worst_status,
)


def _settings(**overrides: object) -> Settings:
    base = {
        "app_secret_key": "a-real-secret-key-not-the-default",
        "db_user": "test",
        "db_password": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_check_secret_key_fails_on_default_placeholder_outside_test_env() -> None:
    settings = _settings(app_secret_key="CHANGE_ME_in_dot_env", app_env="production")
    result = check_secret_key(settings)
    assert result.status == CheckStatus.FAIL


def test_check_secret_key_fails_on_env_example_placeholder() -> None:
    """`.env.example` mostra um placeholder DIFERENTE do default da classe
    `Settings` (`CHANGE_ME_generate_with_openssl_rand_hex_32` vs
    `CHANGE_ME_in_dot_env`) — quem copia o exemplo sem editar cai neste
    valor especificamente, entao a checagem precisa pegar os dois (bug
    real: comparar por igualdade exata so pegava o segundo)."""
    settings = _settings(
        app_secret_key="CHANGE_ME_generate_with_openssl_rand_hex_32", app_env="production"
    )
    result = check_secret_key(settings)
    assert result.status == CheckStatus.FAIL


def test_check_secret_key_ok_with_custom_secret() -> None:
    settings = _settings(app_env="production")
    result = check_secret_key(settings)
    assert result.status == CheckStatus.OK


def test_check_secret_key_ok_in_test_env_even_with_placeholder() -> None:
    settings = _settings(app_secret_key="CHANGE_ME_in_dot_env", app_env="test")
    result = check_secret_key(settings)
    assert result.status == CheckStatus.OK


class _BrokenSession:
    def execute(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("conexao recusada")


def test_check_database_ok_for_working_session(db_session) -> None:
    result = check_database(db_session)
    assert result.status == CheckStatus.OK


def test_check_database_fails_when_execute_raises() -> None:
    result = check_database(_BrokenSession())  # type: ignore[arg-type]
    assert result.status == CheckStatus.FAIL


@pytest.fixture
def isolated_engine_session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_check_migrations_warns_when_no_alembic_version_table(isolated_engine_session) -> None:
    result = check_migrations_current(isolated_engine_session, script_location="alembic")
    assert result.status == CheckStatus.WARN


def test_check_migrations_fails_on_revision_mismatch(isolated_engine_session) -> None:
    isolated_engine_session.execute(
        text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    )
    isolated_engine_session.execute(
        text("INSERT INTO alembic_version (version_num) VALUES ('not_a_real_revision')")
    )
    isolated_engine_session.commit()

    result = check_migrations_current(isolated_engine_session, script_location="alembic")
    assert result.status == CheckStatus.FAIL


def test_check_migrations_ok_when_revision_matches_head(isolated_engine_session) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", "alembic")
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    assert head_revision is not None

    isolated_engine_session.execute(
        text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    )
    isolated_engine_session.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:rev)"), {"rev": head_revision}
    )
    isolated_engine_session.commit()

    result = check_migrations_current(isolated_engine_session, script_location="alembic")
    assert result.status == CheckStatus.OK


def test_check_directory_writable_creates_and_confirms(tmp_path) -> None:
    target = tmp_path / "new_dir"
    result = check_directory_writable("logs", str(target))
    assert result.status == CheckStatus.OK
    assert target.is_dir()


def test_check_directory_writable_fails_when_path_is_a_file(tmp_path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a directory")

    result = check_directory_writable("logs", str(blocked))
    assert result.status == CheckStatus.FAIL


def test_check_mt5_credentials_warns_when_missing() -> None:
    settings = _settings(mt5_login=None, mt5_password=None, mt5_server=None)
    result = check_mt5_credentials(settings)
    assert result.status == CheckStatus.WARN


def test_check_mt5_credentials_ok_when_present() -> None:
    settings = _settings(mt5_login=12345, mt5_password="secret", mt5_server="Broker-Demo")
    result = check_mt5_credentials(settings)
    assert result.status == CheckStatus.OK


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([CheckStatus.OK, CheckStatus.OK], CheckStatus.OK),
        ([CheckStatus.OK, CheckStatus.WARN], CheckStatus.WARN),
        ([CheckStatus.WARN, CheckStatus.FAIL], CheckStatus.FAIL),
        ([CheckStatus.FAIL, CheckStatus.OK, CheckStatus.WARN], CheckStatus.FAIL),
        ([], CheckStatus.OK),
    ],
)
def test_worst_status(statuses: list[CheckStatus], expected: CheckStatus) -> None:
    from app.monitoring.preflight import PreflightCheck

    checks = [PreflightCheck(name=f"c{i}", status=s, detail="") for i, s in enumerate(statuses)]
    assert worst_status(checks) == expected


def test_run_all_checks_returns_one_result_per_check(db_session, tmp_path) -> None:
    settings = _settings(
        log_dir=str(tmp_path / "logs"),
        ml_models_dir=str(tmp_path / "models"),
        ml_datasets_dir=str(tmp_path / "datasets"),
    )
    checks = run_all_checks(settings, db_session)
    names = {c.name for c in checks}
    assert names == {
        "secret_key",
        "database",
        "migrations",
        "log_dir",
        "ml_models_dir",
        "ml_datasets_dir",
        "mt5_credentials",
    }
