"""Ciclo do piloto automatico (`app.execution.autopilot`).

Exercita a orquestracao completa contra um cliente MT5 fake e um banco em
memoria: portoes de seguranca, leitura do mercado, escolha do operacional e
publicacao do status ao vivo. Nenhum terminal MetaTrader e necessario.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import SystemMode
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.system_setting_repository import get_current_mode, set_mode
from app.execution.automation_settings import TradingAutomationConfig
from app.execution.autopilot import run_autopilot_cycle
from app.execution.autopilot_status import AutopilotPhase
from app.execution.playbook import PlaybookKind
from app.mt5.account import AccountSnapshot
from app.mt5.market_data import RawCandle
from app.mt5.symbol_mapper import SymbolSpecification
from tests.fixtures.fake_mt5_client import FakeMT5Client, make_symbol_info
from tests.unit.execution.test_autopilot_status import publisher_for

SYMBOL = "EURUSD"
POINT = 0.0001
# Quarta-feira, 14:10 UTC — sobreposicao Londres/Nova York, horario nobre
# de EURUSD.
NOW = datetime(2026, 7, 22, 14, 10, tzinfo=UTC)
SATURDAY = datetime(2026, 7, 25, 12, tzinfo=UTC)

DEMO_ACCOUNT = AccountSnapshot(
    login=1,
    server="Test-Demo",
    balance=10_000.0,
    equity=10_000.0,
    margin=0.0,
    margin_free=10_000.0,
    currency="USD",
    leverage=100,
    trade_mode=0,
    is_demo=True,
)
REAL_ACCOUNT = replace(DEMO_ACCOUNT, is_demo=False, trade_mode=2)

SPEC = SymbolSpecification(
    name=SYMBOL,
    description="Euro vs Dollar",
    digits=5,
    point=POINT,
    volume_min=0.01,
    volume_max=100.0,
    volume_step=0.01,
    trade_contract_size=100_000.0,
    spread=2,
    trade_mode=4,
    visible=True,
)


def make_client() -> FakeMT5Client:
    client = FakeMT5Client()
    client.symbol_info_result = make_symbol_info(
        name=SYMBOL,
        digits=5,
        point=POINT,
        spread=2,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
    )
    return client


def seed_symbol(db_session) -> int:
    symbol = SymbolRepository(db_session).upsert_from_specification(SPEC)
    db_session.flush()
    return symbol.id


def seed_candles(
    db_session,
    symbol_id: int,
    timeframe: str,
    *,
    count: int,
    minutes: int,
    end: datetime,
    volume: int = 100,
    trend_step: float = 0.0,
) -> None:
    candles: list[RawCandle] = []
    price = 1.1000
    start = end - timedelta(minutes=minutes * count)
    for index in range(count):
        price += trend_step
        candles.append(
            RawCandle(
                open_time=(start + timedelta(minutes=minutes * index)).replace(tzinfo=None),
                open=price,
                high=price + 0.0006,
                low=price - 0.0006,
                close=price + 0.0002,
                tick_volume=volume,
                spread=2,
                real_volume=0,
            )
        )
    CandleRepository(db_session).bulk_upsert(symbol_id, timeframe, candles)
    db_session.flush()


def seed_full_market(db_session, *, end: datetime = NOW, volume: int = 100) -> int:
    symbol_id = seed_symbol(db_session)
    # Contexto: M15 com historico longo o bastante para o perfil horario.
    seed_candles(
        db_session, symbol_id, "M15", count=600, minutes=15, end=end, volume=volume,
        trend_step=0.00005,
    )
    for timeframe, minutes in (("M5", 5), ("M30", 30)):
        seed_candles(
            db_session, symbol_id, timeframe, count=400, minutes=minutes, end=end,
            volume=volume, trend_step=0.00005,
        )
    return symbol_id


@pytest.fixture(autouse=True)
def reset_mode(db_session):
    """A suite compartilha um banco em memoria e os testes de integracao
    comitam o modo do sistema. Sem este reset, o modo deixado por outro
    arquivo decidiria os portoes testados aqui."""
    if get_current_mode(db_session) != SystemMode.DISABLED:
        set_mode(db_session, SystemMode.DISABLED, reason="reset de teste")
        db_session.flush()


def enable_demo(db_session) -> None:
    for target in (
        SystemMode.DATA_ONLY,
        SystemMode.BACKTEST,
        SystemMode.REPLAY,
        SystemMode.PAPER,
        SystemMode.DEMO,
    ):
        set_mode(db_session, target, reason="test")
    db_session.flush()


def run(db_session, *, now=NOW, config=None, account=DEMO_ACCOUNT, symbols=(SYMBOL,)):
    publisher = publisher_for(db_session)
    result = run_autopilot_cycle(
        db_session,
        make_client(),
        config=config or TradingAutomationConfig(enabled=True, symbol=SYMBOL),
        account=account,
        publisher=publisher,
        available_symbols=list(symbols),
        now=now,
    )
    return result, publisher.load()


# --- Portoes de seguranca -------------------------------------------------


def test_blocks_when_system_mode_is_not_demo(db_session) -> None:
    seed_full_market(db_session)
    result, status = run(db_session)
    assert result.phase == AutopilotPhase.BLOCKED
    assert not result.ran
    assert "DEMO" in (result.blocking_error or "")
    assert status.phase == AutopilotPhase.BLOCKED.value


def test_blocks_on_real_account_even_in_demo_mode(db_session) -> None:
    enable_demo(db_session)
    seed_full_market(db_session)
    result, _ = run(db_session, account=REAL_ACCOUNT)
    assert result.phase == AutopilotPhase.BLOCKED
    assert "nao e demo" in (result.blocking_error or "")


def test_blocks_when_symbol_is_absent_from_broker(db_session) -> None:
    enable_demo(db_session)
    seed_full_market(db_session)
    result, _ = run(db_session, symbols=("GBPUSD",))
    assert result.phase == AutopilotPhase.BLOCKED
    assert "nao existe nesta corretora" in (result.blocking_error or "")


def test_waits_for_first_sync_without_reporting_an_error(db_session) -> None:
    """Sem dados ainda nao e falha do operador — nao vira `blocking_error`."""
    enable_demo(db_session)
    result, status = run(db_session)
    assert result.phase == AutopilotPhase.BLOCKED
    assert result.blocking_error is None
    # Pode faltar o simbolo inteiro ou so as candles, dependendo do que
    # outro teste ja comitou no banco compartilhado — os dois casos sao
    # "aguardando dados", nunca um erro para o operador resolver.
    assert "aguardando" in status.headline.lower()


# --- Leitura do mercado e escolha do operacional --------------------------


def test_stands_aside_on_the_weekend(db_session) -> None:
    enable_demo(db_session)
    seed_full_market(db_session, end=SATURDAY)
    result, status = run(db_session, now=SATURDAY)
    assert result.phase == AutopilotPhase.STANDING_ASIDE
    assert result.ran
    assert result.playbook is not None
    assert result.playbook.kind == PlaybookKind.STAND_ASIDE
    assert status.blockers
    assert result.blocking_error is None


def test_chooses_a_playbook_in_prime_session(db_session) -> None:
    enable_demo(db_session)
    seed_full_market(db_session)
    result, status = run(db_session)
    assert result.ran
    assert result.playbook is not None
    assert result.playbook.tradeable
    assert result.playbook.strategy_name is not None
    assert status.playbook_label
    assert status.timeframe in ("M5", "M15", "M30")
    assert status.session_rating == "PRIME"


def test_publishes_the_reasoning_as_activities(db_session) -> None:
    enable_demo(db_session)
    seed_full_market(db_session)
    _, status = run(db_session)
    messages = [activity.message for activity in status.activities]
    assert any("Lendo o mercado" in message for message in messages)
    assert any(
        "operacional" in message.lower() or "escolhendo" in message.lower()
        for message in messages
    )
    assert status.cycles == 1


def test_threshold_is_never_relaxed_below_configuration(db_session) -> None:
    enable_demo(db_session)
    seed_full_market(db_session)
    config = TradingAutomationConfig(enabled=True, symbol=SYMBOL, analysis_threshold=95.0)
    result, status = run(db_session, config=config)
    assert result.playbook is not None
    assert result.playbook.analysis_threshold >= 95.0
    assert status.analysis_threshold >= 95.0


def test_no_order_is_sent_without_full_confluence(db_session) -> None:
    """Com dados sinteticos a analise nunca alcanca o gate profissional —
    o ciclo termina sem NENHUMA ordem enviada ao fake."""
    enable_demo(db_session)
    seed_full_market(db_session)
    client = make_client()
    publisher = publisher_for(db_session)
    run_autopilot_cycle(
        db_session,
        client,
        config=TradingAutomationConfig(enabled=True, symbol=SYMBOL),
        account=DEMO_ACCOUNT,
        publisher=publisher,
        available_symbols=[SYMBOL],
        now=NOW,
    )
    assert client.order_send_calls == []


def test_status_reports_the_symbol_and_stays_fresh(db_session) -> None:
    enable_demo(db_session)
    seed_full_market(db_session)
    _, status = run(db_session)
    assert status.broker_symbol == SYMBOL
    assert status.enabled
    assert status.is_fresh()
