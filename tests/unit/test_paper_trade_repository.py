from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.database.repositories.paper_trade_repository import PaperTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.mt5.symbol_mapper import SymbolSpecification

_T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _spec(name: str) -> SymbolSpecification:
    return SymbolSpecification(
        name=name,
        description="Test symbol",
        digits=5,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=2,
        trade_mode=4,
        visible=True,
    )


def _symbol_id(db_session, name: str) -> int:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec(name))
    db_session.flush()
    return symbol.id


def test_get_open_returns_none_when_no_position(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_SYM1")
    repo = PaperTradeRepository(db_session)
    assert repo.get_open(symbol_id, "M1", "ema_crossover_baseline") is None


def test_open_position_then_get_open_returns_it(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_SYM2")
    repo = PaperTradeRepository(db_session)

    trade = repo.open_position(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="ema_crossover_baseline",
        model_version="rule-based",
        signal_id="sig-1",
        direction="LONG",
        entry_time=_T0,
        entry_price=Decimal("1.10000000"),
        stop_loss=Decimal("1.09000000"),
        take_profit=Decimal("1.11000000"),
        volume=Decimal("0.01000000"),
    )

    fetched = repo.get_open(symbol_id, "M1", "ema_crossover_baseline")
    assert fetched is not None
    assert fetched.id == trade.id
    assert fetched.status == "OPEN"


def test_close_position_updates_status_and_exit_fields(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_SYM3")
    repo = PaperTradeRepository(db_session)

    trade = repo.open_position(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="ema_crossover_baseline",
        model_version="rule-based",
        signal_id="sig-2",
        direction="LONG",
        entry_time=_T0,
        entry_price=Decimal("1.10000000"),
        stop_loss=Decimal("1.09000000"),
        take_profit=Decimal("1.11000000"),
        volume=Decimal("0.01000000"),
    )

    repo.close_position(
        trade,
        exit_time=_T0 + timedelta(minutes=5),
        exit_price=Decimal("1.11000000"),
        exit_reason="take_profit",
        net_pnl=Decimal("10.00"),
        bars_held=5,
    )

    assert trade.status == "CLOSED"
    assert trade.exit_reason == "take_profit"
    assert float(trade.net_pnl) == 10.00
    assert repo.get_open(symbol_id, "M1", "ema_crossover_baseline") is None


def test_list_recent_orders_by_entry_time_descending(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_SYM4")
    repo = PaperTradeRepository(db_session)

    for i in range(3):
        trade = repo.open_position(
            symbol_id=symbol_id,
            timeframe="M1",
            strategy_name="ema_crossover_baseline",
            model_version="rule-based",
            signal_id=f"sig-{i}",
            direction="LONG",
            entry_time=_T0 + timedelta(minutes=i * 10),
            entry_price=Decimal("1.10000000"),
            stop_loss=Decimal("1.09000000"),
            take_profit=Decimal("1.11000000"),
            volume=Decimal("0.01000000"),
        )
        repo.close_position(
            trade,
            exit_time=_T0 + timedelta(minutes=i * 10 + 1),
            exit_price=Decimal("1.10500000"),
            exit_reason="take_profit",
            net_pnl=Decimal("5.00"),
            bars_held=1,
        )

    recent = repo.list_recent(symbol_id, "M1", "ema_crossover_baseline", limit=10)
    # SQLite devolve datetimes sem timezone (limitacao conhecida do driver,
    # nao dos dados) -- normaliza antes de comparar.
    entry_times = [t.entry_time.replace(tzinfo=UTC) for t in recent]
    assert entry_times == sorted(entry_times, reverse=True)
    assert len(recent) == 3


def test_open_positions_are_scoped_per_strategy(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_SYM5")
    repo = PaperTradeRepository(db_session)

    repo.open_position(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="strategy_a",
        model_version="rule-based",
        signal_id="sig-a",
        direction="LONG",
        entry_time=_T0,
        entry_price=Decimal("1.10000000"),
        stop_loss=Decimal("1.09000000"),
        take_profit=Decimal("1.11000000"),
        volume=Decimal("0.01000000"),
    )

    assert repo.get_open(symbol_id, "M1", "strategy_a") is not None
    assert repo.get_open(symbol_id, "M1", "strategy_b") is None


def test_list_all_recent_spans_multiple_symbols_and_resolves_names(db_session) -> None:
    repo = PaperTradeRepository(db_session)
    symbol_a_id = _symbol_id(db_session, "PAPER_SYM6A")
    symbol_b_id = _symbol_id(db_session, "PAPER_SYM6B")

    repo.open_position(
        symbol_id=symbol_a_id,
        timeframe="M1",
        strategy_name="strategy_a",
        model_version="rule-based",
        signal_id="sig-a",
        direction="LONG",
        entry_time=_T0,
        entry_price=Decimal("1.10000000"),
        stop_loss=Decimal("1.09000000"),
        take_profit=Decimal("1.11000000"),
        volume=Decimal("0.01000000"),
    )
    repo.open_position(
        symbol_id=symbol_b_id,
        timeframe="M1",
        strategy_name="strategy_b",
        model_version="rule-based",
        signal_id="sig-b",
        direction="SHORT",
        entry_time=_T0 + timedelta(minutes=1),
        entry_price=Decimal("1.20000000"),
        stop_loss=Decimal("1.21000000"),
        take_profit=Decimal("1.19000000"),
        volume=Decimal("0.02000000"),
    )

    results = repo.list_all_recent(limit=10)
    symbol_names = {name for _, name in results}
    assert {"PAPER_SYM6A", "PAPER_SYM6B"} <= symbol_names
    # Mais recente primeiro.
    assert results[0][1] == "PAPER_SYM6B"
