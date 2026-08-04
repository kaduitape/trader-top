"""Varredura de oportunidades entre instrumentos.

O que estes testes protegem: que a comparacao seja NORMALIZADA (cada ativo
contra ele mesmo) e que o CUSTO entre na nota. Sem a primeira, o ranking so
diria qual ativo tem numeros maiores; sem a segunda, ele elegeria o par mais
caro de operar sempre que o sinal parecesse bom.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.calendar_feed.provider import CalendarEvent, CalendarSnapshot, CalendarStatus
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.market.scanner import _cost_score, evaluate_candidate, scan_market
from app.mt5.market_data import RawCandle
from app.mt5.symbol_mapper import SymbolSpecification

# Terca-feira, 14:00 UTC: Londres e Nova York abertas (sobreposicao).
NOW = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)


def seed(
    db_session,
    name: str,
    *,
    volume: int = 1000,
    spread: int = 8,
    point: float = 0.00001,
    bars: int = 300,
    ultimo_volume: int | None = None,
) -> None:
    symbol = SymbolRepository(db_session).get_by_name(name)
    if symbol is None:
        symbol = SymbolRepository(db_session).upsert_from_specification(
            SymbolSpecification(
                name=name,
                description=name,
                digits=5,
                point=point,
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
                trade_contract_size=100_000.0,
                spread=spread,
                trade_mode=4,
                visible=True,
            )
        )
    preco = 1.10
    candles = []
    for i in range(bars):
        preco *= 1.0001
        e_ultima = i == bars - 1
        candles.append(
            RawCandle(
                open_time=NOW - timedelta(minutes=15 * (bars - i)),
                open=preco,
                high=preco * 1.0006,
                low=preco * 0.9994,
                close=preco * 1.0002,
                tick_volume=(ultimo_volume if e_ultima and ultimo_volume else volume),
                spread=spread,
                real_volume=0,
            )
        )
    CandleRepository(db_session).bulk_upsert(symbol.id, "M15", candles)
    db_session.commit()


# --- nota de custo --------------------------------------------------------


def test_a_tight_spread_scores_full() -> None:
    nota, razao = _cost_score(spread_points=1.0, atr_points=100.0)
    assert nota == 100.0
    assert razao == pytest.approx(0.01)


def test_a_spread_worth_half_the_atr_scores_zero() -> None:
    """Pagar meio ATR so para entrar inviabiliza qualquer alvo realista."""
    nota, _ = _cost_score(spread_points=50.0, atr_points=100.0)
    assert nota == 0.0


def test_cost_degrades_gradually_in_between() -> None:
    baixo, _ = _cost_score(spread_points=10.0, atr_points=100.0)
    alto, _ = _cost_score(spread_points=30.0, atr_points=100.0)
    assert 0.0 < alto < baixo < 100.0


def test_an_unmeasurable_cost_is_neutral_not_zero() -> None:
    """Zerar puniria o ativo por uma lacuna de dado nossa."""
    nota, razao = _cost_score(spread_points=None, atr_points=None)
    assert nota == 50.0
    assert razao is None


# --- avaliacao individual -------------------------------------------------


def test_a_symbol_in_its_prime_session_scores_well(db_session) -> None:
    seed(db_session, "EURUSD")

    candidato = evaluate_candidate(db_session, symbol="EURUSD", now=NOW)

    assert candidato.tradable
    assert candidato.session_score >= 75.0
    assert candidato.score > 50.0


def test_a_symbol_without_candles_is_blocked(db_session) -> None:
    SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name="SEMDADOS",
            description="",
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
    db_session.commit()

    candidato = evaluate_candidate(db_session, symbol="SEMDADOS", now=NOW)

    assert not candidato.tradable
    assert "Sem candles" in (candidato.blocked_reason or "")


def test_the_weekend_blocks_every_symbol(db_session) -> None:
    seed(db_session, "GBPUSD")
    domingo = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)

    candidato = evaluate_candidate(db_session, symbol="GBPUSD", now=domingo)

    assert not candidato.tradable
    assert "fechado" in (candidato.blocked_reason or "").lower()


def test_a_scheduled_event_blocks_the_candidate(db_session) -> None:
    seed(db_session, "USDCHF")
    calendario = CalendarSnapshot(
        status=CalendarStatus.OK,
        events=[
            CalendarEvent(
                title="FOMC",
                scheduled_at=NOW + timedelta(minutes=10),
                currency="USD",
                impact="HIGH",
            )
        ],
    )

    candidato = evaluate_candidate(
        db_session, symbol="USDCHF", now=NOW, calendar=calendario
    )

    assert not candidato.tradable
    assert "FOMC" in (candidato.blocked_reason or "")


def test_an_event_of_another_currency_does_not_block(db_session) -> None:
    seed(db_session, "AUDNZD")
    calendario = CalendarSnapshot(
        status=CalendarStatus.OK,
        events=[
            CalendarEvent(
                title="FOMC",
                scheduled_at=NOW + timedelta(minutes=10),
                currency="USD",
                impact="HIGH",
            )
        ],
    )

    candidato = evaluate_candidate(
        db_session, symbol="AUDNZD", now=NOW, calendar=calendario
    )

    assert candidato.tradable


# --- ranking --------------------------------------------------------------


def test_the_cheaper_symbol_wins_when_everything_else_ties(db_session) -> None:
    """O criterio que quase todo scanner ignora: o custo e certo, o sinal e
    hipotese."""
    seed(db_session, "EURJPY", spread=2)
    seed(db_session, "EURCAD", spread=60)

    resultado = scan_market(
        db_session, now=NOW, symbols=["EURCAD", "EURJPY"], timeframe="M15"
    )

    assert resultado.best is not None
    assert resultado.best.symbol == "EURJPY"


def test_blocked_candidates_go_to_the_end_not_out_of_the_list(db_session) -> None:
    """O painel precisa poder mostrar por que um par que parecia bom nao
    entrou."""
    seed(db_session, "EURGBP")
    SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name="BLOQUEADO",
            description="",
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
    db_session.commit()

    resultado = scan_market(
        db_session, now=NOW, symbols=["BLOQUEADO", "EURGBP"], timeframe="M15"
    )

    assert [c.symbol for c in resultado.candidates] == ["EURGBP", "BLOQUEADO"]
    assert resultado.best is not None
    assert resultado.best.symbol == "EURGBP"


def test_top_returns_only_tradable_candidates(db_session) -> None:
    seed(db_session, "NZDUSD")
    resultado = scan_market(db_session, now=NOW, symbols=["NZDUSD"], timeframe="M15")

    assert all(candidato.tradable for candidato in resultado.top(5))


def test_with_no_tradable_candidate_best_is_none(db_session) -> None:
    seed(db_session, "USDCAD")
    domingo = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)

    resultado = scan_market(
        db_session, now=domingo, symbols=["USDCAD"], timeframe="M15"
    )

    assert resultado.best is None


def test_the_reasons_explain_the_score(db_session) -> None:
    seed(db_session, "AUDUSD")

    candidato = evaluate_candidate(db_session, symbol="AUDUSD", now=NOW)

    assert candidato.reasons
    assert any("Volume" in razao for razao in candidato.reasons)
