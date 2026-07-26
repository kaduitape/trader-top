from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.execution.order_state import OrderState
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


def test_get_active_position_returns_none_when_none_exists(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM1")
    repo = LiveTradeRepository(db_session)
    assert repo.get_active_position(symbol_id, "M1", "ema_crossover_baseline") is None


def test_create_risk_rejected_row_is_not_active(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM2")
    repo = LiveTradeRepository(db_session)

    trade = repo.create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="ema_crossover_baseline",
        model_version="rule-based",
        signal_id="sig-1",
        direction="LONG",
        order_state=OrderState.RISK_REJECTED,
        signal_time=_T0,
        rejection_reason="circuit breaker ativo",
    )

    assert trade.rejection_reason == "circuit breaker ativo"
    assert repo.get_active_position(symbol_id, "M1", "ema_crossover_baseline") is None


def test_create_position_open_is_active(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM3")
    repo = LiveTradeRepository(db_session)

    trade = repo.create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="ema_crossover_baseline",
        model_version="rule-based",
        signal_id="sig-2",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_T0,
        mt5_order_ticket=1001,
        mt5_position_ticket=3001,
        entry_time=_T0,
        entry_price=Decimal("1.10000000"),
        stop_loss=Decimal("1.09000000"),
        take_profit=Decimal("1.11000000"),
        volume=Decimal("0.01000000"),
    )

    active = repo.get_active_position(symbol_id, "M1", "ema_crossover_baseline")
    assert active is not None
    assert active.id == trade.id
    assert active.mt5_position_ticket == 3001


def test_mark_reconciling_keeps_position_active(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM4")
    repo = LiveTradeRepository(db_session)

    trade = repo.create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="s",
        model_version="rule-based",
        signal_id="sig-3",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_T0,
        mt5_position_ticket=3002,
        entry_time=_T0,
    )
    repo.mark_reconciling(trade)

    assert trade.order_state == OrderState.RECONCILING.value
    active = repo.get_active_position(symbol_id, "M1", "s")
    assert active is not None
    assert active.id == trade.id


def test_close_position_removes_it_from_active(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM5")
    repo = LiveTradeRepository(db_session)

    trade = repo.create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="s",
        model_version="rule-based",
        signal_id="sig-4",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_T0,
        mt5_position_ticket=3003,
        entry_time=_T0,
        entry_price=Decimal("1.10000000"),
    )
    repo.close_position(
        trade,
        exit_time=_T0 + timedelta(minutes=5),
        exit_price=Decimal("1.10500000"),
        net_pnl=Decimal("50.00"),
    )

    assert trade.order_state == OrderState.CLOSED.value
    assert repo.get_active_position(symbol_id, "M1", "s") is None


def test_count_entries_since_counts_regardless_of_current_state(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM6")
    repo = LiveTradeRepository(db_session)

    opened_and_closed = repo.create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="s",
        model_version="rule-based",
        signal_id="sig-5",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_T0,
        entry_time=_T0,
    )
    repo.close_position(
        opened_and_closed,
        exit_time=_T0 + timedelta(minutes=1),
        exit_price=Decimal("1.1"),
        net_pnl=Decimal("1.00"),
    )
    repo.create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="s",
        model_version="rule-based",
        signal_id="sig-6",
        direction="LONG",
        order_state=OrderState.RISK_REJECTED,
        signal_time=_T0 + timedelta(minutes=2),
        # sem entry_time -- sinal rejeitado nunca chegou a abrir posicao
    )

    count = repo.count_entries_since(
        symbol_id, "M1", "s", since=_T0.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    assert count == 1  # so o que teve entry_time conta


def test_sum_net_pnl_since_only_counts_closed_trades(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM7")
    repo = LiveTradeRepository(db_session)

    trade = repo.create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="s",
        model_version="rule-based",
        signal_id="sig-7",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_T0,
        entry_time=_T0,
    )
    repo.close_position(
        trade,
        exit_time=_T0 + timedelta(minutes=1),
        exit_price=Decimal("1.1"),
        net_pnl=Decimal("-25.50"),
    )

    total = repo.sum_net_pnl_since(
        symbol_id, "M1", "s", since=_T0.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    assert total == -25.50


def test_get_recent_closed_orders_by_exit_time_descending(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM8")
    repo = LiveTradeRepository(db_session)

    for i in range(3):
        trade = repo.create(
            symbol_id=symbol_id,
            timeframe="M1",
            strategy_name="s",
            model_version="rule-based",
            signal_id=f"sig-{i}",
            direction="LONG",
            order_state=OrderState.POSITION_OPEN,
            signal_time=_T0 + timedelta(minutes=i * 10),
            entry_time=_T0 + timedelta(minutes=i * 10),
        )
        repo.close_position(
            trade,
            exit_time=_T0 + timedelta(minutes=i * 10 + 1),
            exit_price=Decimal("1.1"),
            net_pnl=Decimal("-1.00"),
        )

    recent = repo.get_recent_closed(symbol_id, "M1", "s", limit=10)
    assert len(recent) == 3
    exit_times = [t.exit_time.replace(tzinfo=UTC) for t in recent]
    assert exit_times == sorted(exit_times, reverse=True)


def test_get_last_entry_time_returns_none_when_never_entered(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM9")
    repo = LiveTradeRepository(db_session)
    assert repo.get_last_entry_time(symbol_id, "M1", "s") is None


def test_list_recent_scoped_by_strategy(db_session) -> None:
    symbol_id = _symbol_id(db_session, "LIVE_SYM10")
    repo = LiveTradeRepository(db_session)

    repo.create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name="strategy_a",
        model_version="rule-based",
        signal_id="sig-a",
        direction="LONG",
        order_state=OrderState.RISK_REJECTED,
        signal_time=_T0,
        rejection_reason="x",
    )

    assert len(repo.list_recent(symbol_id, "M1", "strategy_a")) == 1
    assert len(repo.list_recent(symbol_id, "M1", "strategy_b")) == 0


def test_list_all_recent_spans_multiple_symbols_and_resolves_names(db_session) -> None:
    repo = LiveTradeRepository(db_session)
    symbol_a_id = _symbol_id(db_session, "LIVE_SYM11A")
    symbol_b_id = _symbol_id(db_session, "LIVE_SYM11B")

    repo.create(
        symbol_id=symbol_a_id,
        timeframe="M1",
        strategy_name="strategy_a",
        model_version="rule-based",
        signal_id="sig-a",
        direction="LONG",
        order_state=OrderState.RISK_REJECTED,
        signal_time=_T0,
        rejection_reason="x",
    )
    repo.create(
        symbol_id=symbol_b_id,
        timeframe="M1",
        strategy_name="strategy_b",
        model_version="rule-based",
        signal_id="sig-b",
        direction="SHORT",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_T0 + timedelta(minutes=1),
        entry_time=_T0 + timedelta(minutes=1),
        mt5_position_ticket=5001,
    )

    results = repo.list_all_recent(limit=10)
    symbol_names = {name for _, name in results}
    assert {"LIVE_SYM11A", "LIVE_SYM11B"} <= symbol_names
    assert results[0][1] == "LIVE_SYM11B"


# --- Escopo entre timeframes (piloto automatico) --------------------------
#
# O piloto automatico troca de timeframe entre ciclos conforme o horario e o
# volume. Se os limites de risco continuassem contados por timeframe, mudar
# de M5 para M15 zeraria os contadores do dia e esconderia a posicao aberta
# — os testes abaixo travam o comportamento que impede isso.


def _entry(db_session, symbol_id: int, timeframe: str, *, at: datetime) -> None:
    LiveTradeRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe=timeframe,
        strategy_name="autopilot",
        model_version="test",
        signal_id=f"sig-{timeframe}-{at.isoformat()}",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=at,
        entry_time=at,
        entry_price=Decimal("1.1000"),
        volume=Decimal("0.10"),
        mt5_position_ticket=hash((timeframe, at)) % 100_000,
    )


def test_active_position_is_found_across_timeframes(db_session) -> None:
    symbol_id = _symbol_id(db_session, "EURUSD_SCOPE_1")
    at = datetime(2026, 7, 22, 10, 0)
    _entry(db_session, symbol_id, "M5", at=at)
    repository = LiveTradeRepository(db_session)

    # Escopo por timeframe: M15 nao ve a posicao aberta em M5.
    assert repository.get_active_position(symbol_id, "M15", "autopilot") is None
    # Escopo do simbolo inteiro (timeframe=None): ve.
    assert repository.get_active_position(symbol_id, None, "autopilot") is not None


def test_daily_counters_span_timeframes_when_scope_is_none(db_session) -> None:
    symbol_id = _symbol_id(db_session, "EURUSD_SCOPE_2")
    start_of_day = datetime(2026, 7, 22, 0, 0)
    _entry(db_session, symbol_id, "M5", at=datetime(2026, 7, 22, 10, 0))
    _entry(db_session, symbol_id, "M15", at=datetime(2026, 7, 22, 11, 0))
    _entry(db_session, symbol_id, "M30", at=datetime(2026, 7, 22, 12, 0))
    repository = LiveTradeRepository(db_session)

    assert (
        repository.count_entries_since(
            symbol_id, "M5", "autopilot", since=start_of_day
        )
        == 1
    )
    assert (
        repository.count_entries_since(
            symbol_id, None, "autopilot", since=start_of_day
        )
        == 3
    )


def test_last_entry_time_spans_timeframes_when_scope_is_none(db_session) -> None:
    symbol_id = _symbol_id(db_session, "EURUSD_SCOPE_3")
    _entry(db_session, symbol_id, "M5", at=datetime(2026, 7, 22, 10, 0))
    _entry(db_session, symbol_id, "M15", at=datetime(2026, 7, 22, 15, 0))
    repository = LiveTradeRepository(db_session)

    assert repository.get_last_entry_time(symbol_id, "M5", "autopilot") == datetime(
        2026, 7, 22, 10, 0
    )
    assert repository.get_last_entry_time(symbol_id, None, "autopilot") == datetime(
        2026, 7, 22, 15, 0
    )
