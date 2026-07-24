from datetime import UTC, datetime, timedelta

from app.mt5.account import AccountSnapshot
from app.mt5.symbol_mapper import SymbolSpecification
from app.risk.circuit_breaker import CircuitBreakerLevel, DailyStats
from app.risk.config import RiskLimits
from app.risk.engine import evaluate_signal
from app.strategies.base import Signal, SignalDirection

_NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def _signal(*, stop_loss: float = 1.0990, take_profit: float = 1.1050) -> Signal:
    return Signal(
        symbol="EURUSD",
        strategy_name="test_strategy",
        direction=SignalDirection.LONG,
        generated_at=_NOW,
        reference_price=1.1000,
        stop_loss=stop_loss,
        take_profit=take_profit,
        valid_until=_NOW + timedelta(minutes=5),
        reason="test",
        regime_required="none",
        confidence=1.0,
        features_used={},
    )


def _account(*, is_demo: bool = True, balance: float = 10_000.0) -> AccountSnapshot:
    return AccountSnapshot(
        login=123,
        server="Test-Demo",
        balance=balance,
        equity=balance,
        margin=0.0,
        margin_free=balance,
        currency="USD",
        leverage=100,
        trade_mode=0,
        is_demo=is_demo,
    )


def _symbol_spec() -> SymbolSpecification:
    return SymbolSpecification(
        name="EURUSD",
        description="Euro vs US Dollar",
        digits=5,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=2,
        trade_mode=4,
        visible=True,
    )


def _stats(**overrides: object) -> DailyStats:
    base: dict[str, object] = {
        "trades_today": 0,
        "consecutive_losses": 0,
        "daily_pnl": 0.0,
        "open_positions_count": 0,
        "last_trade_time": None,
    }
    base.update(overrides)
    return DailyStats(**base)  # type: ignore[arg-type]


_LIMITS = RiskLimits()


def test_approved_signal_computes_positive_volume() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(),
        limits=_LIMITS,
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is True
    assert decision.computed_volume is not None
    assert decision.computed_volume > 0.0
    assert decision.circuit_breaker_level == CircuitBreakerLevel.NONE


def test_hard_block_rejects_regardless_of_everything_else() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(daily_pnl=-1000.0),
        limits=_LIMITS,
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False
    assert decision.circuit_breaker_level == CircuitBreakerLevel.HARD_BLOCK
    assert decision.computed_volume is None


def test_soft_block_from_consecutive_losses_rejects() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(consecutive_losses=3),
        limits=_LIMITS,
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False
    assert decision.circuit_breaker_level == CircuitBreakerLevel.SOFT_BLOCK


def test_warning_level_still_approves() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(daily_pnl=-210.0),  # 2.1% de 10_000, 70% do limite de 3%
        limits=_LIMITS,
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is True
    assert decision.circuit_breaker_level == CircuitBreakerLevel.WARNING
    assert "WARNING" in decision.reason


def test_real_account_is_always_rejected() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(),
        limits=_LIMITS,
        account=_account(is_demo=False),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False
    assert "demo" in decision.reason.lower()


def test_max_simultaneous_positions_rejects() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(open_positions_count=1),
        limits=RiskLimits(max_simultaneous_positions=1),
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False
    assert "posições simultâneas" in decision.reason or "posicoes simultaneas" in decision.reason


def test_max_trades_per_day_rejects() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(trades_today=10),
        limits=RiskLimits(max_trades_per_day=10),
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False


def test_min_interval_between_trades_rejects() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(last_trade_time=_NOW - timedelta(seconds=10)),
        limits=RiskLimits(min_seconds_between_trades=60),
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False


def test_interval_respected_when_enough_time_elapsed() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(last_trade_time=_NOW - timedelta(seconds=120)),
        limits=RiskLimits(min_seconds_between_trades=60),
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is True


def test_spread_above_limit_rejects() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(),
        limits=RiskLimits(max_spread_points=10.0),
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=50.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False


def test_signal_without_stop_loss_is_rejected() -> None:
    decision = evaluate_signal(
        _signal(stop_loss=1.1000),  # igual ao preco de referencia
        stats=_stats(),
        limits=_LIMITS,
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False
    assert "stop" in decision.reason.lower()


def test_volume_below_minimum_lot_is_rejected() -> None:
    decision = evaluate_signal(
        _signal(),
        stats=_stats(),
        limits=RiskLimits(risk_per_trade_pct=0.001),
        account=_account(balance=100.0),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision.approved is False
    assert decision.computed_volume is None


def test_position_sizing_does_not_depend_on_consecutive_losses() -> None:
    """Garantia anti-martingale de ponta a ponta: duas avaliacoes com o
    MESMO saldo/sinal, diferindo apenas em `consecutive_losses` (abaixo
    do limite de SOFT_BLOCK), devem produzir o MESMO volume."""
    decision_no_losses = evaluate_signal(
        _signal(),
        stats=_stats(consecutive_losses=0),
        limits=_LIMITS,
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    decision_two_losses = evaluate_signal(
        _signal(),
        stats=_stats(consecutive_losses=2),
        limits=_LIMITS,
        account=_account(),
        symbol_spec=_symbol_spec(),
        current_spread_points=2.0,
        feed_last_update_time=_NOW,
        now=_NOW,
    )
    assert decision_no_losses.computed_volume == decision_two_losses.computed_volume
