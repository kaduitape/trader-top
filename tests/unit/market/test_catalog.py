from app.database.repositories.symbol_repository import SymbolRepository
from app.market.catalog import (
    MARKET_CATALOG,
    catalog_availability,
    grouped_availability,
    resolve_broker_symbol,
)
from app.mt5.symbol_mapper import SymbolSpecification


def _add_symbol(db_session, name: str, *, active: bool = True):
    return SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=name,
            description="Broker symbol",
            digits=2 if name.startswith("XAU") else 5,
            point=0.01 if name.startswith("XAU") else 0.00001,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100_000.0,
            spread=2,
            trade_mode=4,
            visible=active,
        )
    )


def test_catalog_contains_xauusd_and_broad_forex_coverage() -> None:
    codes = {instrument.code for instrument in MARKET_CATALOG}

    assert "XAUUSD" in codes
    assert "EURUSD" in codes
    assert "GBPJPY" in codes
    assert "MNQU6" in codes
    assert "USTEC" in codes
    assert "BTCUSD" in codes
    assert len(codes) >= 30


def test_catalog_matches_broker_suffix_without_inventing_symbol(db_session) -> None:
    synced = _add_symbol(db_session, "XAUUSD.a")

    availability = catalog_availability([synced])
    gold = next(item for item in availability if item.instrument.code == "XAUUSD")

    assert gold.is_available is True
    assert gold.synced_symbol == "XAUUSD.a"


def test_catalog_does_not_mark_inactive_symbol_as_available(db_session) -> None:
    inactive = _add_symbol(db_session, "EURUSD", active=False)

    availability = catalog_availability([inactive])
    euro = next(item for item in availability if item.instrument.code == "EURUSD")

    assert euro.is_available is False
    assert euro.synced_symbol is None


def test_grouped_availability_has_all_market_sections() -> None:
    grouped = grouped_availability([])

    assert set(grouped) == {"MAJORS", "CROSSES", "EXOTICS", "METALS", "INDICES", "CRYPTO"}
    assert any(item.instrument.code == "XAGUSD" for item in grouped["METALS"])
    assert any(item.instrument.code == "USTEC" for item in grouped["INDICES"])
    assert any(item.instrument.code == "BTCUSD" for item in grouped["CRYPTO"])


def test_resolve_broker_symbol_prefers_exact_then_shortest_suffix() -> None:
    names = ["XAUUSD.raw", "XAUUSD.a", "XAUUSD", "EURUSD"]

    assert resolve_broker_symbol("XAUUSD", names) == "XAUUSD"
    assert resolve_broker_symbol("XAUUSD", names[:2]) == "XAUUSD.a"
