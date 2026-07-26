"""Piloto automatico: escolha a moeda, o robo decide o resto.

Um ciclo completo, na ordem em que um operador humano pensaria — e cada
etapa e publicada no status ao vivo (`app.execution.autopilot_status`)
ANTES de comecar, para que o operador veja o raciocinio acontecendo, nao
so o resultado:

1. **Portoes de seguranca** — modo `DEMO`, conta demo, simbolo existente na
   corretora, dados sincronizados. Qualquer falha vira `BLOCKED` com o
   motivo exato, nunca um silencio.
2. **Leitura do mercado** — sessao de negociacao do par
   (`app.market.sessions`), volume relativo a mesma hora
   (`app.market.volume_profile`) e regime vigente (`app.market.regimes`).
3. **Escolha do operacional** — `app.execution.playbook` elege a estrategia,
   o timeframe de execucao, o score minimo e o multiplicador de risco.
   `STAND_ASIDE` encerra o ciclo sem enviar nada.
4. **Analise e execucao** — o motor de analise da (ou nega) permissao, o
   gatilho do operacional da o timing (`PlaybookConfluenceStrategy`), o
   motor de risco tem poder de veto e so entao a ordem vai para a conta
   demo, pelo mesmo `DemoExecutionEngine` de sempre.

Nada aqui contorna uma camada existente: o piloto automatico so ESCOLHE os
parametros com que as camadas ja existentes rodam. Conta real permanece
bloqueada incondicionalmente por `app.mt5.orders.send_market_order`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.apexflow.config import load_apexflow_config
from app.apexflow.engine import analyze as apexflow_analyze
from app.apexflow.journal import record_decision
from app.apexflow.strategy import ApexFlowStrategy
from app.core.config import Settings, get_settings
from app.core.enums import SystemMode
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.system_setting_repository import get_current_mode
from app.execution.automation_settings import ENGINE_APEXFLOW, TradingAutomationConfig
from app.execution.autopilot_status import (
    ActivityLevel,
    AutopilotPhase,
    AutopilotStatusPublisher,
)
from app.execution.autopilot_strategy import (
    AUTOPILOT_STRATEGY_NAME,
    PlaybookConfluenceStrategy,
)
from app.execution.engine import (
    DemoExecutionEngine,
    DemoExecutionEvent,
    OrderRejectedByBroker,
    PositionClosed,
    PositionOpened,
    PositionReconciling,
    SignalRejected,
)
from app.execution.playbook import PlaybookDecision, select_playbook
from app.market.catalog import resolve_broker_symbol
from app.market.features import build_candle_features, required_lookback_bars
from app.market.regimes import MarketRegime, classify_latest_regime
from app.market.sessions import evaluate_symbol_session
from app.market.volume_profile import VolumeLevel, read_current_volume
from app.mt5.account import AccountSnapshot
from app.mt5.client import MT5ClientProtocol
from app.mt5.market_data import TIMEFRAME_SECONDS, Timeframe
from app.mt5.symbol_mapper import fetch_symbol_specification
from app.risk.config import RiskLimits
from app.services.analysis_service import analyze_symbol
from app.strategies.registry import create_strategy

logger = logging.getLogger(__name__)

CONTEXT_TIMEFRAME = Timeframe.M15
"""Timeframe de LEITURA de contexto (sessao/volume/regime), fixo.

