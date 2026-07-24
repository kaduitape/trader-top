"""Metricas obrigatorias de backtest (prompt mestre, secao 13).

Nenhuma metrica e escondida atras de um score unico — todas ficam
expostas em `BacktestMetrics`, inclusive as agregacoes por hora/dia da
semana/regime/direcao, para que uma taxa de acerto alta isolada nunca seja
confundida com uma estrategia boa (o proprio prompt mestre alerta para
isso explicitamente).

Todas as funcoes lidam com `trades` vazio sem levantar excecao — retornam
valores neutros (0/None), nunca inventam um numero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.backtesting.engine import Trade

_TRADING_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    net_profit: float
    return_pct: float
    annualized_return_pct: float | None
    max_drawdown: float
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    profit_factor: float | None
    payoff_ratio: float | None
    expectancy: float
    win_rate: float
    avg_win: float
    avg_loss: float
    num_trades: int
    trades_per_day: float
    avg_trade_duration_bars: float
    avg_mae: float
    avg_mfe: float
    avg_cost_per_trade: float
    total_cost: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    max_consecutive_losses: int
    estimated_risk_of_ruin: float | None
    result_by_hour: dict[int, float]
    result_by_day_of_week: dict[int, float]
    result_by_regime_trend: dict[str, float]
    result_by_direction: dict[str, float]


def _empty_metrics() -> BacktestMetrics:
    return BacktestMetrics(
        net_profit=0.0,
        return_pct=0.0,
        annualized_return_pct=None,
        max_drawdown=0.0,
        max_drawdown_pct=0.0,
        max_drawdown_duration_bars=0,
        profit_factor=None,
        payoff_ratio=None,
        expectancy=0.0,
        win_rate=0.0,
        avg_win=0.0,
        avg_loss=0.0,
        num_trades=0,
        trades_per_day=0.0,
        avg_trade_duration_bars=0.0,
        avg_mae=0.0,
        avg_mfe=0.0,
        avg_cost_per_trade=0.0,
        total_cost=0.0,
        sharpe_ratio=None,
        sortino_ratio=None,
        calmar_ratio=None,
        max_consecutive_losses=0,
        estimated_risk_of_ruin=None,
        result_by_hour={},
        result_by_day_of_week={},
        result_by_regime_trend={},
        result_by_direction={},
    )


def _max_drawdown(equity_curve: pd.Series) -> tuple[float, float, int]:
    if equity_curve.empty:
        return 0.0, 0.0, 0
    values = equity_curve.to_numpy(dtype=float)
    running_max = np.maximum.accumulate(values)
    drawdown = values - running_max
    trough_pos = int(np.argmin(drawdown))
    max_dd = float(-drawdown[trough_pos])
    peak_value = running_max[trough_pos]
    max_dd_pct = float(max_dd / peak_value * 100) if peak_value != 0 else 0.0
    peak_candidates = np.where(values[: trough_pos + 1] == peak_value)[0]
    peak_pos = int(peak_candidates[-1]) if len(peak_candidates) else 0
    duration = trough_pos - peak_pos
    return max_dd, max_dd_pct, duration


def _max_consecutive_losses(trades: list[Trade]) -> int:
    worst = 0
    current = 0
    for trade in trades:
        if trade.net_pnl < 0:
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def _estimate_risk_of_ruin(
    win_rate: float, payoff_ratio: float | None, avg_loss: float, initial_balance: float
) -> float | None:
    """Estimativa aproximada (formula classica de risco de ruina para
    apostas com payoff assimetrico). NAO substitui uma simulacao de Monte
    Carlo (Fase 9) — e apenas uma referencia rapida."""
    if payoff_ratio is None or payoff_ratio <= 0 or avg_loss <= 0 or initial_balance <= 0:
        return None

    edge = win_rate - (1 - win_rate) / payoff_ratio
    if edge <= 0:
        return 1.0

    units = initial_balance / avg_loss
    if units <= 0:
        return 1.0

    ratio = (1 - edge) / (1 + edge)
    return float(min(1.0, max(0.0, ratio**units)))


def _trade_returns(trades: list[Trade], initial_balance: float) -> np.ndarray:
    balance = initial_balance
    returns = []
    for trade in trades:
        if balance != 0:
            returns.append(trade.net_pnl / balance)
        balance += trade.net_pnl
    return np.array(returns, dtype=float)


def _annualized_ratio(
    per_trade_mean: float, per_trade_std: float, trades_per_year: float
) -> float | None:
    if per_trade_std <= 0 or trades_per_year <= 0:
        return None
    return float((per_trade_mean / per_trade_std) * math.sqrt(trades_per_year))


def compute_metrics(
    trades: list[Trade], equity_curve: pd.Series, *, initial_balance: float
) -> BacktestMetrics:
    if not trades:
        return _empty_metrics()

    net_pnls = np.array([t.net_pnl for t in trades], dtype=float)
    wins = net_pnls[net_pnls > 0]
    losses = net_pnls[net_pnls < 0]

    net_profit = float(net_pnls.sum())
    return_pct = float(net_profit / initial_balance * 100) if initial_balance else 0.0

    first_time = trades[0].entry_time
    last_time = trades[-1].exit_time
    span_days = max((last_time - first_time).total_seconds() / 86400, 0.0)
    years = span_days / _TRADING_DAYS_PER_YEAR

    final_balance = initial_balance + net_profit
    annualized_return_pct: float | None = None
    # Anualizar um periodo menor que 1 dia eleva o retorno a uma potencia
    # gigantesca (1/years na casa das dezenas de milhares) e estoura o
    # float — alem de ser estatisticamente sem sentido. Sem dados de pelo
    # menos 1 dia, o retorno anualizado fica None (nao inventamos numero).
    if span_days >= 1.0 and years > 0 and initial_balance > 0 and final_balance > 0:
        try:
            annualized_return_pct = float(
                ((final_balance / initial_balance) ** (1 / years) - 1) * 100
            )
        except OverflowError:
            annualized_return_pct = None

    max_dd, max_dd_pct, dd_duration = _max_drawdown(equity_curve)

    profit_factor: float | None
    if len(losses) == 0:
        profit_factor = float("inf") if len(wins) > 0 else None
    else:
        profit_factor = float(wins.sum() / abs(losses.sum()))

    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses.mean())) if len(losses) else 0.0
    payoff_ratio = float(avg_win / avg_loss) if avg_loss > 0 else None

    win_rate = float(len(wins) / len(net_pnls))
    expectancy = float(net_pnls.mean())

    trades_per_day = float(len(trades) / span_days) if span_days > 0 else 0.0
    avg_trade_duration_bars = float(np.mean([t.bars_held for t in trades]))
    avg_mae = float(np.mean([t.mae for t in trades]))
    avg_mfe = float(np.mean([t.mfe for t in trades]))

    costs = np.array([t.commission + t.spread_and_slippage_cost for t in trades], dtype=float)
    total_cost = float(costs.sum())
    avg_cost_per_trade = float(costs.mean())

    trade_returns = _trade_returns(trades, initial_balance)
    trades_per_year = float(len(trades) / years) if years > 0 else 0.0
    sharpe_ratio = (
        _annualized_ratio(
            float(trade_returns.mean()), float(trade_returns.std(ddof=1)), trades_per_year
        )
        if len(trade_returns) >= 2
        else None
    )
    downside_returns = trade_returns[trade_returns < 0]
    sortino_ratio = (
        _annualized_ratio(
            float(trade_returns.mean()), float(downside_returns.std(ddof=1)), trades_per_year
        )
        if len(downside_returns) >= 2
        else None
    )
    calmar_ratio = (
        float(annualized_return_pct / max_dd_pct)
        if annualized_return_pct is not None and max_dd_pct > 0
        else None
    )

    max_consecutive_losses = _max_consecutive_losses(trades)
    estimated_risk_of_ruin = _estimate_risk_of_ruin(
        win_rate, payoff_ratio, avg_loss, initial_balance
    )

    result_by_hour: dict[int, float] = {}
    result_by_day_of_week: dict[int, float] = {}
    result_by_regime_trend: dict[str, float] = {}
    result_by_direction: dict[str, float] = {}
    for trade in trades:
        _accumulate(result_by_hour, trade.entry_time.hour, trade.net_pnl)
        _accumulate(result_by_day_of_week, trade.entry_time.weekday(), trade.net_pnl)
        trend_key = trade.regime_at_entry.trend.value if trade.regime_at_entry else "UNKNOWN"
        _accumulate(result_by_regime_trend, trend_key, trade.net_pnl)
        _accumulate(result_by_direction, trade.direction.value, trade.net_pnl)

    return BacktestMetrics(
        net_profit=net_profit,
        return_pct=return_pct,
        annualized_return_pct=annualized_return_pct,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_duration_bars=dd_duration,
        profit_factor=profit_factor,
        payoff_ratio=payoff_ratio,
        expectancy=expectancy,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        num_trades=len(trades),
        trades_per_day=trades_per_day,
        avg_trade_duration_bars=avg_trade_duration_bars,
        avg_mae=avg_mae,
        avg_mfe=avg_mfe,
        avg_cost_per_trade=avg_cost_per_trade,
        total_cost=total_cost,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        max_consecutive_losses=max_consecutive_losses,
        estimated_risk_of_ruin=estimated_risk_of_ruin,
        result_by_hour=result_by_hour,
        result_by_day_of_week=result_by_day_of_week,
        result_by_regime_trend=result_by_regime_trend,
        result_by_direction=result_by_direction,
    )


def _accumulate[K](bucket: dict[K, float], key: K, value: float) -> None:
    bucket[key] = bucket.get(key, 0.0) + value
