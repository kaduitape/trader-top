from datetime import UTC, datetime, timedelta

from app.backtesting.costs import CostModel
from app.database.repositories.paper_trade_repository import PaperTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.mt5.market_data import RawCandle
from app.mt5.symbol_mapper import SymbolSpecification
from app.paper_trading.engine import PaperTradeClosed, PaperTradeOpened, PaperTradingEngine
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy

_START = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001
_CONTRACT_SIZE = 100_000.0
_ZERO_COST = CostModel(use_recorded_spread=False, slippage_points=0.0)


def _candle(minute_offset: int, o: float, h: float, low: float, c: float) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=minute_offset),
        open=o,
        high=h,
        low=low,
        close=c,
        tick_volume=100,
        spread=10,
        real_volume=0,
    )


class _NeverSignalStrategy(Strategy):
    name = "test_never"

    def generate_signal(self, state: MarketState) -> Signal | None:
        return None


class _TriggerAtTimesStrategy(Strategy):
    """Dispara um sinal LONG toda vez que a barra atual bater com um dos
    horarios em `trigger_times` — controle total sobre quando o motor de
    paper trading deve abrir uma posicao, sem depender de indicador real."""

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


def _symbol_id(db_session, name: str) -> int:
    spec = SymbolSpecification(
        name=name,
        description="Test symbol",
        digits=5,
        point=_POINT,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=_CONTRACT_SIZE,
        spread=2,
        trade_mode=4,
        visible=True,
    )
    symbol = SymbolRepository(db_session).upsert_from_specification(spec)
    db_session.flush()
    return symbol.id


def _engine(
    db_session, strategy: Strategy, *, symbol_id: int, volume: float = 1.0
) -> PaperTradingEngine:
    return PaperTradingEngine(
        db_session,
        strategy,
        symbol="EURUSD",
        symbol_id=symbol_id,
        timeframe="M1",
        bar_seconds=60,
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        volume=volume,
        cost_model=_ZERO_COST,
    )


def test_first_call_only_considers_the_latest_bar_as_new(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_ENGINE1")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(5)]
    # Estrategia dispararia em TODAS as barras se fossem avaliadas -- mas
    # na primeira chamada so a ultima barra conta como "nova".
    all_times = {c.open_time for c in candles}
    strategy = _TriggerAtTimesStrategy(all_times, stop_loss=1.0500, take_profit=1.2000)

    result = _engine(db_session, strategy, symbol_id=symbol_id).step(candles)

    assert result.processed_bars == 1
    assert len(result.events) == 1
    assert isinstance(result.events[0], PaperTradeOpened)
    assert result.events[0].entry_time == candles[-1].open_time

    open_trade = PaperTradeRepository(db_session).get_open(symbol_id, "M1", strategy.name)
    assert open_trade is not None


def test_second_call_with_no_new_bars_is_a_no_op(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_ENGINE2")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(3)]
    strategy = _NeverSignalStrategy()
    engine = _engine(db_session, strategy, symbol_id=symbol_id)

    first = engine.step(candles)
    second = engine.step(candles)

    assert first.processed_bars == 1
    assert second.processed_bars == 0
    assert second.events == []


def test_open_position_closes_on_stop_hit_in_a_later_call(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_ENGINE3")
    opening_candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(3)]
    strategy = _TriggerAtTimesStrategy(
        {opening_candles[-1].open_time}, stop_loss=1.0990, take_profit=1.1050
    )
    engine = _engine(db_session, strategy, symbol_id=symbol_id)

    opened = engine.step(opening_candles)
    assert len(opened.events) == 1
    assert isinstance(opened.events[0], PaperTradeOpened)

    # Nova barra chega num poll seguinte, atingindo o stop.
    new_bar = _candle(3, 1.1000, 1.1005, 1.0980, 1.1000)
    closing_candles = [*opening_candles, new_bar]
    closed = engine.step(closing_candles)

    assert closed.processed_bars == 1
    assert len(closed.events) == 1
    assert isinstance(closed.events[0], PaperTradeClosed)
    assert closed.events[0].exit_reason == "stop_loss"
    assert closed.events[0].exit_price == 1.0990

    assert PaperTradeRepository(db_session).get_open(symbol_id, "M1", strategy.name) is None
    recent = PaperTradeRepository(db_session).list_recent(symbol_id, "M1", strategy.name)
    assert recent[0].status == "CLOSED"
    assert recent[0].bars_held == 1


def test_conservative_rule_stop_wins_when_both_hit_same_bar(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_ENGINE4")
    opening_candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(3)]
    strategy = _TriggerAtTimesStrategy(
        {opening_candles[-1].open_time}, stop_loss=1.0990, take_profit=1.1020
    )
    engine = _engine(db_session, strategy, symbol_id=symbol_id)
    engine.step(opening_candles)

    # Ambas as barreiras cabem nesta barra.
    both_hit_bar = _candle(3, 1.1000, 1.1030, 1.0980, 1.1000)
    result = engine.step([*opening_candles, both_hit_bar])

    assert len(result.events) == 1
    assert isinstance(result.events[0], PaperTradeClosed)
    assert result.events[0].exit_reason == "stop_loss"


def test_new_signal_can_open_after_previous_position_closes_in_same_call(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_ENGINE5")
    opening_candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(3)]
    stop_bar = _candle(3, 1.1000, 1.1005, 1.0980, 1.1000)  # fecha a primeira posicao
    reopen_bar = _candle(4, 1.1000, 1.1005, 1.0995, 1.1000)  # dispara uma nova

    strategy = _TriggerAtTimesStrategy(
        {opening_candles[-1].open_time, reopen_bar.open_time},
        stop_loss=1.0990,
        take_profit=1.1050,
    )
    engine = _engine(db_session, strategy, symbol_id=symbol_id)
    engine.step(opening_candles)

    result = engine.step([*opening_candles, stop_bar, reopen_bar])

    assert len(result.events) == 2
    assert isinstance(result.events[0], PaperTradeClosed)
    assert isinstance(result.events[1], PaperTradeOpened)
    assert result.events[1].entry_time == reopen_bar.open_time


def test_never_signal_strategy_advances_cursor_without_events(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_ENGINE6")
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(10)]
    strategy = _NeverSignalStrategy()
    engine = _engine(db_session, strategy, symbol_id=symbol_id)

    result = engine.step(candles)
    assert result.processed_bars == 1
    assert result.events == []

    more_candles = [*candles, _candle(10, 1.1000, 1.1005, 1.0995, 1.1000)]
    result2 = engine.step(more_candles)
    assert result2.processed_bars == 1
    assert result2.events == []


def test_step_with_empty_candles_is_a_no_op(db_session) -> None:
    symbol_id = _symbol_id(db_session, "PAPER_ENGINE7")
    engine = _engine(db_session, _NeverSignalStrategy(), symbol_id=symbol_id)
    result = engine.step([])
    assert result.processed_bars == 0
    assert result.events == []
