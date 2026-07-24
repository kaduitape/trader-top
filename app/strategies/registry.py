"""Registro de estrategias disponiveis.

Usado pela CLI (`backtest run`/`backtest compare`) e por fases futuras
(walk-forward, dashboard) para instanciar uma estrategia pelo nome, sem
acoplar o chamador aos detalhes do `Config` de cada uma. Cada estrategia
continua configuravel via seu proprio dataclass de configuracao — o
registro so oferece a versao com parametros padrao.
"""

from __future__ import annotations

from collections.abc import Callable

from app.strategies.base import Strategy
from app.strategies.breakout.range_breakout import RangeBreakoutConfig, RangeBreakoutStrategy
from app.strategies.mean_reversion.zscore_reversion import (
    ZScoreMeanReversionConfig,
    ZScoreMeanReversionStrategy,
)
from app.strategies.momentum.momentum_continuation import (
    MomentumContinuationConfig,
    MomentumContinuationStrategy,
)
from app.strategies.trend.ma_crossover import EmaCrossoverConfig, EmaCrossoverStrategy
from app.strategies.trend.pullback import TrendPullbackConfig, TrendPullbackStrategy

StrategyFactory = Callable[[float, int], Strategy]

_FACTORIES: dict[str, StrategyFactory] = {
    "ema_crossover": lambda point, bar_seconds: EmaCrossoverStrategy(
        EmaCrossoverConfig(), point=point, bar_seconds=bar_seconds
    ),
    "trend_pullback": lambda point, bar_seconds: TrendPullbackStrategy(
        TrendPullbackConfig(), point=point, bar_seconds=bar_seconds
    ),
    "range_breakout": lambda point, bar_seconds: RangeBreakoutStrategy(
        RangeBreakoutConfig(), point=point, bar_seconds=bar_seconds
    ),
    "zscore_mean_reversion": lambda point, bar_seconds: ZScoreMeanReversionStrategy(
        ZScoreMeanReversionConfig(), point=point, bar_seconds=bar_seconds
    ),
    "momentum_continuation": lambda point, bar_seconds: MomentumContinuationStrategy(
        MomentumContinuationConfig(), point=point, bar_seconds=bar_seconds
    ),
}

STRATEGY_NAMES: tuple[str, ...] = tuple(_FACTORIES)


def create_strategy(name: str, *, point: float, bar_seconds: int) -> Strategy:
    try:
        factory = _FACTORIES[name]
    except KeyError:
        raise ValueError(
            f"Estrategia desconhecida: '{name}'. Disponiveis: {STRATEGY_NAMES}"
        ) from None
    return factory(point, bar_seconds)
