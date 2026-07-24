"""Valida a migration 0002 (symbols/candles/ticks), encadeada sobre a 0001,
isolada de `app.core.config` (engine sqlite propria)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"

_EXPECTED_TABLES = {"symbols", "candles", "ticks"}


def _load_migration_module(filename: str) -> ModuleType:
    path = _VERSIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(f"migration_{filename}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_market_data_migration_upgrade_creates_expected_tables() -> None:
    migration_0001 = _load_migration_module("0001_initial_schema.py")
    migration_0002 = _load_migration_module("0002_market_data_schema.py")
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.connect() as connection:
        migration_ctx = MigrationContext.configure(connection)
        with Operations.context(migration_ctx):
            migration_0001.upgrade()
            migration_0002.upgrade()

        table_names = set(inspect(connection).get_table_names())
        assert table_names >= _EXPECTED_TABLES


def test_market_data_migration_downgrade_removes_its_tables() -> None:
    migration_0001 = _load_migration_module("0001_initial_schema.py")
    migration_0002 = _load_migration_module("0002_market_data_schema.py")
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.connect() as connection:
        migration_ctx = MigrationContext.configure(connection)
        with Operations.context(migration_ctx):
            migration_0001.upgrade()
            migration_0002.upgrade()
            migration_0002.downgrade()

        table_names = set(inspect(connection).get_table_names())
        assert not (_EXPECTED_TABLES & table_names)
        # 0001 continua intacta — downgrade da 0002 nao deve afetar users/roles.
        assert {"users", "roles"} <= table_names
