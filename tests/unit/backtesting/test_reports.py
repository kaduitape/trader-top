import dataclasses
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.backtesting.engine import BacktestResult, Trade
from app.backtesting.metrics import BacktestMetrics
from app.backtesting.reports import build_report, format_report_text, report_to_dict
from app.strategies.base import SignalDirection

_T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _trade(net_pnl: float) -> Trade:
    return Trade(
        symbol="EURUSD",
        strategy_name="test",
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


def test_build_report_with_trades() -> None:
    result = BacktestResult(
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="test",
        initial_balance=10_000.0,
        trades=[_trade(100.0), _trade(-30.0)],
        equity_curve=pd.Series([10_000.0, 10_100.0, 10_070.0]),
    )

    report = build_report(result)

    assert report.symbol == "EURUSD"
    assert report.timeframe == "M1"
    assert report.strategy_name == "test"
    assert report.period_start == _T0
    assert report.period_end == _T0 + timedelta(minutes=5)
    assert report.final_balance == pytest.approx(10_070.0)
    assert report.metrics.num_trades == 2


def test_build_report_with_no_trades() -> None:
    result = BacktestResult(
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="test",
        initial_balance=10_000.0,
        trades=[],
        equity_curve=pd.Series(dtype=float),
    )

    report = build_report(result)

    assert report.period_start is None
    assert report.period_end is None
    assert report.final_balance == 10_000.0
    assert report.metrics.num_trades == 0


def test_report_to_dict_exposes_all_metrics_and_trades() -> None:
    result = BacktestResult(
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="test",
        initial_balance=10_000.0,
        trades=[_trade(100.0)],
        equity_curve=pd.Series([10_000.0, 10_100.0]),
    )
    report = build_report(result)

    payload = report_to_dict(report)

    assert payload["symbol"] == "EURUSD"
    expected_metric_fields = {f.name for f in dataclasses.fields(BacktestMetrics)}
    assert set(payload["metrics"].keys()) == expected_metric_fields
    assert len(payload["trades"]) == 1
    assert payload["trades"][0]["net_pnl"] == 100.0


def test_format_report_text_contains_key_sections() -> None:
    result = BacktestResult(
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="test",
        initial_balance=10_000.0,
        trades=[_trade(100.0), _trade(-30.0)],
        equity_curve=pd.Series([10_000.0, 10_100.0, 10_070.0]),
    )
    report = build_report(result)

    text = format_report_text(report)

    assert "Backtest: test | EURUSD M1" in text
    assert "Profit factor" in text
    assert "Sharpe" in text
    assert "Risco de ruina estimado" in text
