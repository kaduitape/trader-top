from datetime import UTC, datetime, timedelta

from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.execution.engine import (
    DemoExecutionEngine,
    OrderRejectedByBroker,
    PositionClosed,
    PositionOpened,
    PositionReconciling,
    SignalRejected,
)
from app.execution.order_state import OrderState
from app.mt5.account import AccountSnapshot
from app.mt5.market_data import RawCandle
from app.mt5.symbol_mapper import SymbolSpecification
from app.risk.config import RiskLimits
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy
from tests.fixtures.fake_mt5_client import (
    FakeMT5Client,
    make_history_deal,
    make_order_send_result,
    make_position,
)

_START = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001
_ACCOUNT = AccountSnapshot(
    login=1,
    server="Test-Demo",
    balance=10_000.0,
    equity=10_000.0,
    margin=0.0,
    margin_free=10_000.0,
    currency="USD",
    leverage=100,
    trade_mode=0,
    is_demo=True,
)
# `max_feed_delay_seconds` desabilitado (numero grande) por padrao nestes
# testes: as candles sinteticas sao datadas em 2026-01-05, muito antes do
# "agora" real usado pelo clock padrao do motor (`datetime.now(UTC)`) --
# testes especificos da checagem de saude do feed (Fase 13) injetam seu
# proprio `clock` e um limite realista.
_LIMITS = RiskLimits(max_feed_delay_seconds=10**9)


def _candle(
    minute_offset: int, o: float, h: float, low: float, c: float, spread: int = 2
) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=minute_offset),
        open=o,
        high=h,
        low=low,
        close=c,
        tick_volume=100,
        spread=spread,
        real_volume=0,
    )


class _NeverSignalStrategy(Strategy):
    name = "test_never"

    def generate_signal(self, state: MarketState) -> Signal | None:
        return None


class _TriggerAtTimesStrategy(Strategy):
    name = "test_trigger_at_times"

    def __init__(
        self, trigger_times: set[datetime], *, stop_loss: float, take_profit: float
    ) -> None:
        self._trigger_times = trigger_times
        self._stop_loss = stop_loss
        self._take_profit = take_profit

    def generate_signal(self, state: MarketState) -> Signal | None:
        current = state.current
        if current["open_time"] not in self._trigger_times:
            return None
        return Signal(
            symbol=state.symbol,
            strategy_name=self.name,
            direction=SignalDirection.LONG,
            generated_at=current["open_time"],
            reference_price=float(current["close"]),
            stop_loss=self._stop_loss,
            take_profit=self._take_profit,
            valid_until=current["open_time"] + timedelta(minutes=5),
            reason="test trigger",
            regime_required="none",
            confidence=1.0,
            features_used={},
        )


def _symbol(db_session, name: str) -> tuple[int, SymbolSpecification]:
    spec = SymbolSpecification(
        name=name,
        description="Test symbol",
        digits=5,
        point=_POINT,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=2,
        trade_mode=4,
        visible=True,
    )
    symbol = SymbolRepository(db_session).upsert_from_specification(spec)
    db_session.flush()
    return symbol.id, spec


def _engine(
    db_session,
    client: FakeMT5Client,
    strategy: Strategy,
    *,
    symbol_id: int,
    symbol_spec: SymbolSpecification,
    risk_limits: RiskLimits = _LIMITS,
    clock=None,
) -> DemoExecutionEngine:
    return DemoExecutionEngine(
        db_session,
        client,
        strategy,
        symbol="EURUSD",
        symbol_id=symbol_id,
        timeframe="M1",
        point=_POINT,
        account=_ACCOUNT,
        symbol_spec=symbol_spec,
        risk_limits=risk_limits,
        clock=clock,
    )


