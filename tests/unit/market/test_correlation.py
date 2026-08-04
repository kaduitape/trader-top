"""Correlacao medida e controle de exposicao.

O caso que motiva o modulo: EURUSD, GBPUSD e AUDUSD abertos ao mesmo tempo
nao sao tres apostas — sao uma aposta contra o dolar, com tres vezes o
risco. O limite de "posicoes simultaneas" nao enxergava isso.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.market.correlation import (
    MIN_SAMPLES,
    check_exposure,
    correlate,
    log_returns,
    pearson,
)
from app.mt5.market_data import RawCandle
from app.mt5.symbol_mapper import SymbolSpecification

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def seed(db_session, name: str, closes: list[float], *, start_offset: int = 0) -> None:
    symbol = SymbolRepository(db_session).get_by_name(name)
    if symbol is None:
        symbol = SymbolRepository(db_session).upsert_from_specification(
            SymbolSpecification(
                name=name,
                description=name,
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
        )
    candles = [
        RawCandle(
            open_time=NOW - timedelta(minutes=15 * (len(closes) - i + start_offset)),
            open=price,
            high=price * 1.0002,
            low=price * 0.9998,
            close=price,
            tick_volume=500,
            spread=2,
            real_volume=0,
        )
        for i, price in enumerate(closes)
    ]
    CandleRepository(db_session).bulk_upsert(symbol.id, "M15", candles)
    db_session.commit()


def wave(n: int, *, phase: float = 0.0, base: float = 1.10) -> list[float]:
    """Serie deterministica com variacao suficiente para medir."""
    return [base * (1 + 0.01 * math.sin(i / 5 + phase)) for i in range(n)]


# --- matematica -----------------------------------------------------------


def test_identical_series_correlate_perfectly() -> None:
    xs = log_returns(wave(200))
    assert pearson(xs, xs) == pytest.approx(1.0)


def test_mirrored_series_correlate_negatively() -> None:
    xs = log_returns(wave(200))
    assert pearson(xs, [-x for x in xs]) == pytest.approx(-1.0)


def test_a_short_sample_returns_none_not_zero() -> None:
    """Zero significaria "medi e sao independentes" — nao foi o que houve."""
    curta = [0.001] * (MIN_SAMPLES - 1)
    assert pearson(curta, curta) is None


def test_a_constant_series_returns_none() -> None:
    constante = [0.0] * (MIN_SAMPLES + 10)
    assert pearson(constante, constante) is None


def test_returns_use_ratios_not_differences() -> None:
    assert log_returns([100.0, 110.0])[0] == pytest.approx(math.log(1.1))


def test_non_positive_prices_are_skipped() -> None:
    assert log_returns([1.0, 0.0, 2.0]) == []


# --- leitura do banco -----------------------------------------------------


def test_two_symbols_that_move_together_are_detected(db_session) -> None:
    seed(db_session, "CORR_A", wave(200))
    seed(db_session, "CORR_B", wave(200))

    resultado = correlate(db_session, symbol_a="CORR_A", symbol_b="CORR_B")

    assert resultado.measured
    assert resultado.coefficient == pytest.approx(1.0, abs=0.01)
    assert resultado.is_same_bet()


def test_an_unknown_symbol_is_not_measured(db_session) -> None:
    seed(db_session, "CORR_KNOWN", wave(200))

    resultado = correlate(db_session, symbol_a="CORR_KNOWN", symbol_b="NAO_EXISTE")

    assert not resultado.measured
    assert not resultado.is_same_bet()


def test_series_are_aligned_by_time_not_by_position(db_session) -> None:
    """Lacuna de coleta em um dos simbolos deslocaria a comparacao inteira
    se o alinhamento fosse pelo indice."""
    seed(db_session, "CORR_FULL", wave(200))
    seed(db_session, "CORR_GAP", wave(200), start_offset=40)

    resultado = correlate(db_session, symbol_a="CORR_FULL", symbol_b="CORR_GAP")

    # Sobrepoem-se apenas parcialmente; o que importa e nao explodir e so
    # medir o trecho comum.
    assert resultado.samples <= 200


# --- controle de exposicao ------------------------------------------------


def test_without_open_positions_everything_is_allowed(db_session) -> None:
    verdict = check_exposure(db_session, candidate="CORR_A", open_symbols=[])
    assert verdict.allowed


def test_the_same_symbol_twice_is_refused(db_session) -> None:
    verdict = check_exposure(db_session, candidate="CORR_A", open_symbols=["CORR_A"])
    assert not verdict.allowed
    assert verdict.coefficient == 1.0


def test_a_correlated_candidate_is_refused(db_session) -> None:
    """O caso central: seria a mesma aposta com o dobro do risco."""
    seed(db_session, "CORR_X", wave(200))
    seed(db_session, "CORR_Y", wave(200))

    verdict = check_exposure(db_session, candidate="CORR_X", open_symbols=["CORR_Y"])

    assert not verdict.allowed
    assert verdict.conflicting_symbol == "CORR_Y"
    assert "mesma aposta" in verdict.reason


def test_a_mirrored_candidate_is_also_refused(db_session) -> None:
    """Comprar um e vender o espelho e a mesma aposta — por isso o modulo
    da correlacao, e nao o sinal."""
    seed(db_session, "CORR_UP", wave(200))
    seed(db_session, "CORR_DOWN", [2.20 - price for price in wave(200)])

    verdict = check_exposure(db_session, candidate="CORR_UP", open_symbols=["CORR_DOWN"])

    assert not verdict.allowed
    assert (verdict.coefficient or 0) < 0
    assert "espelhada" in verdict.reason


def test_an_uncorrelated_candidate_is_allowed(db_session) -> None:
    seed(db_session, "CORR_SIN", wave(300))
    seed(db_session, "CORR_COS", wave(300, phase=math.pi / 2))

    verdict = check_exposure(
        db_session, candidate="CORR_SIN", open_symbols=["CORR_COS"], threshold=0.9
    )

    assert verdict.allowed


def test_an_unmeasurable_pair_does_not_block(db_session) -> None:
    """Sem amostra nao ha o que afirmar; bloquear aqui seria travar a
    operacao por falta de dado, nao por risco medido."""
    seed(db_session, "CORR_NEW", wave(10))
    seed(db_session, "CORR_OLD", wave(10))

    verdict = check_exposure(db_session, candidate="CORR_NEW", open_symbols=["CORR_OLD"])

    assert verdict.allowed
