from types import SimpleNamespace

from app.mt5.positions import fetch_open_positions
from tests.fixtures.fake_mt5_client import FakeMT5Client


def test_fetch_open_positions_converts_rows() -> None:
    client = FakeMT5Client()
    client.positions_get_result = (
        SimpleNamespace(
            ticket=1,
            symbol="EURUSD",
            volume=0.1,
            price_open=1.1000,
            price_current=1.1010,
            profit=1.0,
            swap=0.0,
            type=0,
            time=1_700_000_000,
            magic=123,
            comment="test",
        ),
    )

    positions = fetch_open_positions(client)

    assert len(positions) == 1
    assert positions[0].ticket == 1
    assert positions[0].symbol == "EURUSD"
    assert positions[0].profit == 1.0


def test_fetch_open_positions_returns_empty_when_none() -> None:
    client = FakeMT5Client()
    client.positions_get_result = None
    client.last_error_result = (-10, "no ipc connection")

    assert fetch_open_positions(client) == []