def test_approved_signal_sends_order_and_opens_position(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE1")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(5)]
    strategy = _TriggerAtTimesStrategy(
        {candles[-1].open_time}, stop_loss=1.0990, take_profit=1.1050
    )

    client = FakeMT5Client()
    client.order_send_result = make_order_send_result(
        order=1001, deal=2001, position=3001, price=1.1000
    )

    result = _engine(db_session, client, strategy, symbol_id=symbol_id, symbol_spec=spec).step(
        candles
    )

    assert len(result.events) == 1
    assert isinstance(result.events[0], PositionOpened)
    assert result.events[0].mt5_position_ticket == 3001
    assert len(client.order_send_calls) == 1

    active = LiveTradeRepository(db_session).get_active_position(symbol_id, "M1", strategy.name)
    assert active is not None
    assert active.order_state == OrderState.POSITION_OPEN.value
    assert active.mt5_position_ticket == 3001


def test_signal_rejected_by_risk_engine_never_calls_order_send(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE2")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(5)]
    strategy = _TriggerAtTimesStrategy(
        {candles[-1].open_time}, stop_loss=1.0990, take_profit=1.1050
    )

    client = FakeMT5Client()
    limits = RiskLimits(max_trades_per_day=0)

    result = _engine(
        db_session, client, strategy, symbol_id=symbol_id, symbol_spec=spec, risk_limits=limits
    ).step(candles)

    assert len(result.events) == 1
    assert isinstance(result.events[0], SignalRejected)
    assert client.order_send_calls == []

    trades = LiveTradeRepository(db_session).list_recent(symbol_id, "M1", strategy.name)
    assert len(trades) == 1
    assert trades[0].order_state == OrderState.RISK_REJECTED.value
    assert trades[0].rejection_reason == result.events[0].reason


def test_broker_rejection_records_rejected_state(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE3")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(5)]
    strategy = _TriggerAtTimesStrategy(
        {candles[-1].open_time}, stop_loss=1.0990, take_profit=1.1050
    )

    client = FakeMT5Client()
    client.order_send_result = make_order_send_result(retcode=10004, comment="Requote")

    result = _engine(db_session, client, strategy, symbol_id=symbol_id, symbol_spec=spec).step(
        candles
    )

    assert len(result.events) == 1
    assert isinstance(result.events[0], OrderRejectedByBroker)
    assert result.events[0].reason == "Requote"

    trades = LiveTradeRepository(db_session).list_recent(symbol_id, "M1", strategy.name)
    assert trades[0].order_state == OrderState.REJECTED.value
    assert trades[0].rejection_reason == "Requote"
    assert (
        LiveTradeRepository(db_session).get_active_position(symbol_id, "M1", strategy.name) is None
    )


def test_reconciliation_detects_broker_closed_position(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE4")
    strategy = _NeverSignalStrategy()

    trade = LiveTradeRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name=strategy.name,
        model_version="rule-based",
        signal_id="sig-1",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_START,
        mt5_order_ticket=1001,
        mt5_position_ticket=3001,
        entry_time=_START,
    )

    client = FakeMT5Client()
    client.positions_get_result = ()  # broker nao reporta mais a posicao
    client.history_deals_get_result = (
        make_history_deal(position_id=3001, price=1.1050, profit=50.0, entry=1),
    )

    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(3)]
    result = _engine(db_session, client, strategy, symbol_id=symbol_id, symbol_spec=spec).step(
        candles
    )

    assert len(result.events) == 1
    assert isinstance(result.events[0], PositionClosed)
    assert result.events[0].trade_id == trade.id
    assert result.events[0].exit_price == 1.1050
    assert result.events[0].net_pnl == 50.0

    assert trade.order_state == OrderState.CLOSED.value
    assert (
        LiveTradeRepository(db_session).get_active_position(symbol_id, "M1", strategy.name) is None
    )


def test_reconciliation_still_open_produces_no_event_and_blocks_new_signal(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE5")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(3)]
    # Estrategia dispararia sempre, mas a posicao ja ativa deve bloquear.
    strategy = _TriggerAtTimesStrategy(
        {c.open_time for c in candles}, stop_loss=1.0500, take_profit=1.2000
    )

    LiveTradeRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name=strategy.name,
        model_version="rule-based",
        signal_id="sig-2",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_START,
        mt5_order_ticket=1001,
        mt5_position_ticket=3001,
        entry_time=_START,
    )

    client = FakeMT5Client()
    client.positions_get_result = (make_position(ticket=3001),)

    result = _engine(db_session, client, strategy, symbol_id=symbol_id, symbol_spec=spec).step(
        candles
    )

    assert result.events == []
    assert client.order_send_calls == []
    active = LiveTradeRepository(db_session).get_active_position(symbol_id, "M1", strategy.name)
    assert active is not None
    assert active.order_state == OrderState.POSITION_OPEN.value


def test_reconciliation_ambiguous_marks_reconciling(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE6")
    strategy = _NeverSignalStrategy()

    LiveTradeRepository(db_session).create(
        symbol_id=symbol_id,
        timeframe="M1",
        strategy_name=strategy.name,
        model_version="rule-based",
        signal_id="sig-3",
        direction="LONG",
        order_state=OrderState.POSITION_OPEN,
        signal_time=_START,
        mt5_order_ticket=1001,
        mt5_position_ticket=3001,
        entry_time=_START,
    )

    client = FakeMT5Client()
    client.positions_get_result = ()
    client.history_deals_get_result = ()  # nenhum deal encontrado -- ambiguo

    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(3)]
    result = _engine(db_session, client, strategy, symbol_id=symbol_id, symbol_spec=spec).step(
        candles
    )

    assert len(result.events) == 1
    assert isinstance(result.events[0], PositionReconciling)

    active = LiveTradeRepository(db_session).get_active_position(symbol_id, "M1", strategy.name)
    assert active is not None
    assert active.order_state == OrderState.RECONCILING.value


def test_step_with_empty_candles_is_a_no_op(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE7")
    client = FakeMT5Client()
    result = _engine(
        db_session, client, _NeverSignalStrategy(), symbol_id=symbol_id, symbol_spec=spec
    ).step([])
    assert result.processed_bars == 0
    assert result.events == []


def test_first_call_only_considers_latest_bar(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE8")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(5)]
    all_times = {c.open_time for c in candles}
    strategy = _TriggerAtTimesStrategy(all_times, stop_loss=1.0500, take_profit=1.2000)

    client = FakeMT5Client()
    client.order_send_result = make_order_send_result()

    result = _engine(db_session, client, strategy, symbol_id=symbol_id, symbol_spec=spec).step(
        candles
    )

    assert result.processed_bars == 1
    assert len(result.events) == 1
    assert isinstance(result.events[0], PositionOpened)


def test_stale_feed_rejects_signal_and_never_sends_order(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE9")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(5)]
    strategy = _TriggerAtTimesStrategy(
        {candles[-1].open_time}, stop_loss=1.0990, take_profit=1.1050
    )

    # Clock "real" muito a frente da ultima candle -> feed parece atrasado.
    stale_clock = lambda: candles[-1].open_time + timedelta(minutes=10)  # noqa: E731
    limits = RiskLimits(max_feed_delay_seconds=60.0)

    client = FakeMT5Client()
    result = _engine(
        db_session,
        client,
        strategy,
        symbol_id=symbol_id,
        symbol_spec=spec,
        risk_limits=limits,
        clock=stale_clock,
    ).step(candles)

    assert len(result.events) == 1
    assert isinstance(result.events[0], SignalRejected)
    assert "atrasad" in result.events[0].reason
    assert client.order_send_calls == []


def test_fresh_feed_allows_signal_when_clock_is_close_to_last_candle(db_session) -> None:
    symbol_id, spec = _symbol(db_session, "DEMO_ENGINE10")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(5)]
    strategy = _TriggerAtTimesStrategy(
        {candles[-1].open_time}, stop_loss=1.0990, take_profit=1.1050
    )

    fresh_clock = lambda: candles[-1].open_time + timedelta(seconds=5)  # noqa: E731
    limits = RiskLimits(max_feed_delay_seconds=60.0)

    client = FakeMT5Client()
    client.order_send_result = make_order_send_result()
    result = _engine(
        db_session,
        client,
        strategy,
        symbol_id=symbol_id,
        symbol_spec=spec,
        risk_limits=limits,
        clock=fresh_clock,
    ).step(candles)

    assert len(result.events) == 1
    assert isinstance(result.events[0], PositionOpened)
