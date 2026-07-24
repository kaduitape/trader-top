from datetime import UTC, datetime, timedelta

import pytest

from app.backtesting.costs import CostModel
from app.backtesting.engine import BacktestConfig
from app.backtesting.walk_forward import run_walk_forward, split_sequential_windows
from app.mt5.market_data import RawCandle
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


class _FirstBarLongStrategy(Strategy):
    """Dispara exatamente um sinal LONG na primeira barra que vir (índice 0
    de QUALQUER janela, já que uma instância nova é criada por janela via
    `strategy_factory`) — dá controle total sobre o resultado de cada
    janela sem depender de indicador real."""

    name = "test_first_bar_long"

    def __init__(self, *, stop_loss: float, take_profit: float) -> None:
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        self._fired = False

    def generate_signal(self, state: MarketState) -> Signal | None:
        if self._fired:
            return None
        self._fired = True
        current = state.current
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


def _winning_block(start_minute: int) -> list[RawCandle]:
    """Bloco de 3 candles: sinal (0), execução (1, abre em 1.1000), alvo
    atingido (2, take_profit=1.1020) — sempre o MESMO resultado (+0.0020
    de preço), independente de onde o bloco cai na série."""
    return [
        _candle(start_minute, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(start_minute + 1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(start_minute + 2, 1.1000, 1.1030, 1.0995, 1.1010),
    ]


def _config(volume: float = 1.0) -> BacktestConfig:
    return BacktestConfig(volume=volume, entry_delay_bars=1, cost_model=_ZERO_COST)


def test_split_sequential_windows_divides_evenly() -> None:
    windows = split_sequential_windows(100, n_windows=4)
    assert windows == [(0, 25), (25, 50), (50, 75), (75, 100)]


def test_split_sequential_windows_last_window_absorbs_remainder() -> None:
    windows = split_sequential_windows(101, n_windows=4)
    assert windows == [(0, 25), (25, 50), (50, 75), (75, 101)]


def test_split_sequential_windows_rejects_invalid_n_windows() -> None:
    with pytest.raises(ValueError):
        split_sequential_windows(10, n_windows=0)


def test_split_sequential_windows_rejects_insufficient_bars() -> None:
    with pytest.raises(ValueError):
        split_sequential_windows(3, n_windows=5)


def test_walk_forward_never_signal_strategy_has_no_eligible_windows() -> None:
    candles = [_candle(i, 1.1000, 1.1010, 1.0990, 1.1000) for i in range(30)]
    report = run_walk_forward(
        _NeverSignalStrategy,
        candles,
        n_windows=3,
        config=_config(),
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        initial_balance=10_000.0,
        symbol="EURUSD",
        timeframe="M1",
        min_trades_per_window=1,
    )
    assert len(report.windows) == 3
    assert all(w.metrics.num_trades == 0 for w in report.windows)
    assert report.is_stable is False
    assert report.stability_notes


def test_walk_forward_uniformly_profitable_strategy_is_stable() -> None:
    n_windows = 5
    candles: list[RawCandle] = []
    for w in range(n_windows):
        candles.extend(_winning_block(w * 3))

    report = run_walk_forward(
        lambda: _FirstBarLongStrategy(stop_loss=1.0990, take_profit=1.1020),
        candles,
        n_windows=n_windows,
        config=_config(volume=1.0),
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        initial_balance=10_000.0,
        symbol="EURUSD",
        timeframe="M1",
        min_trades_per_window=1,
    )

    assert len(report.windows) == n_windows
    for window in report.windows:
        assert window.metrics.num_trades == 1
        assert window.metrics.net_profit == pytest.approx(200.0)

    assert report.profitable_window_ratio == 1.0
    assert report.max_single_window_profit_share == pytest.approx(1.0 / n_windows)
    assert report.is_stable is True
    assert report.aggregate_metrics.num_trades == n_windows
    assert report.aggregate_metrics.net_profit == pytest.approx(200.0 * n_windows)


def test_walk_forward_flags_single_exceptional_window_as_unstable() -> None:
    n_windows = 4
    candles: list[RawCandle] = []
    for w in range(n_windows):
        if w == 0:
            # Janela excepcional: alvo MUITO maior que as demais.
            candles.extend(
                [
                    _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
                    _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
                    _candle(2, 1.1000, 1.5000, 1.0995, 1.4000),
                ]
            )
        else:
            candles.extend(_winning_block(w * 3))

    report = run_walk_forward(
        lambda: _FirstBarLongStrategy(stop_loss=1.0990, take_profit=1.4900),
        candles,
        n_windows=n_windows,
        config=_config(volume=1.0),
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        initial_balance=10_000.0,
        symbol="EURUSD",
        timeframe="M1",
        min_trades_per_window=1,
    )

    # A estrategia usa o MESMO par stop/target em todas as janelas, entao
    # nas janelas 1..3 (bloco vencedor padrao, alvo 1.1020) o alvo de
    # 1.4900 nunca e atingido -> end_of_data, resultado pequeno/neutro.
    assert report.max_single_window_profit_share is not None
    assert report.max_single_window_profit_share > 0.8
    assert report.is_stable is False
    assert any("período excepcional" in note for note in report.stability_notes)


def test_walk_forward_excludes_windows_below_min_trades_threshold() -> None:
    n_windows = 3
    candles: list[RawCandle] = []
    for w in range(n_windows):
        candles.extend(_winning_block(w * 3))

    report = run_walk_forward(
        lambda: _FirstBarLongStrategy(stop_loss=1.0990, take_profit=1.1020),
        candles,
        n_windows=n_windows,
        config=_config(),
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        initial_balance=10_000.0,
        symbol="EURUSD",
        timeframe="M1",
        min_trades_per_window=2,  # cada janela so produz 1 trade -> nenhuma elegivel
    )
    assert report.is_stable is False
    assert any("trade(s)" in note for note in report.stability_notes)
