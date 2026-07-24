"""Walk-forward para estratégias baseadas em regra (Fase 9).

O prompt mestre proíbe eleger uma estratégia "pronta" só pelo lucro
líquido de um único backtest (Fase 6) — walk-forward é a resposta: a
mesma estratégia é rodada, de forma independente (saldo e instância
novos), em `n_windows` janelas cronológicas contíguas e não sobrepostas
que cobrem toda a série. Uma estratégia cujo resultado depende de uma
única janela excepcional não passa no julgamento de estabilidade, mesmo
que o lucro agregado seja positivo.

As janelas nunca se sobrepõem (não há vazamento entre elas) e são
sempre cronológicas (nunca embaralhadas). Isso é deliberadamente mais
simples que uma reotimização de parâmetros por janela (walk-forward
"clássico" de otimização) — como as estratégias aqui são baseadas em
regra, não têm parâmetros ajustados por dados, o objetivo é medir
consistência temporal, não reotimizar.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.backtesting.engine import BacktestConfig, BacktestResult, CandleBacktestEngine, Trade
from app.backtesting.metrics import BacktestMetrics, compute_metrics
from app.market.features import CandleFeatureLike
from app.strategies.base import Strategy


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    index: int
    start_time: datetime
    end_time: datetime
    num_candles: int
    result: BacktestResult
    metrics: BacktestMetrics


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    windows: list[WalkForwardWindow]
    aggregate_metrics: BacktestMetrics
    profitable_window_ratio: float
    max_single_window_profit_share: float | None
    is_stable: bool
    stability_notes: list[str]


def split_sequential_windows(n_bars: int, *, n_windows: int) -> list[tuple[int, int]]:
    """Janelas contíguas e não sobrepostas cobrindo toda a série. A última
    janela absorve o resto quando `n_bars` não é divisível por `n_windows`."""
    if n_windows < 1:
        raise ValueError("n_windows deve ser >= 1.")
    if n_bars < n_windows:
        raise ValueError(f"dados insuficientes ({n_bars} barra(s)) para {n_windows} janela(s).")

    base_size = n_bars // n_windows
    windows: list[tuple[int, int]] = []
    start = 0
    for w in range(n_windows):
        end = start + base_size if w < n_windows - 1 else n_bars
        windows.append((start, end))
        start = end
    return windows


def _stitch_equity_curves(curves: Sequence[pd.Series], *, initial_balance: float) -> pd.Series:
    """Encadeia as curvas de patrimônio de cada janela como se fosse uma
    única conta contínua: cada janela reinicia sozinha em `initial_balance`
    (saldo/instância novos por design), então cada curva subsequente é
    deslocada pelo saldo acumulado das janelas anteriores — sem isso, a
    concatenação criaria quedas artificiais entre janelas."""
    non_empty = [c for c in curves if not c.empty]
    if not non_empty:
        return pd.Series(dtype=float)

    stitched: list[pd.Series] = []
    offset = 0.0
    for curve in non_empty:
        adjusted = curve + offset
        stitched.append(adjusted)
        offset += curve.iloc[-1] - initial_balance
    return pd.concat(stitched)


def run_walk_forward(
    strategy_factory: Callable[[], Strategy],
    candles: Sequence[CandleFeatureLike],
    *,
    n_windows: int,
    config: BacktestConfig,
    point: float,
    contract_size: float,
    initial_balance: float,
    symbol: str,
    timeframe: str,
    min_trades_per_window: int = 5,
    max_single_window_profit_share_threshold: float = 0.8,
) -> WalkForwardReport:
    n = len(candles)
    window_ranges = split_sequential_windows(n, n_windows=n_windows)

    windows: list[WalkForwardWindow] = []
    all_trades: list[Trade] = []
    all_equity_curves: list[pd.Series] = []

    for idx, (start, end) in enumerate(window_ranges):
        window_candles = candles[start:end]
        strategy = strategy_factory()
        engine = CandleBacktestEngine(
            strategy,
            config,
            point=point,
            contract_size=contract_size,
            initial_balance=initial_balance,
        )
        result = engine.run(window_candles, symbol=symbol, timeframe=timeframe)
        metrics = compute_metrics(
            result.trades, result.equity_curve, initial_balance=initial_balance
        )
        windows.append(
            WalkForwardWindow(
                index=idx,
                start_time=window_candles[0].open_time,
                end_time=window_candles[-1].open_time,
                num_candles=len(window_candles),
                result=result,
                metrics=metrics,
            )
        )
        all_trades.extend(result.trades)
        all_equity_curves.append(result.equity_curve)

    aggregate_equity = _stitch_equity_curves(all_equity_curves, initial_balance=initial_balance)
    aggregate_metrics = compute_metrics(
        all_trades, aggregate_equity, initial_balance=initial_balance
    )

    notes: list[str] = []
    eligible = [w for w in windows if w.metrics.num_trades >= min_trades_per_window]
    if len(eligible) < len(windows):
        notes.append(
            f"{len(windows) - len(eligible)} janela(s) com menos de "
            f"{min_trades_per_window} trade(s) — excluída(s) do julgamento de estabilidade."
        )

    if not eligible:
        notes.append("nenhuma janela com trades suficientes para avaliar estabilidade.")
        return WalkForwardReport(
            windows=windows,
            aggregate_metrics=aggregate_metrics,
            profitable_window_ratio=0.0,
            max_single_window_profit_share=None,
            is_stable=False,
            stability_notes=notes,
        )

    profitable_ratio = sum(1 for w in eligible if w.metrics.net_profit >= 0) / len(eligible)
    if profitable_ratio < 0.5:
        notes.append(
            f"apenas {profitable_ratio:.0%} das janelas elegíveis foram lucrativas (mínimo: 50%)."
        )

    positive_profits = [w.metrics.net_profit for w in eligible if w.metrics.net_profit > 0]
    total_positive = sum(positive_profits)
    max_share = (max(positive_profits) / total_positive) if total_positive > 0 else None
    if max_share is not None and max_share > max_single_window_profit_share_threshold:
        notes.append(
            f"uma única janela responde por {max_share:.0%} do lucro positivo total "
            f"(limite: {max_single_window_profit_share_threshold:.0%}) — risco de resultado "
            "dependente de um período excepcional."
        )

    is_stable = profitable_ratio >= 0.5 and (
        max_share is None or max_share <= max_single_window_profit_share_threshold
    )

    return WalkForwardReport(
        windows=windows,
        aggregate_metrics=aggregate_metrics,
        profitable_window_ratio=profitable_ratio,
        max_single_window_profit_share=max_share,
        is_stable=is_stable,
        stability_notes=notes,
    )
