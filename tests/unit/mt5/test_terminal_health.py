from app.mt5.terminal_health import detect_account_change, fetch_terminal_health
from tests.fixtures.fake_mt5_client import FakeMT5Client, make_terminal_info


def test_fetch_terminal_health_success() -> None:
    client = FakeMT5Client()
    client.terminal_info_result = make_terminal_info(connected=True, company="Broker X")

    health = fetch_terminal_health(client)

    assert health is not None
    assert health.connected is True
    assert health.company == "Broker X"


def test_fetch_terminal_health_none_when_unavailable() -> None:
    client = FakeMT5Client()
    client.terminal_info_result = None
    client.last_error_result = (-10, "no ipc connection")

    assert fetch_terminal_health(client) is None


def test_detect_account_change() -> None:
    assert detect_account_change(previous_login=None, current_login=123) is False
    assert detect_account_change(previous_login=123, current_login=123) is False
    assert detect_account_change(previous_login=123, current_login=456) is True
