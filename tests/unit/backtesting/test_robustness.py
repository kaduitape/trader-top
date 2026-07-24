from datetime import UTC, datetime, timedelta

import pytest

from app.backtesting.costs import CostModel
from app.backtesting.engine import BacktestConfig
from app.backtesting.fills import TickCostModel
from app.backtesting.robustness import run_cost_stress_test, scale_cost_model
from app.mt5.market_data import RawCandle
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy

_START = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001
_CONTRACT_SIZE = 100_000.0


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


class _FirstBarLongStrategy(Strategy):
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


def test_scale_cost_model_scales_slippage_and_commission_only() -> None:
    base = CostModel(
        commission_per_lot=2.0,
        slippage_points=1.0,
        use_recorded_spread=False,
        fixed_spread_points=5.0,
    )
    scaled = scale_cost_model(base, slippage_multiplier=3.0, commission_multiplier=2.0)

    assert scaled.slippage_points == pytest.approx(3.0)
    assert scaled.commission_per_lot == pytest.approx(4.0)
    # Campos nao relacionados a stress permanecem intactos.
    assert scaled.use_recorded_spread is False
    assert scaled.fixed_spread_points == pytest.approx(5.0)


def test_scale_cost_model_works_generically_for_tick_cost_model() -> None:
    base = TickCostModel(latency_ms=100, slippage_points=2.0, commission_per_lot=1.0)
    scaled = scale_cost_model(base, slippage_multiplier=2.0, commission_multiplier=5.0)

    assert scaled.slippage_points == pytest.approx(4.0)
    assert scaled.commission_per_lot == pytest.approx(5.0)
    assert scaled.latency_ms == 100


def test_stress_test_increases_costs_and_reduces_net_profit() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(2, 1.1000, 1.1030, 1.0995, 1.1010),
    ]
    base_config = BacktestConfig(
        volume=1.0,
        entry_delay_bars=1,
        cost_model=CostModel(
            use_recorded_spread=False, slippage_points=1.0, commission_per_lot=1.0
        ),
    )

    result = run_cost_stress_test(
        lambda: _FirstBarLongStrategy(stop_loss=1.0990, take_profit=1.1020),
        candles,
        base_config=base_config,
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        initial_balance=10_000.0,
        symbol="EURUSD",
        timeframe="M1",
        slippage_multiplier=3.0,
        commission_multiplier=3.0,
    )

    assert result.baseline_metrics.num_trades == 1
    assert result.stressed_metrics.num_trades == 1
    # Custos maiores -> lucro liquido stressado nunca melhor que o baseline.
    assert result.stressed_metrics.net_profit < result.baseline_metrics.net_profit
    assert result.net_profit_degradation_pct is not None
    assert result.net_profit_degradation_pct > 0


def test_stress_test_survives_flag_false_when_expectancy_turns_negative() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        # Ganho minusculo (2 pontos): sob custo 3x, a comissao/slippage
        # avultados devem virar a expectativa negativa.
        _candle(2, 1.1000, 1.1003, 1.0995, 1.1002),
    ]
    base_config = BacktestConfig(
        volume=1.0,
        entry_delay_bars=1,
        cost_model=CostModel(
            use_recorded_spread=False, slippage_points=5.0, commission_per_lot=10.0
        ),
    )

    result = run_cost_stress_test(
        lambda: _FirstBarLongStrategy(stop_loss=1.0990, take_profit=1.1002),
        candles,
        base_config=base_config,
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        initial_balance=10_000.0,
        symbol="EURUSD",
        timeframe="M1",
        slippage_multiplier=5.0,
        commission_multiplier=5.0,
    )

    assert result.survives is False


def test_stress_test_degradation_is_none_when_baseline_not_profitable() -> None:
    candles = [
        _candle(0, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(1, 1.1000, 1.1000, 1.1000, 1.1000),
        _candle(2, 1.1000, 1.1005, 1.0980, 1.1000),  # so o stop e atingido
    ]
    base_config = BacktestConfig(
        volume=1.0, entry_delay_bars=1, cost_model=CostModel(use_recorded_spread=False)
    )

    result = run_cost_stress_test(
        lambda: _FirstBarLongStrategy(stop_loss=1.0990, take_profit=1.1050),
        candles,
        base_config=base_config,
        point=_POINT,
        contract_size=_CONTRACT_SIZE,
        initial_balance=10_000.0,
        symbol="EURUSD",
        timeframe="M1",
    )

    assert result.baseline_metrics.net_profit <= 0
    assert result.net_profit_degradation_pct is None
