"""Qual par o robo opera neste ciclo.

O radar existia e nao mandava em nada: o operador escolhia EURUSD e o robo
passava o dia nele, mesmo com XAUUSD pontuando muito melhor. Estes testes
cobrem a ligacao entre os dois — e principalmente as duas regras que
protegem dinheiro: posicao aberta congela a escolha, e trocar exige margem.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.execution.order_state import OrderState
from app.execution.symbol_selection import (
    SOURCE_FIXED,
    SOURCE_RADAR,
    SWITCH_MARGIN,
    choose_symbol,
)
from app.market.scanner import ScanCandidate, ScanResult
from app.mt5.symbol_mapper import SymbolSpecification

NOW = datetime(2026, 8, 5, 14, 0, tzinfo=UTC)
ESTRATEGIA = "autopilot"


def _seed(db_session, *nomes: str) -> None:
    for nome in nomes:
        SymbolRepository(db_session).upsert_from_specification(
            SymbolSpecification(
                name=nome, description=nome, digits=5, point=0.00001,
                volume_min=0.01, volume_max=100.0, volume_step=0.01,
                trade_contract_size=100_000.0, spread=2, trade_mode=4, visible=True,
            )
        )
    db_session.commit()


def _candidato(symbol: str, score: float, *, blocked: str | None = None) -> ScanCandidate:
    return ScanCandidate(
        symbol=symbol, score=score, session_score=100.0, volume_score=75.0,
        cost_score=90.0, session_label="Melhor horario", volume_label="Normal",
        spread_points=8.0, atr_points=200.0, spread_atr_ratio=0.04,
        blocked_reason=blocked,
    )


def _scan(*candidatos: ScanCandidate) -> ScanResult:
    return ScanResult(generated_at=NOW, candidates=tuple(candidatos))


def _escolher(db_session, *, configurado: str, fonte: str, scan, disponiveis=None):
    return choose_symbol(
        db_session,
        configured_symbol=configurado,
        source=fonte,
        available_symbols=disponiveis if disponiveis is not None else ["EURUSD", "XAUUSD", "GBPUSD"],
        strategy_name=ESTRATEGIA,
        now=NOW,
        scan_result=scan,
    )


# --- modo fixo -------------------------------------------------------------


def test_the_fixed_mode_ignores_the_radar(db_session) -> None:
    """Quem escolheu um par quer aquele par."""
    _seed(db_session, "EURUSD", "XAUUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_FIXED,
        scan=_scan(_candidato("XAUUSD", 95.0)),
    )

    assert escolha.symbol == "EURUSD"
    assert escolha.from_radar is False


# --- modo radar ------------------------------------------------------------


def test_the_radar_switches_to_the_better_pair(db_session) -> None:
    _seed(db_session, "EURUSD", "XAUUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(_candidato("XAUUSD", 92.0), _candidato("EURUSD", 60.0)),
    )

    assert escolha.symbol == "XAUUSD"
    assert escolha.from_radar is True


def test_it_falls_to_the_next_when_the_first_is_not_available_here(db_session) -> None:
    """"Se nao conseguir, fica monitorando as proximas": o primeiro colocado
    pode simplesmente nao existir nesta corretora."""
    _seed(db_session, "EURUSD", "GBPUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(_candidato("XAUUSD", 95.0), _candidato("GBPUSD", 88.0)),
        disponiveis=["EURUSD", "GBPUSD"],
    )

    assert escolha.symbol == "GBPUSD"


def test_a_blocked_candidate_is_never_chosen(db_session) -> None:
    """`top()` ja filtra bloqueados; este teste garante que a ligacao nao
    reintroduziu o problema pegando `candidates` cru."""
    _seed(db_session, "EURUSD", "XAUUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(
            _candidato("XAUUSD", 99.0, blocked="Evento de alto impacto"),
            _candidato("EURUSD", 70.0),
        ),
    )

    assert escolha.symbol == "EURUSD"


def test_with_no_candidate_it_keeps_the_configured_pair(db_session) -> None:
    """Radar vazio nao pode virar "sem par": o robo segue monitorando."""
    _seed(db_session, "EURUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(_candidato("XAUUSD", 99.0, blocked="Fechado")),
    )

    assert escolha.symbol == "EURUSD"
    assert escolha.from_radar is False
    assert "monitorando" in escolha.reason


def test_the_queue_is_reported_for_the_panel(db_session) -> None:
    _seed(db_session, "EURUSD", "XAUUSD", "GBPUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(
            _candidato("XAUUSD", 92.0),
            _candidato("GBPUSD", 85.0),
            _candidato("EURUSD", 70.0),
        ),
    )

    assert escolha.considered == ("XAUUSD", "GBPUSD", "EURUSD")


# --- histerese -------------------------------------------------------------


def test_a_narrow_lead_does_not_justify_switching(db_session) -> None:
    """Sem isso, dois pares empatados fariam o robo alternar a cada ciclo e
    nunca acompanhar nenhum tempo suficiente para operar."""
    _seed(db_session, "EURUSD", "XAUUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(_candidato("XAUUSD", 82.0), _candidato("EURUSD", 80.0)),
    )

    assert escolha.symbol == "EURUSD"
    assert "nao compensa trocar" in escolha.reason


def test_a_clear_lead_does_justify_switching(db_session) -> None:
    _seed(db_session, "EURUSD", "XAUUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(
            _candidato("XAUUSD", 80.0 + SWITCH_MARGIN + 1),
            _candidato("EURUSD", 80.0),
        ),
    )

    assert escolha.symbol == "XAUUSD"


def test_the_margin_does_not_apply_when_the_current_pair_is_out_of_the_queue(
    db_session,
) -> None:
    """Se o par atual nem esta operavel, nao ha o que preservar."""
    _seed(db_session, "EURUSD", "XAUUSD")

    escolha = _escolher(
        db_session, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(
            _candidato("XAUUSD", 71.0),
            _candidato("EURUSD", 70.0, blocked="Sem candles"),
        ),
    )

    assert escolha.symbol == "XAUUSD"


# --- posicao aberta --------------------------------------------------------


@pytest.fixture
def com_posicao_aberta(db_session):
    _seed(db_session, "EURUSD", "XAUUSD")
    symbol = SymbolRepository(db_session).get_by_name("EURUSD")
    LiveTradeRepository(db_session).create(
        symbol_id=symbol.id,
        timeframe="M15",
        strategy_name=ESTRATEGIA,
        direction="LONG",
        signal_time=NOW,
        entry_price=1.10,
        stop_loss=1.09,
        take_profit=1.12,
        volume=0.01,
        model_version=None,
        signal_id="sig-selecao-1",
        order_state=OrderState.POSITION_OPEN,
    )
    db_session.commit()
    yield db_session
    # A suite compartilha o banco: uma operacao aberta deixada aqui apareceria
    # na listagem de outro arquivo de teste.
    from sqlalchemy import delete

    from app.database.models.live_trade import LiveTrade

    db_session.execute(delete(LiveTrade).where(LiveTrade.symbol_id == symbol.id))
    db_session.commit()


def test_an_open_position_freezes_the_choice(com_posicao_aberta) -> None:
    """Trocar de par com posicao aberta abandonaria o trailing e o
    break-even dela no meio do caminho."""
    escolha = _escolher(
        com_posicao_aberta, configurado="EURUSD", fonte=SOURCE_RADAR,
        scan=_scan(_candidato("XAUUSD", 99.0), _candidato("EURUSD", 40.0)),
    )

    assert escolha.symbol == "EURUSD"
    assert "Posicao aberta" in escolha.reason
