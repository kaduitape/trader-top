from app.risk.circuit_breaker import CircuitBreakerLevel, DailyStats, classify_circuit_breaker
from app.risk.config import RiskLimits

_LIMITS = RiskLimits(max_daily_loss_pct=3.0, max_consecutive_losses=3)


def _stats(
    *, trades_today=0, consecutive_losses=0, daily_pnl=0.0, open_positions_count=0
) -> DailyStats:
    return DailyStats(
        trades_today=trades_today,
        consecutive_losses=consecutive_losses,
        daily_pnl=daily_pnl,
        open_positions_count=open_positions_count,
        last_trade_time=None,
    )


def test_no_losses_no_drawdown_is_none() -> None:
    level = classify_circuit_breaker(_stats(daily_pnl=50.0), _LIMITS, initial_balance=10_000.0)
    assert level == CircuitBreakerLevel.NONE


def test_daily_loss_below_warning_threshold_is_none() -> None:
    # 3% * 0.7 = 2.1% -> abaixo disso ainda e NONE
    level = classify_circuit_breaker(_stats(daily_pnl=-100.0), _LIMITS, initial_balance=10_000.0)
    assert level == CircuitBreakerLevel.NONE


def test_daily_loss_at_seventy_percent_of_max_is_warning() -> None:
    # 2.1% de 10_000 = 210
    level = classify_circuit_breaker(_stats(daily_pnl=-210.0), _LIMITS, initial_balance=10_000.0)
    assert level == CircuitBreakerLevel.WARNING


def test_daily_loss_at_max_is_hard_block() -> None:
    level = classify_circuit_breaker(_stats(daily_pnl=-300.0), _LIMITS, initial_balance=10_000.0)
    assert level == CircuitBreakerLevel.HARD_BLOCK


def test_daily_loss_beyond_max_is_still_hard_block() -> None:
    level = classify_circuit_breaker(_stats(daily_pnl=-1000.0), _LIMITS, initial_balance=10_000.0)
    assert level == CircuitBreakerLevel.HARD_BLOCK


def test_consecutive_losses_at_limit_is_soft_block() -> None:
    level = classify_circuit_breaker(
        _stats(consecutive_losses=3, daily_pnl=0.0), _LIMITS, initial_balance=10_000.0
    )
    assert level == CircuitBreakerLevel.SOFT_BLOCK


def test_consecutive_losses_below_limit_is_none() -> None:
    level = classify_circuit_breaker(
        _stats(consecutive_losses=2, daily_pnl=0.0), _LIMITS, initial_balance=10_000.0
    )
    assert level == CircuitBreakerLevel.NONE


def test_hard_block_takes_priority_over_soft_block() -> None:
    level = classify_circuit_breaker(
        _stats(consecutive_losses=5, daily_pnl=-1000.0), _LIMITS, initial_balance=10_000.0
    )
    assert level == CircuitBreakerLevel.HARD_BLOCK


def test_zero_initial_balance_is_emergency_stop() -> None:
    level = classify_circuit_breaker(_stats(), _LIMITS, initial_balance=0.0)
    assert level == CircuitBreakerLevel.EMERGENCY_STOP


def test_negative_initial_balance_is_emergency_stop() -> None:
    level = classify_circuit_breaker(_stats(), _LIMITS, initial_balance=-500.0)
    assert level == CircuitBreakerLevel.EMERGENCY_STOP


def test_positive_daily_pnl_never_triggers_loss_based_levels() -> None:
    level = classify_circuit_breaker(
        _stats(consecutive_losses=0, daily_pnl=99999.0), _LIMITS, initial_balance=10_000.0
    )
    assert level == CircuitBreakerLevel.NONE
