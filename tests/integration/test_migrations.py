"""Valida que a migration inicial do Alembic cria e remove exatamente o
schema esperado, de forma isolada de `app.core.config` (roda contra uma
engine sqlite propria, nao a do resto da suite)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0001_initial_schema.py"
)

_EXPECTED_TABLES = {"roles", "users", "user_roles", "system_settings", "audit_logs"}


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0001_initial_schema", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_initial_migration_upgrade_creates_expected_tables() -> None:
    migration = _load_migration_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.connect() as connection:
        migration_ctx = MigrationContext.configure(connection)
        with Operations.context(migration_ctx):
            migration.upgrade()

        table_names = set(inspect(connection).get_table_names())
        assert table_names >= _EXPECTED_TABLES


def test_initial_migration_downgrade_removes_all_tables() -> None:
    migration = _load_migration_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.connect() as connection:
        migration_ctx = MigrationContext.configure(connection)
        with Operations.context(migration_ctx):
            migration.upgrade()
            migration.downgrade()

        table_names = set(inspect(connection).get_table_names())
        assert not (_EXPECTED_TABLES & table_names)
