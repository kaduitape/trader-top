"""Risk Manager dinamico (`app.apexflow.risk`).

Trava por teste as duas regras que impedem o gerenciamento de piorar uma
operacao: o stop nunca anda para tras e o break-even vem antes do
trailing. Cobre tambem os limites que encerram o dia.
"""

from __future__ import annotations

import pytest

from app.apexflow.config import ApexFlowConfig
from app.apexflow.risk import (
    StopMoveKind,
    TradingHaltReason,
    compute_r_multiple,
    dynamic_take_profit,
    evaluate_stop_move,
    evaluate_trading_halt,
)
from app.apexflow.volatility import VolatilityState
from app.strategies.base import SignalDirection
from tests.unit.apexflow.conftest import volatility_reading

CONFIG = ApexFlowConfig(
    break_even_r=0.8,
    trailing_start_r=1.0,
    trailing_step_r=0.5,
    risk_reward_min=1.5,
    min_atr_points=20.0,
    daily_profit_target_pct=3.0,
    max_drawdown_pct=5.0,
)
ENTRY = 1.1000
STOP = 1.0980  # 20 pontos de risco em EURUSD (point 0.0001)
POINT = 0.0001


# --- R multiple ------------------------------------------------------------


def test_r_multiple_measures_progress_in_risk_units() -> None:
    assert compute_r_multiple(
        direction=SignalDirection.LONG,
        entry_price=ENTRY,
        current_price=1.1020,
        stop_loss=STOP,
    ) == pytest.approx(1.0)
    # Venda: risco de 20 pontos (1.1020 - 1.1000). Preco a 1.0990 andou 10
    # pontos a favor, ou seja, 0.5R.
    assert compute_r_multiple(
        direction=SignalDirection.SHORT,
        entry_price=ENTRY,
        current_price=1.0990,
        stop_loss=1.1020,
    ) == pytest.approx(0.5)


def test_zero_risk_reports_none_instead_of_dividing_by_zero() -> None:
    assert (
        compute_r_multiple(
            direction=SignalDirection.LONG,
            entry_price=ENTRY,
            current_price=1.1010,
            stop_loss=ENTRY,
        )
        is None
    )


# --- Take profit dinamico --------------------------------------------------


def test_take_profit_respects_the_minimum_risk_reward() -> None:
    target = dynamic_take_profit(
        direction=SignalDirection.LONG,
        entry_price=ENTRY,
        stop_loss=STOP,
        volatility=volatility_reading(atr_ratio=1.0),
        point=POINT,
        config=CONFIG,
    )
    risk = ENTRY - STOP
    assert target >= ENTRY + risk * CONFIG.risk_reward_min


def test_higher_volatility_stretches_the_target() -> None:
    calm = dynamic_take_profit(
        direction=SignalDirection.LONG, entry_price=ENTRY, stop_loss=STOP,
        volatility=volatility_reading(atr_ratio=1.0), point=POINT, config=CONFIG,
    )
    volatile = dynamic_take_profit(
        direction=SignalDirection.LONG, entry_price=ENTRY, stop_loss=STOP,
        volatility=volatility_reading(VolatilityState.EXPANDING, atr_ratio=1.8),
        point=POINT, config=CONFIG,
    )
    assert volatile > calm


def test_short_target_is_below_entry() -> None:
    target = dynamic_take_profit(
        direction=SignalDirection.SHORT,
        entry_price=ENTRY,
        stop_loss=1.1020,
        volatility=volatility_reading(),
        point=POINT,
        config=CONFIG,
    )
    assert target < ENTRY


# --- Break-even e trailing -------------------------------------------------


def test_no_move_before_break_even_threshold() -> None:
    intent = evaluate_stop_move(
        direction=SignalDirection.LONG,
        entry_price=ENTRY,
        current_price=1.1005,  # 0.25R
        stop_loss=STOP,
        config=CONFIG,
    )
    assert intent.kind == StopMoveKind.NONE
    assert not intent.should_move


