import pytest

from app.strategies.breakout.range_breakout import RangeBreakoutStrategy
from app.strategies.mean_reversion.zscore_reversion import ZScoreMeanReversionStrategy
from app.strategies.momentum.momentum_continuation import MomentumContinuationStrategy
from app.strategies.registry import STRATEGY_NAMES, create_strategy
from app.strategies.trend.ma_crossover import EmaCrossoverStrategy
from app.strategies.trend.pullback import TrendPullbackStrategy


def test_strategy_names_include_all_fase6_strategies() -> None:
    assert set(STRATEGY_NAMES) == {
        "ema_crossover",
        "trend_pullback",
        "range_breakout",
        "zscore_mean_reversion",
        "momentum_continuation",
    }


@pytest.mark.parametrize(
    ("name", "expected_type", "expected_strategy_name"),
    [
        ("ema_crossover", EmaCrossoverStrategy, "ema_crossover_baseline"),
        ("trend_pullback", TrendPullbackStrategy, "trend_pullback"),
        ("range_breakout", RangeBreakoutStrategy, "range_breakout"),
        ("zscore_mean_reversion", ZScoreMeanReversionStrategy, "zscore_mean_reversion"),
        ("momentum_continuation", MomentumContinuationStrategy, "momentum_continuation"),
    ],
)
def test_create_strategy_returns_correct_type(
    name: str, expected_type: type, expected_strategy_name: str
) -> None:
    strategy = create_strategy(name, point=0.0001, bar_seconds=60)
    assert isinstance(strategy, expected_type)
    assert strategy.name == expected_strategy_name


def test_create_strategy_raises_for_unknown_name() -> None:
    with pytest.raises(ValueError, match="Estrategia desconhecida"):
        create_strategy("does_not_exist", point=0.0001, bar_seconds=60)
