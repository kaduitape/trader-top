import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from app.backtesting.costs import CostModel
from app.backtesting.engine import BacktestConfig, CandleBacktestEngine
from app.mt5.market_data import RawCandle
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy
from app.strategies.trend.ma_crossover import EmaCrossoverConfig, EmaCrossoverStrategy

_START = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001
_CONTRACT_SIZE = 100_000.0


def _candle(
    minute_offset: int, o: float, h: float, low: float, c: float, spread: int = 10
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


class _OneShotStrategy(Strategy):
    """Dispara exatamente um sinal, na barra cujo `open_time` combina com
    `trigger_open_time` — controle total sobre quando o trade acontece,
    sem depender de nenhuma logica de indicador real."""

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


_ZERO_COST = CostModel(use_recorded_spread=False, slippage_points=0.0)


def _without_signal_ids(trades: list) -> list:
    """`Signal.signal_id` (e portanto `Trade.signal_id`) e um UUID novo a
    cada geracao de sinal — correto para rastreabilidade real, mas precisa
    ser ignorado ao comparar trades por igualdade estrutural nos testes de
    determinismo/vazamento."""
    return [dataclasses.replace(trade, signal_id="") for trade in trades]


def _engine(
    strategy: Strategy, *, cost_model: CostModel = _ZERO_COST, volume: float = 1.0
) -> CandleBacktestEngine:
    config = BacktestConfig(volume=volume, entry_delay_bars=1, cost_model=cost_model)
    return CandleBacktestEngine(
        strategy, config, point=_POINT, contract_size=_CONTRACT_SIZE, initial_balance=10_000.0
    )


def test_empty_candle_list_returns_empty_result() -> None:
    engine = _engine(_NeverSignalStrategy())
    result = engine.run([], symbol="EURUSD", timeframe="M1")

    assert result.trades == []
    assert result.equity_curve.empty


def test_single_candle_does_not_crash_and_produces_no_trades() -> None:
    strategy = _OneShotStrategy(
        _START, direction=SignalDirection.LONG, stop_loss=1.0900, take_profit=1.1100
    )
    engine = _engine(strategy)
    candles = [_candle(0, 1.1000, 1.1005, 1.0995, 1.1000)]

    result = engine.run(candles, symbol="EURUSD", timeframe="M1")

    assert result.trades == []


def test_never_signal_strategy_produces_no_trades() -> None:
    candles = [_candle(i, 1.1000, 1.1010, 1.0990, 1.1000) for i in range(10)]
    engine = _engine(_NeverSignalStrategy())

    result = engine.run(candles, symbol="EURUSD", timeframe="M1")

    assert result.trades == []
    assert (result.equity_curve == 10_000.0).all()


def test_long_trade_stop_loss_only() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),  # sinal gerado aqui
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),  # execucao (abertura=entrada)
        _candle(2, 1.1000, 1.1005, 1.0980, 1.1000),  # so o stop e atingido
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1050
    )
    result = _engine(strategy).run(candles, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(1.0990)
    assert trade.entry_price == pytest.approx(1.1000)
    assert trade.entry_time == candles[1].open_time
    assert trade.exit_time == candles[2].open_time
    assert trade.bars_held == 1


def test_long_trade_take_profit_only() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(2, 1.1000, 1.1030, 1.0995, 1.1000),  # so o alvo e atingido
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1020
    )
    result = _engine(strategy).run(candles, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.exit_price == pytest.approx(1.1020)


def test_long_trade_conservative_when_both_stop_and_target_hit_same_candle() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(2, 1.1000, 1.1030, 1.0980, 1.1000),  # AMBOS dentro do range
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1020
    )
    result = _engine(strategy).run(candles, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    trade = result.trades[0]
    # Nunca escolhe o resultado favoravel (take_profit) quando ambos sao
    # atingidos na mesma candle — sempre assume o pior caso (stop_loss).
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(1.0990)


def test_short_trade_conservative_when_both_stop_and_target_hit_same_candle() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(2, 1.1000, 1.1020, 1.0970, 1.1000),  # AMBOS dentro do range
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.SHORT, stop_loss=1.1010, take_profit=1.0980
    )
    result = _engine(strategy).run(candles, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(1.1010)


def test_position_closes_at_end_of_data_when_neither_level_hit() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(2, 1.1000, 1.1005, 1.0995, 1.1003),
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0900, take_profit=1.1100
    )
    result = _engine(strategy).run(candles, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "end_of_data"
    assert trade.exit_price == pytest.approx(1.1003)
    assert trade.exit_time == candles[2].open_time


def test_entry_executes_at_next_bar_open_not_signal_bar_close() -> None:
    candles = [
        _candle(0, 1.1000, 1.1010, 1.0990, 1.1005),  # sinal gerado com close=1.1005
        _candle(1, 1.2000, 1.2010, 1.1990, 1.2005),  # execucao: abertura = 1.2000
        _candle(2, 1.2000, 1.2005, 1.0000, 1.2000),  # forca stop (nao importa o valor)
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0500, take_profit=1.3000
    )
    result = _engine(strategy).run(candles, symbol="EURUSD", timeframe="M1")

    assert len(result.trades) == 1
    # O preco de entrada deve vir da ABERTURA da barra de execucao (1.2000),
    # nunca do preco de referencia do sinal (fechamento da barra 0, 1.1005).
    assert result.trades[0].entry_price == pytest.approx(1.2000)


def test_costs_reduce_net_pnl_relative_to_gross() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(2, 1.1000, 1.1030, 1.0995, 1.1000),
    ]
    strategy = _OneShotStrategy(
        candles[0].open_time, direction=SignalDirection.LONG, stop_loss=1.0990, take_profit=1.1020
    )
    cost_model = CostModel(use_recorded_spread=False, slippage_points=0.0, commission_per_lot=5.0)
    result = _engine(strategy, cost_model=cost_model, volume=1.0).run(
        candles, symbol="EURUSD", timeframe="M1"
    )

    trade = result.trades[0]
    assert trade.commission == pytest.approx(5.0)
    assert trade.net_pnl == pytest.approx(trade.gross_pnl - 5.0)


