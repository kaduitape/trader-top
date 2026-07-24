from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.database.models.system_setting import SystemSetting
from app.mt5.sync_settings import (
    MT5_SYNC_CONFIG_KEY,
    MT5SyncConfig,
    MT5SyncStatus,
    heartbeat_is_fresh,
    load_sync_config,
    load_sync_status,
    save_sync_config,
    save_sync_status,
)


def test_sync_config_defaults_to_nine_timeframes_and_xauusd(db_session) -> None:
    db_session.execute(
        delete(SystemSetting).where(SystemSetting.key == MT5_SYNC_CONFIG_KEY)
    )
    db_session.flush()

    config = load_sync_config(db_session)

    assert "XAUUSD" in config.symbols
    assert config.timeframes == ("MN1", "W1", "D1", "H4", "H1", "M30", "M15", "M5", "M1")
    assert config.enabled is False


def test_sync_config_round_trip(db_session) -> None:
    config = replace(
        MT5SyncConfig(),
        enabled=True,
        symbols=("XAUUSD", "EURJPY"),
        interval_seconds=30,
        collect_ticks=False,
    )

    save_sync_config(db_session, config)
    db_session.flush()

    assert load_sync_config(db_session) == config


def test_sync_status_round_trip_and_fresh_heartbeat(db_session) -> None:
    status = MT5SyncStatus(
        state="ONLINE",
        worker_online=True,
        connected=True,
        heartbeat_at=datetime.now(UTC).isoformat(),
        ready_symbols=2,
    )
    save_sync_status(db_session, status)
    db_session.flush()

    loaded = load_sync_status(db_session)

    assert loaded == status
    assert heartbeat_is_fresh(loaded)


def test_old_heartbeat_is_offline() -> None:
    status = MT5SyncStatus(
        worker_online=True,
        heartbeat_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
    )

    assert not heartbeat_is_fresh(status)
