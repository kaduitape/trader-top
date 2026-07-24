import pytest

from app.market.trade_levels import compute_trade_levels
from app.strategies.base import SignalDirection


def test_long_atr_only_fallback_when_no_structure_stop() -> None:
    levels = compute_trade_levels(
        direction=SignalDirection.LONG,
        entry_price=100.0,
        atr=2.0,
        structure_stop_price=None,
        atr_multiplier_stop=1.5,
    )

    assert levels.stop_loss == pytest.approx(100.0 - 1.5 * 2.0)
    risk = 100.0 - levels.stop_loss
    assert levels.take_profit_1 == pytest.approx(100.0 + risk * 1.0)
    assert levels.take_profit_2 == pytest.approx(100.0 + risk * 2.0)
    assert levels.take_profit_3 == pytest.approx(100.0 + risk * 3.0)
    assert levels.risk_reward_1 == 1.0
    assert levels.risk_reward_2 == 2.0
    assert levels.risk_reward_3 == 3.0
    assert levels.break_even_price == pytest.approx(100.0 + risk * 1.0)
    assert levels.trailing_activation_price == pytest.approx(100.0 + risk * 1.5)


def test_long_prefers_structure_stop_when_tighter() -> None:
    # ATR stop ficaria em 97.0; nivel de estrutura em 98.0 (mais apertado,
    # menos risco) -- deve vencer.
    levels = compute_trade_levels(
        direction=SignalDirection.LONG,
        entry_price=100.0,
        atr=2.0,
        structure_stop_price=98.0,
        atr_multiplier_stop=1.5,
    )
    assert levels.stop_loss == pytest.approx(98.0)


def test_long_ignores_structure_stop_when_looser_than_atr() -> None:
    # ATR stop (97.0) e mais apertado que o nivel de estrutura (90.0) --
    # ATR deve vencer.
    levels = compute_trade_levels(
        direction=SignalDirection.LONG,
        entry_price=100.0,
        atr=2.0,
        structure_stop_price=90.0,
        atr_multiplier_stop=1.5,
    )
    assert levels.stop_loss == pytest.approx(97.0)


def test_long_ignores_structure_stop_on_wrong_side_of_entry() -> None:
    # Nivel de estrutura ACIMA da entrada nao faz sentido como stop de uma
    # posicao comprada -- deve ser ignorado, cai para ATR.
    levels = compute_trade_levels(
        direction=SignalDirection.LONG,
        entry_price=100.0,
        atr=2.0,
        structure_stop_price=105.0,
        atr_multiplier_stop=1.5,
    )
    assert levels.stop_loss == pytest.approx(97.0)


def test_short_atr_only_fallback() -> None:
    levels = compute_trade_levels(
        direction=SignalDirection.SHORT,
        entry_price=100.0,
        atr=2.0,
        structure_stop_price=None,
        atr_multiplier_stop=1.5,
    )

    assert levels.stop_loss == pytest.approx(103.0)
    risk = levels.stop_loss - 100.0
    assert levels.take_profit_1 == pytest.approx(100.0 - risk * 1.0)
    assert levels.take_profit_2 == pytest.approx(100.0 - risk * 2.0)
    assert levels.take_profit_3 == pytest.approx(100.0 - risk * 3.0)


def test_short_prefers_structure_stop_when_tighter() -> None:
    levels = compute_trade_levels(
        direction=SignalDirection.SHORT,
        entry_price=100.0,
        atr=2.0,
        structure_stop_price=102.0,
        atr_multiplier_stop=1.5,
    )
    assert levels.stop_loss == pytest.approx(102.0)


def test_long_short_symmetry() -> None:
    long_levels = compute_trade_levels(
        direction=SignalDirection.LONG,
        entry_price=100.0,
        atr=2.0,
        structure_stop_price=None,
    )
    short_levels = compute_trade_levels(
        direction=SignalDirection.SHORT,
        entry_price=100.0,
        atr=2.0,
        structure_stop_price=None,
    )

    long_risk = 100.0 - long_levels.stop_loss
    short_risk = short_levels.stop_loss - 100.0
    assert long_risk == pytest.approx(short_risk)
    assert (100.0 - long_levels.take_profit_1) == pytest.approx(short_levels.take_profit_1 - 100.0)