def test_leakage_mutating_a_candle_after_trade_closed_does_not_change_trades() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(2, 1.1000, 1.1005, 1.0980, 1.1000),  # trade fecha aqui (stop)
        _candle(3, 1.1000, 1.1005, 1.0995, 1.1000),  # barra tranquila, sem posicao
        _candle(4, 1.1000, 1.1005, 1.0995, 1.1000),  # ultima barra (sera mutada)
    ]

    def _run() -> list:
        strategy = _OneShotStrategy(
            candles[0].open_time,
            direction=SignalDirection.LONG,
            stop_loss=1.0990,
            take_profit=1.1050,
        )
        return _engine(strategy).run(candles, symbol="EURUSD", timeframe="M1").trades

    baseline = _run()

    mutated_candles = list(candles)
    mutated_candles[-1] = _candle(4, 9999.0, 9999.0, 9999.0, 9999.0)

    def _run_mutated() -> list:
        strategy = _OneShotStrategy(
            mutated_candles[0].open_time,
            direction=SignalDirection.LONG,
            stop_loss=1.0990,
            take_profit=1.1050,
        )
        return (
            CandleBacktestEngine(
                strategy,
                BacktestConfig(volume=1.0, entry_delay_bars=1, cost_model=_ZERO_COST),
                point=_POINT,
                contract_size=_CONTRACT_SIZE,
                initial_balance=10_000.0,
            )
            .run(mutated_candles, symbol="EURUSD", timeframe="M1")
            .trades
        )

    mutated = _run_mutated()

    assert _without_signal_ids(baseline) == _without_signal_ids(mutated)
    assert len(baseline) == 1


def test_engine_is_deterministic_and_reproducible_with_real_strategy() -> None:
    rng_candles = [
        _candle(
            i,
            1.1000 + 0.0001 * (1 if i % 3 == 0 else -1),
            1.1010 + 0.0001 * i,
            1.0990 - 0.0001 * i,
            1.1000 + 0.0001 * ((i % 5) - 2),
        )
        for i in range(60)
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
            bar_seconds=60,
        )

    def _run() -> list:
        engine = CandleBacktestEngine(
            _make_strategy(),
            BacktestConfig(volume=0.1, entry_delay_bars=1, cost_model=CostModel()),
            point=_POINT,
            contract_size=_CONTRACT_SIZE,
            initial_balance=10_000.0,
        )
        return engine.run(rng_candles, symbol="EURUSD", timeframe="M1").trades

    first = _run()
    second = _run()

    assert _without_signal_ids(first) == _without_signal_ids(second)
