from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.backtesting.engine import Trade
from app.backtesting.metrics import compute_metrics
from app.market.regimes import MarketRegime, Trend, VolatilityLevel
from app.strategies.base import SignalDirection

_T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _regime(trend: Trend) -> MarketRegime:
    return MarketRegime(
        trend=trend,
        volatility=VolatilityLevel.NORMAL,
        spread_adequate=True,
        liquidity_adequate=True,
        is_transition=False,
        is_extraordinary_event=False,
    )


def _trade(
    net_pnl: float,
    *,
    entry_time: datetime,
    exit_time: datetime,
    direction: SignalDirection = SignalDirection.LONG,
    commission: float = 1.0,
    spread_cost: float = 0.5,
    regime_trend: Trend | None = None,
    bars_held: int = 5,
) -> Trade:
    return Trade(
        symbol="EURUSD",
        strategy_name="test",
        signal_id="x",
        direction=direction,
        entry_time=entry_time,
        entry_price=1.1000,
        exit_time=exit_time,
        exit_price=1.1010,
        stop_loss=1.0900,
        take_profit=1.1100,
        volume=1.0,
        gross_pnl=net_pnl + commission,
        commission=commission,
        spread_and_slippage_cost=spread_cost,
        net_pnl=net_pnl,
        exit_reason="take_profit",
        bars_held=bars_held,
        mae=0.0010,
        mfe=0.0020,
        regime_at_entry=_regime(regime_trend) if regime_trend is not None else None,
    )


def test_compute_metrics_with_no_trades_returns_neutral_values() -> None:
    metrics = compute_metrics([], pd.Series(dtype=float), initial_balance=10_000.0)

    assert metrics.num_trades == 0
    assert metrics.net_profit == 0.0
    assert metrics.profit_factor is None
    assert metrics.sharpe_ratio is None
    assert metrics.estimated_risk_of_ruin is None
    assert metrics.result_by_hour == {}


def test_compute_metrics_basic_aggregates() -> None:
    trades = [
        _trade(100.0, entry_time=_T0, exit_time=_T0 + timedelta(minutes=5), regime_trend=Trend.UP),
        _trade(
            -50.0,
            entry_time=_T0 + timedelta(days=1),
            exit_time=_T0 + timedelta(days=1, minutes=5),
            regime_trend=Trend.SIDEWAYS,
        ),
        _trade(
            200.0,
            entry_time=_T0 + timedelta(days=2),
            exit_time=_T0 + timedelta(days=2, minutes=5),
            regime_trend=Trend.UP,
        ),
        _trade(
            -50.0,
            entry_time=_T0 + timedelta(days=3),
            exit_time=_T0 + timedelta(days=3, minutes=5),
            regime_trend=Trend.DOWN,
            direction=SignalDirection.SHORT,
        ),
    ]
    equity_curve = pd.Series(
        [10_000.0, 10_100.0, 10_050.0, 10_250.0, 10_200.0],
        index=[
            _T0,
            _T0 + timedelta(minutes=5),
            _T0 + timedelta(days=1, minutes=5),
            _T0 + timedelta(days=2, minutes=5),
            _T0 + timedelta(days=3, minutes=5),
        ],
    )

    metrics = compute_metrics(trades, equity_curve, initial_balance=10_000.0)

    assert metrics.num_trades == 4
    assert metrics.net_profit == pytest.approx(200.0)
    assert metrics.return_pct == pytest.approx(2.0)
    assert metrics.win_rate == pytest.approx(0.5)
    assert metrics.avg_win == pytest.approx(150.0)
    assert metrics.avg_loss == pytest.approx(50.0)
    assert metrics.profit_factor == pytest.approx(3.0)
    assert metrics.payoff_ratio == pytest.approx(3.0)
    assert metrics.expectancy == pytest.approx(50.0)
    assert metrics.max_consecutive_losses == 1
    assert metrics.max_drawdown == pytest.approx(50.0)
    assert metrics.max_drawdown_pct == pytest.approx(50.0 / 10_100.0 * 100)
    assert metrics.max_drawdown_duration_bars == 1
    assert metrics.result_by_direction == {"LONG": 250.0, "SHORT": -50.0}
    assert metrics.result_by_regime_trend == {"UP": 300.0, "SIDEWAYS": -50.0, "DOWN": -50.0}
    assert metrics.total_cost == pytest.approx(
        sum(t.commission + t.spread_and_slippage_cost for t in trades)
    )


