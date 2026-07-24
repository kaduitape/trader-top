"""Simulação de Monte Carlo por reamostragem de trades (Fase 9).

Substitui a aproximação puramente analítica de risco de ruína
(`app.backtesting.metrics._estimate_risk_of_ruin`, Fase 5) por uma
estimativa empírica: reamostra a ORDEM dos trades já realizados, com
reposição (bootstrap i.i.d. — método clássico de "reordenação de
trades" para risco de ruína), milhares de vezes. **Nunca inventa um
trade novo nem altera o resultado de um trade** — apenas embaralha a
sequência em que os resultados já observados poderiam ter ocorrido.

Isso responde a uma pergunta diferente da do backtest original: não "o
que aconteceu nesta ordem específica", mas "com que frequência uma
sequência plausível destes MESMOS resultados levaria a conta à ruína".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.backtesting.engine import Trade

_DEFAULT_PERCENTILES: tuple[int, ...] = (5, 25, 50, 75, 95)


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    num_simulations: int
    num_trades: int
    ruin_probability: float
    ruin_threshold_balance: float
    final_balance_percentiles: dict[int, float]
    max_drawdown_pct_percentiles: dict[int, float]


def simulate_bootstrap(
    trades: list[Trade],
    *,
    initial_balance: float,
    num_simulations: int = 1000,
    ruin_threshold_pct: float = 50.0,
    percentiles: tuple[int, ...] = _DEFAULT_PERCENTILES,
    random_state: int | None = None,
) -> MonteCarloResult:
    if initial_balance <= 0:
        raise ValueError("initial_balance deve ser positivo.")
    if not 0.0 < ruin_threshold_pct < 100.0:
        raise ValueError("ruin_threshold_pct deve estar entre 0 e 100 (exclusive).")

    ruin_threshold_balance = initial_balance * (ruin_threshold_pct / 100.0)

    if not trades:
        return MonteCarloResult(
            num_simulations=num_simulations,
            num_trades=0,
            ruin_probability=0.0,
            ruin_threshold_balance=ruin_threshold_balance,
            final_balance_percentiles=dict.fromkeys(percentiles, initial_balance),
            max_drawdown_pct_percentiles=dict.fromkeys(percentiles, 0.0),
        )

    net_pnls = np.array([t.net_pnl for t in trades], dtype=float)
    n_trades = len(net_pnls)
    rng = np.random.default_rng(random_state)

    final_balances = np.empty(num_simulations, dtype=float)
    max_drawdowns_pct = np.empty(num_simulations, dtype=float)
    ruin_count = 0

    for sim in range(num_simulations):
        order = rng.integers(0, n_trades, size=n_trades)
        path = np.concatenate(([initial_balance], initial_balance + np.cumsum(net_pnls[order])))
        final_balances[sim] = path[-1]

        running_max = np.maximum.accumulate(path)
        drawdown = running_max - path
        peak_at_trough = running_max[int(np.argmax(drawdown))]
        max_drawdowns_pct[sim] = (
            float(drawdown.max() / peak_at_trough * 100) if peak_at_trough > 0 else 0.0
        )

        if path.min() <= ruin_threshold_balance:
            ruin_count += 1

    return MonteCarloResult(
        num_simulations=num_simulations,
        num_trades=n_trades,
        ruin_probability=ruin_count / num_simulations,
        ruin_threshold_balance=ruin_threshold_balance,
        final_balance_percentiles={p: float(np.percentile(final_balances, p)) for p in percentiles},
        max_drawdown_pct_percentiles={
            p: float(np.percentile(max_drawdowns_pct, p)) for p in percentiles
        },
    )
