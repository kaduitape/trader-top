"""Valida a migration 0004 (paper_trades), encadeada sobre 0001+0002+0003,
isolada de `app.core.config` (engine sqlite propria)."""

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
)


def _load_migration_module(filename: str) -> ModuleType:
    path = _VERSIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(f"migration_{filename}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_paper_trades_migration_upgrade_creates_table() -> None:
    migrations = [_load_migration_module(name) for name in _ALL_MIGRATIONS]
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.connect() as connection:
        migration_ctx = MigrationContext.configure(connection)
        with Operations.context(migration_ctx):
            for migration in migrations:
                migration.upgrade()

        table_names = set(inspect(connection).get_table_names())
        assert "paper_trades" in table_names


def test_paper_trades_migration_downgrade_removes_only_its_table() -> None:
    migrations = [_load_migration_module(name) for name in _ALL_MIGRATIONS]
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.connect() as connection:
        migration_ctx = MigrationContext.configure(connection)
        with Operations.context(migration_ctx):
            for migration in migrations:
                migration.upgrade()
            migrations[-1].downgrade()

        table_names = set(inspect(connection).get_table_names())
        assert "paper_trades" not in table_names
        assert {"symbols", "candles", "ticks", "data_quality_events"} <= table_names
