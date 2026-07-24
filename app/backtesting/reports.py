"""Relatorio de backtest: junta metricas + trades num unico objeto,
serializavel para JSON, e uma versao texto legivel para a CLI.

Nunca oculta as metricas individuais atras de um resumo — o relatorio
sempre expõe `BacktestMetrics` por completo (ver `app.backtesting.metrics`).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.backtesting.engine import BacktestResult, Trade
from app.backtesting.metrics import BacktestMetrics, compute_metrics


@dataclass(frozen=True, slots=True)
class BacktestReport:
    symbol: str
    timeframe: str
    strategy_name: str
    period_start: datetime | None
    period_end: datetime | None
    initial_balance: float
    final_balance: float
    metrics: BacktestMetrics
    trades: list[Trade]


def build_report(result: BacktestResult) -> BacktestReport:
    metrics = compute_metrics(
        result.trades, result.equity_curve, initial_balance=result.initial_balance
    )
    period_start = result.trades[0].entry_time if result.trades else None
    period_end = result.trades[-1].exit_time if result.trades else None
    final_balance = result.initial_balance + metrics.net_profit

    return BacktestReport(
        symbol=result.symbol,
        timeframe=result.timeframe,
        strategy_name=result.strategy_name,
        period_start=period_start,
        period_end=period_end,
        initial_balance=result.initial_balance,
        final_balance=final_balance,
        metrics=metrics,
        trades=result.trades,
    )


def report_to_dict(report: BacktestReport) -> dict[str, Any]:
    return {
        "symbol": report.symbol,
        "timeframe": report.timeframe,
        "strategy_name": report.strategy_name,
        "period_start": report.period_start,
        "period_end": report.period_end,
        "initial_balance": report.initial_balance,
        "final_balance": report.final_balance,
        "metrics": dataclasses.asdict(report.metrics),
        "trades": [dataclasses.asdict(trade) for trade in report.trades],
    }


def format_report_text(report: BacktestReport) -> str:
    m = report.metrics
    lines = [
        f"Backtest: {report.strategy_name} | {report.symbol} {report.timeframe}",
        f"Periodo: {report.period_start} -> {report.period_end}",
        f"Saldo inicial: {report.initial_balance:.2f} | Saldo final: {report.final_balance:.2f}",
        "",
        f"Numero de trades: {m.num_trades} | Trades/dia: {m.trades_per_day:.3f}",
        f"Lucro liquido: {m.net_profit:.2f} ({m.return_pct:.2f}%)",
        f"Retorno anualizado: {_fmt(m.annualized_return_pct, '%')}",
        f"Drawdown maximo: {m.max_drawdown:.2f} ({m.max_drawdown_pct:.2f}%), "
        f"duracao: {m.max_drawdown_duration_bars} barra(s)",
        f"Profit factor: {_fmt(m.profit_factor)} | Payoff: {_fmt(m.payoff_ratio)}",
        f"Expectativa por trade: {m.expectancy:.2f} | Taxa de acerto: {m.win_rate * 100:.1f}%",
        f"Media de ganho: {m.avg_win:.2f} | Media de perda: {m.avg_loss:.2f}",
        f"Sharpe: {_fmt(m.sharpe_ratio)} | Sortino: {_fmt(m.sortino_ratio)} | "
        f"Calmar: {_fmt(m.calmar_ratio)}",
        f"Sequencia maxima de perdas: {m.max_consecutive_losses}",
        f"Risco de ruina estimado: {_fmt(m.estimated_risk_of_ruin)} (aproximado, nao e Monte Carlo)",
        f"MAE medio: {m.avg_mae:.5f} | MFE medio: {m.avg_mfe:.5f}",
        f"Custo total: {m.total_cost:.2f} | Custo medio/trade: {m.avg_cost_per_trade:.2f}",
        f"Duracao media do trade: {m.avg_trade_duration_bars:.1f} barra(s)",
        "",
        f"Resultado por direcao: {m.result_by_direction}",
        f"Resultado por tendencia (regime na entrada): {m.result_by_regime_trend}",
        f"Resultado por hora (UTC): {m.result_by_hour}",
        f"Resultado por dia da semana (0=segunda): {m.result_by_day_of_week}",
    ]
    return "\n".join(lines)


def _fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "N/D"
    if value == float("inf"):
        return "infinito"
    return f"{value:.3f}{suffix}"