def test_compute_metrics_all_wins_gives_infinite_profit_factor_and_no_ruin_estimate() -> None:
    trades = [
        _trade(100.0, entry_time=_T0, exit_time=_T0 + timedelta(minutes=5)),
        _trade(
            50.0,
            entry_time=_T0 + timedelta(days=1),
            exit_time=_T0 + timedelta(days=1, minutes=5),
        ),
    ]
    equity_curve = pd.Series([10_000.0, 10_100.0, 10_150.0])

    metrics = compute_metrics(trades, equity_curve, initial_balance=10_000.0)

    assert metrics.profit_factor == float("inf")
    assert metrics.avg_loss == 0.0
    assert metrics.estimated_risk_of_ruin is None


def test_compute_metrics_all_losses_gives_zero_profit_factor() -> None:
    trades = [
        _trade(-100.0, entry_time=_T0, exit_time=_T0 + timedelta(minutes=5)),
        _trade(
            -50.0,
            entry_time=_T0 + timedelta(days=1),
            exit_time=_T0 + timedelta(days=1, minutes=5),
        ),
    ]
    equity_curve = pd.Series([10_000.0, 9_900.0, 9_850.0])

    metrics = compute_metrics(trades, equity_curve, initial_balance=10_000.0)

    assert metrics.profit_factor == pytest.approx(0.0)
    assert metrics.win_rate == 0.0
    assert metrics.estimated_risk_of_ruin is None


def test_compute_metrics_single_trade_has_no_sharpe_or_sortino() -> None:
    trades = [_trade(100.0, entry_time=_T0, exit_time=_T0 + timedelta(minutes=5))]
    equity_curve = pd.Series([10_000.0, 10_100.0])

    metrics = compute_metrics(trades, equity_curve, initial_balance=10_000.0)

    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None


def test_max_consecutive_losses_counts_longest_streak() -> None:
    trades = [
        _trade(10.0, entry_time=_T0, exit_time=_T0 + timedelta(minutes=1)),
        _trade(-10.0, entry_time=_T0 + timedelta(minutes=2), exit_time=_T0 + timedelta(minutes=3)),
        _trade(-10.0, entry_time=_T0 + timedelta(minutes=4), exit_time=_T0 + timedelta(minutes=5)),
        _trade(-10.0, entry_time=_T0 + timedelta(minutes=6), exit_time=_T0 + timedelta(minutes=7)),
        _trade(10.0, entry_time=_T0 + timedelta(minutes=8), exit_time=_T0 + timedelta(minutes=9)),
        _trade(
            -10.0, entry_time=_T0 + timedelta(minutes=10), exit_time=_T0 + timedelta(minutes=11)
        ),
    ]
    equity_curve = pd.Series([10_000.0] * 7)

    metrics = compute_metrics(trades, equity_curve, initial_balance=10_000.0)

    assert metrics.max_consecutive_losses == 3


def test_result_by_hour_and_day_of_week_grouping() -> None:
    monday_9am = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)  # segunda-feira
    tuesday_14pm = datetime(2026, 1, 6, 14, 0, tzinfo=UTC)  # terca-feira
    trades = [
        _trade(30.0, entry_time=monday_9am, exit_time=monday_9am + timedelta(minutes=5)),
        _trade(-10.0, entry_time=tuesday_14pm, exit_time=tuesday_14pm + timedelta(minutes=5)),
    ]
    equity_curve = pd.Series([10_000.0, 10_030.0, 10_020.0])

    metrics = compute_metrics(trades, equity_curve, initial_balance=10_000.0)

    assert metrics.result_by_hour == {9: 30.0, 14: -10.0}
    assert metrics.result_by_day_of_week == {0: 30.0, 1: -10.0}
