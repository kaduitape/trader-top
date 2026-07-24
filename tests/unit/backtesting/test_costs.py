import pytest

from app.backtesting.costs import (
    CostModel,
    apply_entry_cost,
    apply_exit_cost,
    commission_cost,
    spread_points_for,
)
from app.strategies.base import SignalDirection


def test_spread_points_for_uses_recorded_spread_by_default() -> None:
    model = CostModel()
    assert spread_points_for(model, 20) == 20.0


def test_spread_points_for_fixed_override() -> None:
    model = CostModel(fixed_spread_points=5.0)
    assert spread_points_for(model, 20) == 5.0


def test_spread_points_for_zero_when_disabled_and_no_fixed_value() -> None:
    model = CostModel(use_recorded_spread=False)
    assert spread_points_for(model, 20) == 0.0


def test_apply_entry_cost_long_is_worse_than_reference() -> None:
    model = CostModel(use_recorded_spread=True, slippage_points=1.0)
    entry = apply_entry_cost(
        1.1000, SignalDirection.LONG, model=model, candle_spread_points=20, point=0.00001
    )
    assert entry > 1.1000


def test_apply_entry_cost_short_is_worse_than_reference() -> None:
    model = CostModel(use_recorded_spread=True, slippage_points=1.0)
    entry = apply_entry_cost(
        1.1000, SignalDirection.SHORT, model=model, candle_spread_points=20, point=0.00001
    )
    assert entry < 1.1000


def test_apply_exit_cost_long_is_worse_than_raw_price() -> None:
    model = CostModel(slippage_points=1.0)
    exit_price = apply_exit_cost(
        1.1050, SignalDirection.LONG, model=model, candle_spread_points=20, point=0.00001
    )
    assert exit_price < 1.1050


def test_apply_exit_cost_short_is_worse_than_raw_price() -> None:
    model = CostModel(slippage_points=1.0)
    exit_price = apply_exit_cost(
        1.1050, SignalDirection.SHORT, model=model, candle_spread_points=20, point=0.00001
    )
    assert exit_price > 1.1050


def test_zero_cost_model_does_not_change_price() -> None:
    model = CostModel(use_recorded_spread=False, slippage_points=0.0)
    entry_long = apply_entry_cost(
        1.1000, SignalDirection.LONG, model=model, candle_spread_points=20, point=0.00001
    )
    entry_short = apply_entry_cost(
        1.1000, SignalDirection.SHORT, model=model, candle_spread_points=20, point=0.00001
    )
    assert entry_long == pytest.approx(1.1000)
    assert entry_short == pytest.approx(1.1000)


def test_commission_cost_scales_with_volume() -> None:
    model = CostModel(commission_per_lot=7.0)
    assert commission_cost(model, 0.1) == pytest.approx(0.7)
    assert commission_cost(model, 1.0) == pytest.approx(7.0)