def test_break_even_comes_before_trailing() -> None:
    intent = evaluate_stop_move(
        direction=SignalDirection.LONG,
        entry_price=ENTRY,
        current_price=1.1018,  # 0.9R: passou do break-even, nao do trailing
        stop_loss=STOP,
        config=CONFIG,
    )
    assert intent.kind == StopMoveKind.BREAK_EVEN
    assert intent.new_stop_loss == pytest.approx(ENTRY)


def test_trailing_locks_profit_once_past_the_start() -> None:
    intent = evaluate_stop_move(
        direction=SignalDirection.LONG,
        entry_price=ENTRY,
        current_price=1.1040,  # 2R
        stop_loss=STOP,
        config=CONFIG,
    )
    assert intent.kind == StopMoveKind.TRAILING
    assert intent.new_stop_loss is not None
    assert intent.new_stop_loss > ENTRY


def test_stop_never_moves_backwards_on_a_long() -> None:
    """Preco recuou depois de um trailing anterior: a nova proposta seria
    pior que o stop atual e precisa ser recusada."""
    intent = evaluate_stop_move(
        direction=SignalDirection.LONG,
        entry_price=ENTRY,
        current_price=1.1022,
        stop_loss=1.1030,  # ja bem acima da entrada
        config=CONFIG,
    )
    assert intent.kind == StopMoveKind.NONE


def test_stop_never_moves_backwards_on_a_short() -> None:
    intent = evaluate_stop_move(
        direction=SignalDirection.SHORT,
        entry_price=ENTRY,
        current_price=1.0978,
        stop_loss=1.0970,  # ja bem abaixo da entrada
        config=CONFIG,
    )
    assert intent.kind == StopMoveKind.NONE


def test_short_trailing_moves_the_stop_down() -> None:
    intent = evaluate_stop_move(
        direction=SignalDirection.SHORT,
        entry_price=ENTRY,
        current_price=1.0960,  # 2R com stop a 1.1020
        stop_loss=1.1020,
        config=CONFIG,
    )
    assert intent.kind == StopMoveKind.TRAILING
    assert intent.new_stop_loss is not None
    assert intent.new_stop_loss < ENTRY


# --- Limites do dia --------------------------------------------------------


def halt(**kwargs):
    base = {
        "day_start_balance": 10_000.0,
        "current_equity": 10_000.0,
        "daily_pnl": 0.0,
        "consecutive_losses": 0,
        "max_consecutive_losses": 3,
        "config": CONFIG,
        "max_daily_loss_pct": 3.0,
    }
    base.update(kwargs)
    return evaluate_trading_halt(**base)


def test_normal_day_is_not_halted() -> None:
    assert not halt(daily_pnl=50.0).is_halted


def test_daily_loss_limit_halts() -> None:
    result = halt(daily_pnl=-350.0, current_equity=9_650.0)
    assert result.reason == TradingHaltReason.DAILY_LOSS


def test_daily_profit_target_halts() -> None:
    """Bater a meta e motivo legitimo para parar — nao so a perda."""
    result = halt(daily_pnl=350.0, current_equity=10_350.0)
    assert result.reason == TradingHaltReason.DAILY_PROFIT


def test_consecutive_losses_halt() -> None:
    result = halt(consecutive_losses=3, daily_pnl=-20.0)
    assert result.reason == TradingHaltReason.CONSECUTIVE_LOSSES


def test_drawdown_from_peak_halts() -> None:
    result = halt(daily_pnl=100.0, current_equity=10_100.0, peak_equity=11_000.0)
    assert result.reason == TradingHaltReason.DRAWDOWN


def test_loss_limit_takes_priority_over_profit_target() -> None:
    result = halt(daily_pnl=-400.0, current_equity=9_600.0)
    assert result.reason == TradingHaltReason.DAILY_LOSS


def test_missing_balance_reports_instead_of_guessing() -> None:
    result = halt(day_start_balance=0.0)
    assert not result.is_halted
    assert "indisponivel" in result.detail