Fixo de proposito: o perfil de volume por hora so e comparavel consigo
mesmo. Se a leitura mudasse de timeframe junto com a execucao, o "volume
desta hora" passaria a ser medido em outra unidade a cada ciclo e a
comparacao historica perderia sentido. O timeframe de EXECUCAO continua
sendo escolhido pelo seletor de operacional."""

CONTEXT_PROFILE_BARS = 1_500
"""~2 semanas de M15 — historico suficiente para varias amostras de cada
hora do dia sem carregar a serie inteira a cada ciclo."""

EXECUTION_TIMEFRAMES: tuple[str, ...] = ("M5", "M15", "M30")

APEXFLOW_ENTRY_TIMEFRAMES: tuple[str, ...] = ("M5", "M15", "M1")
"""Timeframes de entrada aceitos pelo ApexFlow, em ordem de preferencia.
H1 esta fora por arquitetura (`app.apexflow.mtf.ENTRY_TIMEFRAMES`)."""


@dataclass(frozen=True, slots=True)
class AutopilotCycleResult:
    """O que aconteceu no ciclo. `blocking_error` so e preenchido quando
    existe algo que o OPERADOR precisa resolver (modo errado, conta real,
    simbolo ausente) — ausencia de sinal e ficar de fora nunca sao erro."""

    ran: bool
    phase: AutopilotPhase
    message: str
    playbook: PlaybookDecision | None = None
    events: tuple[DemoExecutionEvent, ...] = ()
    blocking_error: str | None = None


def _describe_event(event: DemoExecutionEvent) -> tuple[str, ActivityLevel]:
    if isinstance(event, SignalRejected):
        return (f"Risco vetou a entrada: {event.reason}", ActivityLevel.WARN)
    if isinstance(event, OrderRejectedByBroker):
        return (f"Corretora recusou a ordem: {event.reason}", ActivityLevel.ERROR)
    if isinstance(event, PositionOpened):
        return (
            f"Operacao aberta: {event.direction.value} {event.volume} lote(s) a "
            f"{event.entry_price:.5f} (ticket {event.mt5_position_ticket}).",
            ActivityLevel.GOOD,
        )
    if isinstance(event, PositionClosed):
        return (
            f"Operacao encerrada pela corretora a {event.exit_price:.5f}; "
            f"resultado {event.net_pnl:+.2f}.",
            ActivityLevel.GOOD if event.net_pnl >= 0 else ActivityLevel.WARN,
        )
    if isinstance(event, PositionReconciling):
        return (
            "A corretora nao reporta mais a posicao e nenhum negocio de "
            "fechamento foi encontrado — revisao manual necessaria.",
            ActivityLevel.ERROR,
        )
    return (str(event), ActivityLevel.INFO)


def _daily_counters(session: Session, symbol_id: int) -> tuple[int, float]:
    repository = LiveTradeRepository(session)
    start_of_day = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    trades = repository.count_entries_since(
        symbol_id, None, AUTOPILOT_STRATEGY_NAME, since=start_of_day
    )
    pnl = repository.sum_net_pnl_since(
        symbol_id, None, AUTOPILOT_STRATEGY_NAME, since=start_of_day
    )
    return trades, pnl


def _open_position_summary(session: Session, symbol_id: int) -> str:
    trade = LiveTradeRepository(session).get_active_position(
        symbol_id, None, AUTOPILOT_STRATEGY_NAME
    )
    if trade is None:
        return ""
    entry = f"{float(trade.entry_price):.5f}" if trade.entry_price is not None else "?"
    return (
        f"{trade.direction} {float(trade.volume or 0):g} lote(s) a {entry} "
        f"({trade.timeframe}, ticket {trade.mt5_position_ticket or '-'})"
    )


def _classify_regime(candles: list) -> MarketRegime | None:
    if len(candles) < required_lookback_bars():
        return None
    try:
        features = build_candle_features(candles)
        return classify_latest_regime(features)
    except ValueError:
        return None


def _blocked(
    publisher: AutopilotStatusPublisher,
    message: str,
    *,
    blocking_error: str | None,
    level: ActivityLevel = ActivityLevel.WARN,
    **fields: object,
) -> AutopilotCycleResult:
    publisher.publish(
        AutopilotPhase.BLOCKED,
        message,
        level=level,
        enabled=True,
        **fields,
    )
    return AutopilotCycleResult(
        ran=False,
        phase=AutopilotPhase.BLOCKED,
        message=message,
        blocking_error=blocking_error,
    )


def _run_apexflow(
    session: Session,
    client: MT5ClientProtocol,
    *,
    config: TradingAutomationConfig,
    account: AccountSnapshot,
    publisher: AutopilotStatusPublisher,
    symbol_row,
    broker_symbol: str,
    spec,
    session_state,
    volume,
    market_fields: dict,
    available_timeframes: tuple[str, ...],
    settings: Settings,
    now: datetime,
) -> AutopilotCycleResult:
    """Ciclo com o ApexFlow AI no comando da decisao.

    O motor decide COMPRAR/VENDER/NAO OPERAR; toda decisao (inclusive as de
    nao operar, que sao a maioria) e registrada no Learning Engine antes de
    qualquer envio de ordem. Os portoes de risco e a execucao continuam
    sendo exatamente os mesmos do caminho por playbook.
    """
    apexflow_config = load_apexflow_config(session)
    timeframe = _apexflow_timeframe(config.timeframe, available_timeframes)
    bar_seconds = TIMEFRAME_SECONDS[timeframe]

    publisher.publish(
        AutopilotPhase.ANALYZING,
        f"ApexFlow AI lendo o fluxo de {broker_symbol} em {timeframe.value}.",
        enabled=True,
        timeframe=timeframe.value,
        playbook_kind="APEXFLOW",
        playbook_label="ApexFlow AI",
        playbook_description=(
            "Motor de fluxo de ticks, microestrutura e price action: decide "
            "comprar, vender ou nao operar."
        ),
        playbook_icon="bi-cpu",
        analysis_threshold=apexflow_config.min_confidence * 100,
        **market_fields,
    )

    analysis = apexflow_analyze(
        session,
        symbol=broker_symbol,
        timeframe=timeframe,
        config=apexflow_config,
        point=float(symbol_row.point),
        now=now,
    )
    decision = analysis.decision

    record_decision(
        session,
        decision,
        analysis.vector,
        symbol_id=symbol_row.id,
        timeframe=timeframe.value,
        context=analysis.context,
        session_state=session_state,
        volume=volume,
    )

    apexflow_fields = {
        "timeframe": timeframe.value,
        "playbook_kind": "APEXFLOW",
        "playbook_label": f"ApexFlow AI — {decision.label}",
        "playbook_description": analysis.context.label,
        "playbook_icon": "bi-cpu",
        "analysis_score": round(decision.confidence * 100, 1),
        "analysis_threshold": round(apexflow_config.min_confidence * 100, 1),
        "analysis_recommendation": decision.action.value,
        "fit_score": round(analysis.vector.completeness * 100, 1),
        "risk_factor": 1.0,
        "reasons": tuple(decision.reasons),
        "blockers": tuple(decision.vetoes),
    }

    if not decision.is_entry:
        headline = (
            f"ApexFlow AI: NAO OPERAR ({decision.probability_abstain * 100:.0f}% de "
            f"abstencao). {decision.vetoes[0] if decision.vetoes else ''}"
        ).strip()
        publisher.publish(
            AutopilotPhase.STANDING_ASIDE,
            headline,
            detail=(
                f"Compra {decision.probability_buy * 100:.1f}% / venda "
                f"{decision.probability_sell * 100:.1f}% — minimo para operar "
                f"{apexflow_config.min_confidence * 100:.0f}%."
            ),
            enabled=True,
            **market_fields,
            **apexflow_fields,
        )
        return AutopilotCycleResult(
            ran=True,
            phase=AutopilotPhase.STANDING_ASIDE,
            message=headline,
        )

    execution_candles = CandleRepository(session).get_recent(
        symbol_row.id, timeframe.value, required_lookback_bars() + 5
    )
    if len(execution_candles) < 2:
        return _blocked(
            publisher,
            f"Aguardando candles de {broker_symbol}/{timeframe.value}.",
            blocking_error=None,
            **market_fields,
            **apexflow_fields,
        )

    strategy = ApexFlowStrategy(
        analysis,
        expected_open_time=execution_candles[-1].open_time,
        point=float(symbol_row.point),
        config=apexflow_config,
    )
    limits = RiskLimits(
        risk_per_trade_pct=config.risk_per_trade_pct,
        max_daily_loss_pct=config.max_daily_loss_pct,
        max_consecutive_losses=config.max_consecutive_losses,
        max_simultaneous_positions=config.max_simultaneous_positions,
        max_trades_per_day=config.max_trades_per_day,
        min_seconds_between_trades=config.min_seconds_between_trades,
        max_spread_points=min(config.max_spread_points, apexflow_config.max_spread_points),
        max_feed_delay_seconds=max(
            float(settings.quality_max_feed_delay_seconds), float(bar_seconds * 2)
        ),
    )
    engine = DemoExecutionEngine(
        session,
        client,
        strategy,
        symbol=broker_symbol,
        symbol_id=symbol_row.id,
        timeframe=timeframe.value,
        point=float(symbol_row.point),
        account=account,
        symbol_spec=spec,
        risk_limits=limits,
        magic=0,
        model_version=decision.model_version,
        clock=lambda: now,
        scope_across_timeframes=True,
    )
    result = engine.step(execution_candles)

    for event in result.events:
        message, level = _describe_event(event)
        publisher.note(message, level=level)

    trades_today, pnl_today = _daily_counters(session, symbol_row.id)
    open_position = _open_position_summary(session, symbol_row.id)
    opened = any(isinstance(event, PositionOpened) for event in result.events)
    phase = AutopilotPhase.POSITION_OPEN if (opened or open_position) else (
        AutopilotPhase.WAITING_TRIGGER
    )
    headline = (
        f"ApexFlow AI {decision.label} com {decision.confidence * 100:.1f}% de "
        f"confianca em {broker_symbol}."
    )

    publisher.publish(
        phase,
        headline,
        enabled=True,
        trades_today=trades_today,
        pnl_today=pnl_today,
        open_position=open_position,
        last_cycle_at=now.isoformat(),
        cycles=publisher.load().cycles + 1,
        last_error="",
        **market_fields,
        **apexflow_fields,
    )
    return AutopilotCycleResult(
        ran=True, phase=phase, message=headline, events=tuple(result.events)
    )


def _apexflow_timeframe(
    configured: str, available: tuple[str, ...]
) -> Timeframe:
    """Timeframe de EXECUCAO do ApexFlow.

    O motor recusa timeframes de contexto por arquitetura (`H1` fornece
    direcao macro, nunca entrada), entao um `configured` fora da lista de
    entrada cai para o padrao em vez de derrubar o ciclo.
    """
    candidates = [
        code for code in (configured, *available) if code in APEXFLOW_ENTRY_TIMEFRAMES
    ]
    return Timeframe(candidates[0] if candidates else "M5")


def run_autopilot_cycle(
    session: Session,
    client: MT5ClientProtocol,
    *,
    config: TradingAutomationConfig,
    account: AccountSnapshot,
    publisher: AutopilotStatusPublisher,
    available_symbols: list[str],
    available_timeframes: tuple[str, ...] = EXECUTION_TIMEFRAMES,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> AutopilotCycleResult:
    """Roda um ciclo completo do piloto automatico para `config.symbol`.

    Nunca levanta excecao por condicao de mercado — so por falha genuina de
    infraestrutura, que o chamador (worker/CLI) reporta. O `session` NAO e
    comitado aqui: quem chama decide a fronteira da transacao.
    """
    resolved_settings = settings or get_settings()
    resolved_now = (now or datetime.now(UTC)).astimezone(UTC)

    if get_current_mode(session) != SystemMode.DEMO:
        return _blocked(
            publisher,
            "Pausado: o modo operacional precisa estar em DEMO.",
            blocking_error="Automacao pausada: o modo operacional nao esta em DEMO.",
        )
    if not account.is_demo:
        return _blocked(
            publisher,
            "Bloqueado: a conta conectada nao e demo — nenhuma ordem sera enviada.",
            blocking_error="Automacao bloqueada: a conta MT5 conectada nao e demo.",
            level=ActivityLevel.ERROR,
        )

    broker_symbol = resolve_broker_symbol(config.symbol, available_symbols)
    if broker_symbol is None:
        return _blocked(
            publisher,
            f"Bloqueado: {config.symbol} nao existe nesta corretora.",
            blocking_error=f"Automacao pausada: {config.symbol} nao existe nesta corretora.",
            symbol=config.symbol,
        )

    symbol = SymbolRepository(session).get_by_name(broker_symbol)
    spec = fetch_symbol_specification(client, broker_symbol)
    if symbol is None or spec is None:
        return _blocked(
            publisher,
            f"Aguardando a primeira sincronizacao de {broker_symbol}.",
            blocking_error=None,
            symbol=config.symbol,
            broker_symbol=broker_symbol,
        )

    candle_repository = CandleRepository(session)
    trades_today, pnl_today = _daily_counters(session, symbol.id)
    open_position = _open_position_summary(session, symbol.id)

    publisher.publish(
        AutopilotPhase.READING_MARKET,
        f"Lendo o mercado de {broker_symbol}: sessao, volume e regime.",
        enabled=True,
        symbol=config.symbol,
        broker_symbol=broker_symbol,
        trades_today=trades_today,
        pnl_today=pnl_today,
        open_position=open_position,
        last_error="",
    )

    session_state = evaluate_symbol_session(broker_symbol, now=resolved_now)
    context_candles = candle_repository.get_recent(
        symbol.id, CONTEXT_TIMEFRAME.value, CONTEXT_PROFILE_BARS
    )
    volume = read_current_volume(context_candles, now=resolved_now)
    regime = _classify_regime(context_candles)

    market_fields = {
        "session_rating": session_state.rating.value,
        "session_label": session_state.label,
        "active_sessions": ", ".join(session_state.active_labels),
        "volume_level": volume.level.value,
        "volume_label": volume.label,
        "volume_ratio": round(volume.ratio, 2) if volume.ratio is not None else None,
        "trend": regime.trend.value if regime is not None else "SEM_DADOS",
        "volatility": regime.volatility.value if regime is not None else "SEM_DADOS",
    }

    if regime is None and volume.level == VolumeLevel.UNKNOWN and not context_candles:
        return _blocked(
            publisher,
            f"Aguardando candles de {broker_symbol}/{CONTEXT_TIMEFRAME.value} "
            "para conseguir ler o mercado.",
            blocking_error=None,
            symbol=config.symbol,
            broker_symbol=broker_symbol,
            **market_fields,
        )

    if config.engine == ENGINE_APEXFLOW:
        return _run_apexflow(
            session,
            client,
            config=config,
            account=account,
            publisher=publisher,
            symbol_row=symbol,
            broker_symbol=broker_symbol,
            spec=spec,
            session_state=session_state,
            volume=volume,
            market_fields=market_fields,
            available_timeframes=available_timeframes,
            settings=resolved_settings,
            now=resolved_now,
        )

    publisher.publish(
        AutopilotPhase.CHOOSING_PLAYBOOK,
        f"{session_state.headline} Volume {volume.label.lower()}. "
        "Escolhendo o melhor operacional.",
        enabled=True,
        **market_fields,
    )

    usable_timeframes = tuple(
        code for code in available_timeframes if code in EXECUTION_TIMEFRAMES
    ) or EXECUTION_TIMEFRAMES
    playbook = select_playbook(
        session=session_state,
        volume=volume,
        regime=regime,
        base_threshold=config.analysis_threshold,
        available_timeframes=usable_timeframes,
    )

    playbook_fields = {
        "playbook_kind": playbook.kind.value,
        "playbook_label": playbook.label,
        "playbook_description": playbook.description,
        "playbook_icon": playbook.icon,
        "timeframe": playbook.timeframe,
        "analysis_threshold": playbook.analysis_threshold,
        "risk_factor": playbook.risk_factor,
        "fit_score": playbook.fit_score,
        "reasons": tuple(playbook.reasons),
        "blockers": tuple(playbook.blockers),
    }

    if not playbook.tradeable:
        publisher.publish(
            AutopilotPhase.STANDING_ASIDE,
            playbook.headline,
            detail="Nenhuma ordem sera enviada enquanto a condicao nao mudar.",
            level=ActivityLevel.INFO,
            enabled=True,
            analysis_score=None,
            analysis_recommendation="",
            **market_fields,
            **playbook_fields,
        )
        return AutopilotCycleResult(
            ran=True,
            phase=AutopilotPhase.STANDING_ASIDE,
            message=playbook.headline,
            playbook=playbook,
        )

    execution_timeframe = Timeframe(playbook.timeframe)
    bar_seconds = TIMEFRAME_SECONDS[execution_timeframe]
    execution_candles = candle_repository.get_recent(
        symbol.id, execution_timeframe.value, required_lookback_bars() + 5
    )
    if len(execution_candles) < 2:
        return _blocked(
            publisher,
            f"Aguardando candles de {broker_symbol}/{execution_timeframe.value} "
            "para executar o operacional escolhido.",
            blocking_error=None,
            symbol=config.symbol,
            broker_symbol=broker_symbol,
            **market_fields,
            **playbook_fields,
        )

    publisher.publish(
        AutopilotPhase.ANALYZING,
        f"{playbook.label} em {execution_timeframe.value}: analisando "
        f"{broker_symbol} com score minimo {playbook.analysis_threshold:.0f}.",
        enabled=True,
        **market_fields,
        **playbook_fields,
    )

    report = analyze_symbol(
        session,
        symbol=broker_symbol,
        primary_timeframe=execution_timeframe,
        threshold=playbook.analysis_threshold,
        now=resolved_now,
    )

    assert playbook.strategy_name is not None  # garantido por `tradeable`
    trigger = create_strategy(
        playbook.strategy_name, point=float(symbol.point), bar_seconds=bar_seconds
    )
    strategy = PlaybookConfluenceStrategy(
        report,
        trigger=trigger,
        expected_open_time=execution_candles[-1].open_time,
        playbook_label=playbook.label,
        playbook_kind=playbook.kind.value,
        fit_score=playbook.fit_score,
    )

    limits = RiskLimits(
        risk_per_trade_pct=round(config.risk_per_trade_pct * playbook.risk_factor, 4),
        max_daily_loss_pct=config.max_daily_loss_pct,
        max_consecutive_losses=config.max_consecutive_losses,
        max_simultaneous_positions=config.max_simultaneous_positions,
        max_trades_per_day=config.max_trades_per_day,
        min_seconds_between_trades=config.min_seconds_between_trades,
        max_spread_points=config.max_spread_points,
        max_feed_delay_seconds=max(
            float(resolved_settings.quality_max_feed_delay_seconds),
            float(bar_seconds * 2),
        ),
    )

    engine = DemoExecutionEngine(
        session,
        client,
        strategy,
        symbol=broker_symbol,
        symbol_id=symbol.id,
        timeframe=execution_timeframe.value,
        point=float(symbol.point),
        account=account,
        symbol_spec=spec,
        risk_limits=limits,
        magic=0,
        model_version=f"autopilot:{playbook.kind.value}",
        clock=lambda: resolved_now,
        # Obrigatorio: o timeframe muda entre ciclos conforme o horario e o
        # volume, e os limites de risco precisam valer para o simbolo
        # inteiro, nao por timeframe.
        scope_across_timeframes=True,
    )
    result = engine.step(execution_candles)

    analysis_fields = {
        "analysis_score": round(report.score.total_score, 1),
        "analysis_recommendation": report.recommendation,
    }

    trades_today, pnl_today = _daily_counters(session, symbol.id)
    open_position = _open_position_summary(session, symbol.id)

    for event in result.events:
        message, level = _describe_event(event)
        publisher.note(message, level=level)

    if any(isinstance(event, PositionOpened) for event in result.events):
        phase, headline, level = (
            AutopilotPhase.POSITION_OPEN,
            f"Operando {broker_symbol}: {open_position or 'posicao aberta'}.",
            ActivityLevel.GOOD,
        )
    elif open_position:
        phase, headline, level = (
            AutopilotPhase.POSITION_OPEN,
            f"Acompanhando a operacao aberta em {broker_symbol}: {open_position}. "
            "O stop e o alvo estao na corretora.",
            ActivityLevel.INFO,
        )
    elif report.recommendation != "ENTER":
        reason = report.rejection_reasons[0] if report.rejection_reasons else "score abaixo do minimo"
        phase, headline, level = (
            AutopilotPhase.WAITING_TRIGGER,
            f"Contexto ainda nao aprovado (score {report.score.total_score:.1f} / "
            f"minimo {playbook.analysis_threshold:.0f}): {reason}",
            ActivityLevel.INFO,
        )
    elif strategy.last_block_reason:
        phase, headline, level = (
            AutopilotPhase.WAITING_TRIGGER,
            strategy.last_block_reason,
            ActivityLevel.INFO,
        )
    else:
        phase, headline, level = (
            AutopilotPhase.WAITING_TRIGGER,
            f"{playbook.label} armado em {execution_timeframe.value}: aguardando a "
            "proxima barra fechar.",
            ActivityLevel.INFO,
        )

    publisher.publish(
        phase,
        headline,
        level=level,
        enabled=True,
        trades_today=trades_today,
        pnl_today=pnl_today,
        open_position=open_position,
        last_cycle_at=resolved_now.isoformat(),
        cycles=publisher.load().cycles + 1,
        last_error="",
        **market_fields,
        **playbook_fields,
        **analysis_fields,
    )

    return AutopilotCycleResult(
        ran=True,
        phase=phase,
        message=headline,
        playbook=playbook,
        events=tuple(result.events),
    )
