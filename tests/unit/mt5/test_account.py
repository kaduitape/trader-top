import logging

import pytest

from app.mt5.account import fetch_account_snapshot
from tests.fixtures.fake_mt5_client import FakeMT5Client, make_account_info


def test_fetch_account_snapshot_demo() -> None:
    client = FakeMT5Client()
    client.account_info_result = make_account_info(trade_mode=client.ACCOUNT_TRADE_MODE_DEMO)

    snapshot = fetch_account_snapshot(client)

    assert snapshot is not None
    assert snapshot.is_demo is True
    assert snapshot.login == 12345678


def test_fetch_account_snapshot_real_is_flagged_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeMT5Client()
    client.account_info_result = make_account_info(trade_mode=client.ACCOUNT_TRADE_MODE_REAL)

    with caplog.at_level(logging.WARNING, logger="app.mt5.account"):
        snapshot = fetch_account_snapshot(client)

    assert snapshot is not None
    assert snapshot.is_demo is False
    assert any("mt5_account_is_real" in record.getMessage() for record in caplog.records)


def test_fetch_account_snapshot_none_when_unavailable() -> None:
    client = FakeMT5Client()
    client.account_info_result = None
    client.last_error_result = (-10, "no ipc connection")

    assert fetch_account_snapshot(client) is None
