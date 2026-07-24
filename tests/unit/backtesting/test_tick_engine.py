import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from app.backtesting.fills import TickCostModel
from app.backtesting.tick_engine import TickBacktestConfig, TickBacktestEngine
from app.mt5.market_data import RawCandle, RawTick
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy
from app.strategies.trend.ma_crossover import EmaCrossoverConfig, EmaCrossoverStrategy

_START = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001
_CONTRACT_SIZE = 100_000.0
_BAR_SECONDS = 60


def _candle(minute_offset: int, price: float = 1.1000) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=minute_offset),
        open=price,
        high=price + 0.0005,
        low=price - 0.0005,
        close=price,
        tick_volume=100,
        spread=10,
        real_volume=0,
    )


def _tick(seconds_offset: float, bid: float, ask: float) -> RawTick:
    return RawTick(
        timestamp=_START + timedelta(seconds=seconds_offset),
        bid=bid,
        ask=ask,
        last=0.0,
        volume=0.0,
        flags=6,
    )


class _OneShotStrategy(Strategy):
    """Dispara exatamente um sinal, na barra cujo `open_time` combina com
    `trigger_open_time` — mesmo padrao usado nos testes do motor por
    candle (Fase 5), para isolar o comportamento do motor por tick de
    qualquer logica real de indicador."""

    name = "test_one_shot"

    def __init__(
        self,
        trigger_open_time: datetime,
        *,
        direction: SignalDirection,
        stop_loss: float,
        take_profit: float,
    ) -> None:
        self._trigger_open_time = trigger_open_time
        self._direction = direction
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        self._fired = False

    def generate_signal(self, state: MarketState) -> Signal | None:
        if self._fired or state.current["open_time"] != self._trigger_open_time:
            return None
        self._fired = True
        current = state.current
        return Signal(
            symbol=state.symbol,
            strategy_name=self.name,
            direction=self._direction,
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


_ZERO_COST = TickCostModel(slippage_points=0.0, commission_per_lot=0.0)


def _engine(
    strategy: Strategy, *, cost_model: TickCostModel = _ZERO_COST, **config_overrides: object
) -> TickBacktestEngine:
    config = TickBacktestConfig(volume=1.0, entry_delay_bars=1, cost_model=cost_model, **config_overrides)  # type: ignore[arg-type]
    return TickBacktestEngine(
        strategy,
        config,
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        bar_seconds=_BAR_SECONDS,
        initial_balance=10_000.0,
    )


def _without_signal_ids(trades: list) -> list:
    return [dataclasses.replace(t, signal_id="") for t in trades]


def test_empty_candles_returns_empty_result() -> None:
    engine = _engine(
        _OneShotStrategy(_START, direction=SignalDirection.LONG, stop_loss=1.09, take_profit=1.11)
    )
    result = engine.run([], [], symbol="EURUSD", timeframe="M1")

    assert result.trades == []
    assert result.rejections == []
    assert result.equity_curve.empty


def test_chronological_order_resolves_target_before_stop() -> None:
    """Ao contrario do motor por candle (que assumiria sempre o stop se
    ambos os niveis coubessem numa mesma candle), o motor por tick usa a
    ordem cronologica real — se o alvo foi atingido primeiro, o resultado
    e take_profit, mesmo que o stop tambem seria atingido depois."""
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1002),  # fill de entrada (abertura da barra 1)
        _tick(61, 1.1021, 1.1023),  # alvo atingido primeiro
        _tick(62, 1.0985, 1.0987),  # stop tambem seria atingido, mas depois
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1020
    )
    result = _engine(strategy).run(candles, ticks, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.exit_time == ticks[1].timestamp


def test_chronological_order_resolves_stop_before_target() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1002),
        _tick(61, 1.0985, 1.0987),  # stop atingido primeiro
        _tick(62, 1.1021, 1.1023),  # alvo tambem seria atingido, mas depois
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1020
    )
    result = _engine(strategy).run(candles, ticks, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].exit_time == ticks[1].timestamp


