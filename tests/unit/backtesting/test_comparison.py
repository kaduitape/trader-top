from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.backtesting.comparison import build_comparison_row, format_comparison_table
from app.backtesting.engine import BacktestResult, Trade
from app.strategies.base import SignalDirection

_T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _trade(net_pnl: float, strategy_name: str) -> Trade:
    return Trade(
        symbol="EURUSD",
        strategy_name=strategy_name,
        signal_id="x",
        direction=SignalDirection.LONG,
        entry_time=_T0,
        entry_price=1.1000,
        exit_time=_T0 + timedelta(minutes=5),
        exit_price=1.1010,
        stop_loss=1.0900,
        take_profit=1.1100,
        volume=1.0,
        gross_pnl=net_pnl + 1.0,
        commission=1.0,
        spread_and_slippage_cost=0.5,
        net_pnl=net_pnl,
        exit_reason="take_profit",
        bars_held=5,
        mae=0.001,
        mfe=0.002,
        regime_at_entry=None,
    )


def test_build_comparison_row_matches_metrics() -> None:
    result = BacktestResult(
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="strategy_a",
        initial_balance=10_000.0,
        trades=[_trade(100.0, "strategy_a"), _trade(-40.0, "strategy_a")],
        equity_curve=pd.Series([10_000.0, 10_100.0, 10_060.0]),
    )

    row = build_comparison_row(result, initial_balance=10_000.0)

    assert row.strategy_name == "strategy_a"
    assert row.num_trades == 2
    assert row.net_profit == pytest.approx(60.0)
    assert row.return_pct == pytest.approx(0.6)
    assert row.win_rate == pytest.approx(0.5)


def test_build_comparison_row_with_no_trades() -> None:
    result = BacktestResult(
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="strategy_b",
        initial_balance=10_000.0,
        trades=[],
        equity_curve=pd.Series(dtype=float),
    )

    row = build_comparison_row(result, initial_balance=10_000.0)

    assert row.num_trades == 0
    assert row.net_profit == 0.0
    assert row.profit_factor is None


def test_format_comparison_table_preserves_input_order_never_ranks() -> None:
    rows = [
        build_comparison_row(
            BacktestResult(
                symbol="EURUSD",
                timeframe="M1",
                strategy_name="worse_strategy",
                initial_balance=10_000.0,
                trades=[_trade(-100.0, "worse_strategy")],
                equity_curve=pd.Series([10_000.0, 9_900.0]),
            ),
            initial_balance=10_000.0,
        ),
        build_comparison_row(
            BacktestResult(
                symbol="EURUSD",
                timeframe="M1",
                strategy_name="better_strategy",
                initial_balance=10_000.0,
                trades=[_trade(500.0, "better_strategy")],
                equity_curve=pd.Series([10_000.0, 10_500.0]),
            ),
            initial_balance=10_000.0,
        ),
    ]

    text = format_comparison_table(rows)
    lines = text.splitlines()

    # A ordem das linhas deve seguir a ordem de entrada (worse antes de
    # better), nunca reordenada por lucro — o relatorio nao elege vencedora.
    assert "worse_strategy" in lines[1]
    assert "better_strategy" in lines[2]


def test_format_comparison_table_with_no_rows_only_has_header() -> None:
    text = format_comparison_table([])
    assert len(text.splitlines()) == 1
    assert "estrategia" in text
