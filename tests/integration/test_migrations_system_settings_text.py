"""Valida as migrations 0008 (system_settings.value -> TEXT) e 0009
(apexflow_decisions), encadeadas sobre 0001-0007, isoladas de
`app.core.config` (engine sqlite propria)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"

_ALL_MIGRATIONS = (
    "0001_initial_schema.py",
    "0002_market_data_schema.py",
    "0003_data_quality_events.py",
    "0004_paper_trades.py",
    "0005_live_trades.py",
    "0006_drift_events.py",
    "0007_ticks_microsecond_precision.py",
    "0008_system_settings_text_value.py",
    "0009_apexflow_decisions.py",
)


def _load_migration_module(filename: str) -> ModuleType:
    path = _VERSIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(f"migration_{filename}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgraded_connection(connection) -> list[ModuleType]:
    migrations = [_load_migration_module(name) for name in _ALL_MIGRATIONS]
    migration_ctx = MigrationContext.configure(connection)
    with Operations.context(migration_ctx):
        for migration in migrations:
            migration.upgrade()
    return migrations


def test_migration_chain_applies_and_keeps_system_settings() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        _upgraded_connection(connection)
        table_names = set(inspect(connection).get_table_names())
        assert "system_settings" in table_names
        assert "apexflow_decisions" in table_names


def test_apexflow_decisions_downgrade_removes_only_its_table() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        migrations = _upgraded_connection(connection)
        migration_ctx = MigrationContext.configure(connection)
        with Operations.context(migration_ctx):
            migrations[-1].downgrade()
        table_names = set(inspect(connection).get_table_names())
        assert "apexflow_decisions" not in table_names
        assert {"symbols", "live_trades", "system_settings"} <= table_names


def test_apexflow_decisions_revision_is_chained_after_0008() -> None:
    module = _load_migration_module("0009_apexflow_decisions.py")
    assert module.revision == "0009"
    assert module.down_revision == "0008"


def test_system_settings_migration_is_a_no_op_on_sqlite() -> None:
    """No SQLite o tipo declarado e so afinidade — a 0008 nao deve tentar
    (nem precisar) alterar nada, e por isso e reversivel sem efeito."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        _upgraded_connection(connection)
        module = _load_migration_module("0008_system_settings_text_value.py")
        migration_ctx = MigrationContext.configure(connection)
        with Operations.context(migration_ctx):
            module.downgrade()
        assert "system_settings" in set(inspect(connection).get_table_names())


def test_revision_is_chained_after_0007() -> None:
    module = _load_migration_module("0008_system_settings_text_value.py")
    assert module.revision == "0008"
    assert module.down_revision == "0007"
