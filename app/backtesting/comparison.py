"""Relatorio comparativo entre estrategias (criterio de aceite da Fase 6).

Mostra as mesmas metricas lado a lado, na ordem em que as estrategias
foram passadas — nunca ordena por lucro nem elege uma "vencedora". O
prompt mestre e explicito: nunca escolher/classificar uma estrategia como
pronta so pelo lucro liquido ou de forma automatica. Esse julgamento
pertence a fases futuras (ranking com Efficiency Score, Fase 9) e,
principalmente, a um humano.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.backtesting.engine import BacktestResult
from app.backtesting.metrics import compute_metrics


@dataclass(frozen=True, slots=True)
class StrategyComparisonRow:
    strategy_name: str
    num_trades: int
    net_profit: float
    return_pct: float
    win_rate: float
    profit_factor: float | None
    max_drawdown_pct: float
    sharpe_ratio: float | None
    expectancy: float


def build_comparison_row(
    result: BacktestResult, *, initial_balance: float
) -> StrategyComparisonRow:
    metrics = compute_metrics(result.trades, result.equity_curve, initial_balance=initial_balance)
    return StrategyComparisonRow(
        strategy_name=result.strategy_name,
        num_trades=metrics.num_trades,
        net_profit=metrics.net_profit,
        return_pct=metrics.return_pct,
        win_rate=metrics.win_rate,
        profit_factor=metrics.profit_factor,
        max_drawdown_pct=metrics.max_drawdown_pct,
        sharpe_ratio=metrics.sharpe_ratio,
        expectancy=metrics.expectancy,
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "N/D"
    if value == float("inf"):
        return "infinito"
    return f"{value:.3f}"


def format_comparison_table(rows: list[StrategyComparisonRow]) -> str:
    headers = (
        "estrategia",
        "trades",
        "lucro_liquido",
        "retorno_%",
        "taxa_acerto",
        "profit_factor",
        "max_drawdown_%",
        "sharpe",
        "expectativa",
    )
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append(
            "\t".join(
                (
                    row.strategy_name,
                    str(row.num_trades),
                    f"{row.net_profit:.2f}",
                    f"{row.return_pct:.2f}",
                    f"{row.win_rate * 100:.1f}",
                    _fmt(row.profit_factor),
                    f"{row.max_drawdown_pct:.2f}",
                    _fmt(row.sharpe_ratio),
                    f"{row.expectancy:.2f}",
                )
            )
        )
    return "\n".join(lines)
