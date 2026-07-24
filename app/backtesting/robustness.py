"""Teste de robustez por stress de custos (Fase 9).

O prompt mestre (seções 13/31) exige testar robustez com custos
aumentados — a pergunta é: quanto do resultado depende de premissas de
custo otimistas? Este módulo reexecuta o mesmo backtest com slippage e
comissão multiplicados (o spread gravado na candle, vindo do próprio
corretor, não é alterado — não há uma base honesta para "inventar" um
spread maior sem dados reais de um regime de mercado mais adverso).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence

from app.backtesting.costs import CostModel
from app.backtesting.engine import BacktestConfig, CandleBacktestEngine
from app.backtesting.metrics import BacktestMetrics, compute_metrics
from app.market.features import CandleFeatureLike
from app.strategies.base import Strategy


def scale_cost_model(
    model: CostModel, *, slippage_multiplier: float = 1.0, commission_multiplier: float = 1.0
) -> CostModel:
    return dataclasses.replace(
        model,
        slippage_points=model.slippage_points * slippage_multiplier,
        commission_per_lot=model.commission_per_lot * commission_multiplier,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class CostStressResult:
    baseline_metrics: BacktestMetrics
    stressed_metrics: BacktestMetrics
    slippage_multiplier: float
    commission_multiplier: float
    net_profit_degradation_pct: float | None
    """`None` quando o lucro líquido base já não era positivo (percentual de
    degradação não faz sentido nesse caso)."""
    survives: bool
    """`True` se a expectativa por trade continua positiva sob o stress."""


def run_cost_stress_test(
    strategy_factory: Callable[[], Strategy],
    candles: Sequence[CandleFeatureLike],
    *,
    base_config: BacktestConfig,
    point: float,
    contract_size: float,
    initial_balance: float,
    symbol: str,
    timeframe: str,
    slippage_multiplier: float = 3.0,
    commission_multiplier: float = 3.0,
) -> CostStressResult:
    baseline_engine = CandleBacktestEngine(
        strategy_factory(),
        base_config,
        point=point,
        contract_size=contract_size,
        initial_balance=initial_balance,
    )
    baseline_result = baseline_engine.run(candles, symbol=symbol, timeframe=timeframe)
    baseline_metrics = compute_metrics(
        baseline_result.trades, baseline_result.equity_curve, initial_balance=initial_balance
    )

    stressed_config = dataclasses.replace(
        base_config,
        cost_model=scale_cost_model(
            base_config.cost_model,
            slippage_multiplier=slippage_multiplier,
            commission_multiplier=commission_multiplier,
        ),
    )
    stressed_engine = CandleBacktestEngine(
        strategy_factory(),
        stressed_config,
        point=point,
        contract_size=contract_size,
        initial_balance=initial_balance,
    )
    stressed_result = stressed_engine.run(candles, symbol=symbol, timeframe=timeframe)
    stressed_metrics = compute_metrics(
        stressed_result.trades, stressed_result.equity_curve, initial_balance=initial_balance
    )

    degradation: float | None = None
    if baseline_metrics.net_profit > 0:
        degradation = (
            (baseline_metrics.net_profit - stressed_metrics.net_profit)
            / abs(baseline_metrics.net_profit)
            * 100
        )

    return CostStressResult(
        baseline_metrics=baseline_metrics,
        stressed_metrics=stressed_metrics,
        slippage_multiplier=slippage_multiplier,
        commission_multiplier=commission_multiplier,
        net_profit_degradation_pct=degradation,
        survives=stressed_metrics.expectancy > 0,
    )
