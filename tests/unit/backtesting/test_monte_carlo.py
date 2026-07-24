from datetime import UTC, datetime, timedelta

import pytest

from app.backtesting.engine import Trade
from app.backtesting.monte_carlo import simulate_bootstrap
from app.strategies.base import SignalDirection

_T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _trade(net_pnl: float, *, index: int = 0) -> Trade:
    entry_time = _T0 + timedelta(minutes=index * 10)
    return Trade(
        symbol="EURUSD",
        strategy_name="test",
        signal_id=f"trade-{index}",
        direction=SignalDirection.LONG,
        entry_time=entry_time,
        entry_price=1.1000,
        exit_time=entry_time + timedelta(minutes=5),
        exit_price=1.1010,
        stop_loss=1.0900,
        take_profit=1.1100,
        volume=1.0,
        gross_pnl=net_pnl,
        commission=0.0,
        spread_and_slippage_cost=0.0,
        net_pnl=net_pnl,
        exit_reason="take_profit",
        bars_held=5,
        mae=0.0,
        mfe=0.0,
        regime_at_entry=None,
    )


def test_simulate_bootstrap_with_no_trades_returns_neutral_result() -> None:
    result = simulate_bootstrap([], initial_balance=10_000.0, num_simulations=100)

    assert result.num_trades == 0
    assert result.ruin_probability == 0.0
    assert all(v == 10_000.0 for v in result.final_balance_percentiles.values())
    assert all(v == 0.0 for v in result.max_drawdown_pct_percentiles.values())


def test_simulate_bootstrap_identical_trades_give_deterministic_final_balance() -> None:
    trades = [_trade(10.0, index=i) for i in range(20)]
    result = simulate_bootstrap(
        trades, initial_balance=10_000.0, num_simulations=200, random_state=42
    )

    # Reamostrar 20 copias identicas de +10, em qualquer ordem, sempre soma
    # exatamente +200 -- todos os percentis do saldo final devem coincidir.
    assert result.num_trades == 20
    assert result.ruin_probability == 0.0
    for value in result.final_balance_percentiles.values():
        assert value == pytest.approx(10_200.0)


def test_simulate_bootstrap_dominant_loss_trade_produces_high_ruin_probability() -> None:
    # Com 2 trades (+10 e -9000) reamostrados com reposicao, 3 das 4
    # combinacoes igualmente provaveis (perda,perda)/(perda,ganho)/(ganho,perda)
    # cruzam o limiar de ruina de 50% (5000) em algum ponto do caminho -- so
    # (ganho,ganho) nunca cruza.
    trades = [_trade(10.0, index=0), _trade(-9_000.0, index=1)]
    result = simulate_bootstrap(
        trades,
        initial_balance=10_000.0,
        num_simulations=5_000,
        ruin_threshold_pct=50.0,
        random_state=7,
    )

    assert result.ruin_probability > 0.5
    assert result.ruin_threshold_balance == pytest.approx(5_000.0)


def test_simulate_bootstrap_is_deterministic_with_fixed_random_state() -> None:
    trades = [_trade(pnl, index=i) for i, pnl in enumerate([50.0, -30.0, 20.0, -10.0, 15.0])]

    first = simulate_bootstrap(
        trades, initial_balance=10_000.0, num_simulations=500, random_state=123
    )
    second = simulate_bootstrap(
        trades, initial_balance=10_000.0, num_simulations=500, random_state=123
    )

    assert first == second


def test_simulate_bootstrap_rejects_invalid_initial_balance() -> None:
    with pytest.raises(ValueError):
        simulate_bootstrap([_trade(10.0)], initial_balance=0.0)


@pytest.mark.parametrize("ruin_threshold_pct", [0.0, 100.0, -5.0, 150.0])
def test_simulate_bootstrap_rejects_invalid_ruin_threshold(ruin_threshold_pct: float) -> None:
    with pytest.raises(ValueError):
        simulate_bootstrap(
            [_trade(10.0)], initial_balance=10_000.0, ruin_threshold_pct=ruin_threshold_pct
        )