def test_entry_rejected_when_spread_too_wide() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1050),  # spread de 500 pontos
        _tick(61, 1.1000, 1.1002),
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1020
    )
    cost_model = TickCostModel(max_spread_points=10.0)
    result = _engine(strategy, cost_model=cost_model).run(
        candles, ticks, symbol="EURUSD", timeframe="M1"
    )

    assert result.trades == []
    assert len(result.rejections) == 1
    assert "spread" in (result.rejections[0].fill.rejection_reason or "")


def test_liquidity_warning_flagged_on_large_tick_gap() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1002),
        _tick(70, 1.1021, 1.1023),  # gap de 10s > max_tick_gap_seconds (5s default)
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1020
    )
    result = _engine(strategy).run(candles, ticks, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    assert result.trades[0].liquidity_warning is True


def test_time_based_exit() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1002),
        _tick(70, 1.1005, 1.1007),
        _tick(95, 1.1005, 1.1007),  # 35s depois da entrada > max_holding_seconds (30)
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0900, take_profit=1.2000
    )
    result = _engine(strategy, max_holding_seconds=30.0).run(
        candles, ticks, symbol="EURUSD", timeframe="M1"
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "time_exit"
    assert result.trades[0].exit_time == ticks[2].timestamp


def test_trailing_stop_triggers_on_retracement() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1002),  # entrada
        _tick(61, 1.1030, 1.1032),  # sobe: melhor preco 1.1030
        _tick(62, 1.1019, 1.1021),  # recua abaixo do trailing (1.1030-0.0010=1.1020)
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0950, take_profit=1.2000
    )
    result = _engine(strategy, trailing_stop_points=10.0).run(
        candles, ticks, symbol="EURUSD", timeframe="M1"
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "trailing_stop"
    assert trade.exit_time == ticks[2].timestamp
    assert trade.exit_price == pytest.approx(1.1019)


def test_end_of_data_closes_at_last_tick() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1002),
        _tick(61, 1.1005, 1.1007),
        _tick(62, 1.1003, 1.1005),
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0900, take_profit=1.2000
    )
    result = _engine(strategy).run(candles, ticks, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "end_of_data"
    assert trade.exit_time == ticks[-1].timestamp
    assert trade.exit_price == pytest.approx(1.1003)  # bid do ultimo tick


def test_short_trade_uses_correct_sides() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1002),  # entrada SHORT ao bid
        _tick(61, 1.0975, 1.0977),  # alvo (ask <= take_profit)
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.SHORT, stop_loss=1.1020, take_profit=1.0980
    )
    result = _engine(strategy).run(candles, ticks, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == SignalDirection.SHORT
    assert trade.entry_price == pytest.approx(1.1000)  # bid na entrada
    assert trade.exit_reason == "take_profit"
    assert trade.exit_price == pytest.approx(1.0977)  # ask na saida


def test_as_trade_conversion_preserves_core_fields() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]
    ticks = [
        _tick(60, 1.1000, 1.1002),
        _tick(61, 1.1021, 1.1023),
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1020
    )
    result = _engine(strategy).run(candles, ticks, symbol="EURUSD", timeframe="M1")
    tick_trade = result.trades[0]
    base_trade = tick_trade.as_trade()

    assert base_trade.symbol == tick_trade.symbol
    assert base_trade.net_pnl == pytest.approx(tick_trade.net_pnl)
    assert base_trade.exit_reason == tick_trade.exit_reason


def test_engine_is_deterministic_and_reproducible_with_real_strategy() -> None:
    candles = [_candle(i, price=1.1000 + 0.0001 * ((i % 5) - 2)) for i in range(60)]
    ticks = [
        _tick(i * 20, 1.1000 + 0.0001 * ((i % 7) - 3), 1.1002 + 0.0001 * ((i % 7) - 3))
        for i in range(200)
    ]

    def _make_strategy() -> EmaCrossoverStrategy:
        return EmaCrossoverStrategy(
            EmaCrossoverConfig(
                fast_column="ema_9",
                slow_column="ema_21",
                stop_loss_points=100.0,
                take_profit_points=200.0,
            ),
            point=_POINT,
            bar_seconds=_BAR_SECONDS,
        )

    def _run() -> list:
        engine = _engine(_make_strategy())
        return engine.run(candles, ticks, symbol="EURUSD", timeframe="M1").trades

    first = _run()
    second = _run()

    assert _without_signal_ids(first) == _without_signal_ids(second)
