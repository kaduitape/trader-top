from app.mt5.symbol_mapper import (
    SymbolSpecification,
    fetch_symbol_specification,
    list_symbols,
    normalize_price,
    normalize_volume,
)
from tests.fixtures.fake_mt5_client import FakeMT5Client, make_symbol_info


def test_list_symbols() -> None:
    client = FakeMT5Client()
    client.symbols_get_result = (make_symbol_info(name="EURUSD"), make_symbol_info(name="GBPUSD"))

    assert list_symbols(client) == ["EURUSD", "GBPUSD"]


def test_list_symbols_returns_empty_when_none() -> None:
    client = FakeMT5Client()
    client.symbols_get_result = None
    client.last_error_result = (-10, "no ipc connection")

    assert list_symbols(client) == []


def test_fetch_symbol_specification() -> None:
    client = FakeMT5Client()
    client.symbol_info_result = make_symbol_info(name="EURUSD", digits=5, volume_step=0.01)

    spec = fetch_symbol_specification(client, "EURUSD")

    assert spec is not None
    assert spec.name == "EURUSD"
    assert spec.digits == 5
    assert spec.volume_step == 0.01


def test_fetch_symbol_specification_none_when_unknown() -> None:
    client = FakeMT5Client()
    client.symbol_info_result = None
    client.last_error_result = (-10, "symbol not found")

    assert fetch_symbol_specification(client, "UNKNOWN") is None


def _eurusd_spec(**overrides: object) -> SymbolSpecification:
    base: dict[str, object] = {
        "name": "EURUSD",
        "description": "",
        "digits": 5,
        "point": 0.00001,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_contract_size": 100_000.0,
        "spread": 2,
        "trade_mode": 4,
        "visible": True,
    }
    base.update(overrides)
    return SymbolSpecification(**base)  # type: ignore[arg-type]


def test_normalize_volume_rounds_to_step() -> None:
    spec = _eurusd_spec()
    assert normalize_volume(0.127, spec) == 0.13


def test_normalize_volume_clamps_to_minimum() -> None:
    spec = _eurusd_spec()
    assert normalize_volume(0.001, spec) == 0.01


def test_normalize_volume_clamps_to_maximum() -> None:
    spec = _eurusd_spec(volume_max=50.0)
    assert normalize_volume(1000.0, spec) == 50.0


def test_normalize_price_rounds_to_symbol_digits() -> None:
    spec = _eurusd_spec(digits=5)
    assert normalize_price(1.234567, spec) == 1.23457
