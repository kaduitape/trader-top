"""Worker Windows de sincronizacao continua com o MetaTrader 5.

Este processo fica residente na sessao do usuario do Windows (onde o
terminal MT5 e a DLL oficial estao disponiveis). Ele le do banco o plano
configurado no dashboard, mantem a conexao, reconecta automaticamente e
persiste candles fechados/ticks de forma incremental e idempotente.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

from app.core.build_info import code_version
from app.core.config import get_settings
from app.core.enums import SystemMode
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.data_quality_repository import DataQualityEventRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.system_setting_repository import get_current_mode
from app.database.repositories.tick_repository import TickRepository
from app.database.session import get_session_factory
from app.execution.analysis_strategy import AnalysisReportStrategy
from app.execution.automation_settings import (
    TradingAutomationConfig,
    load_trading_automation_config,
)
from app.execution.autopilot import EXECUTION_TIMEFRAMES, run_autopilot_cycle
from app.execution.autopilot_status import (
    ActivityLevel,
    AutopilotPhase,
    AutopilotStatusPublisher,
)
from app.execution.engine import DemoExecutionEngine
from app.market.catalog import resolve_broker_symbol
from app.market.data_quality import check_candles, check_ticks
from app.market.features import required_lookback_bars
from app.mt5.account import fetch_account_snapshot
from app.mt5.client import MT5ClientProtocol
from app.mt5.connection import MT5Connection, MT5ConnectionConfig
from app.mt5.market_data import (
    TIMEFRAME_SECONDS,
    Timeframe,
    fetch_candles_from_pos,
    fetch_candles_range,
    fetch_server_time,
    fetch_ticks_range,
)
from app.mt5.symbol_mapper import fetch_symbol_specification, list_symbols
from app.mt5.sync_settings import (
    MT5SyncConfig,
    MT5SyncStatus,
    load_sync_config,
    load_sync_status,
    save_sync_status,
    utc_now_iso,
)
from app.mt5.terminal_health import fetch_terminal_health
from app.news.call_log import ORIGIN_ROBOT, calls_from
from app.risk.config import RiskLimits
from app.services.analysis_service import analyze_symbol

logger = logging.getLogger(__name__)


class MT5AutoSyncWorker:
    """Orquestrador testavel do conector persistente."""

    def __init__(
        self,
        *,
        client: MT5ClientProtocol | None = None,
        stop_event: Event | None = None,
    ) -> None:
        self._settings = get_settings()
        self._client = client
        self._stop = stop_event or Event()
        self._connection: MT5Connection | None = None
        self._worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._next_sync_at = datetime.min.replace(tzinfo=UTC)
        self._started_at = utc_now_iso()
        self._failures = 0

    def stop(self) -> None:
        self._stop.set()

    def _read_control(self) -> tuple[MT5SyncConfig, MT5SyncStatus]:
        session = get_session_factory()()
        try:
            return load_sync_config(session), load_sync_status(session)
        finally:
            session.close()

    def _publish(self, status: MT5SyncStatus) -> MT5SyncStatus:
        published = replace(
            status,
            heartbeat_at=utc_now_iso(),
            worker_id=self._worker_id,
            # Carimbados em TODA publicacao, e nao so na inicial: e o unico
            # jeito de o painel saber que versao esta de fato rodando aqui,
            # inclusive quando o worker foi reiniciado por fora.
            code_version=code_version(),
            started_at=self._started_at,
            consecutive_failures=self._failures,
        )
        session = get_session_factory()()
        try:
            save_sync_status(session, published)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return published

    def _ensure_connection(self) -> MT5Connection | None:
        if self._connection is not None and self._connection.is_connected:
            health = fetch_terminal_health(self._connection.client)
            if health is not None and health.connected:
                return self._connection
            self._connection.disconnect()
            self._connection = None

        connection = MT5Connection(
            MT5ConnectionConfig.from_settings(self._settings),
            client=self._client,
        )
        if not connection.connect_with_retry():
            return None
        self._connection = connection
        return connection

    def _disconnect(self) -> None:
        if self._connection is not None and self._connection.is_connected:
            self._connection.disconnect()
        self._connection = None

    def _terminal_status(
        self,
        status: MT5SyncStatus,
        connection: MT5Connection,
        *,
        state: str,
    ) -> MT5SyncStatus:
        health = fetch_terminal_health(connection.client)
        account = fetch_account_snapshot(connection.client)
        if health is None or account is None:
            return replace(
                status,
                state="ERROR",
                connected=False,
                last_error="O terminal conectou, mas nao retornou os dados da conta.",
            )
        return replace(
            status,
            state=state,
            connected=health.connected,
            terminal_name=health.terminal_name,
            company=health.company,
            broker_server=account.server,
            account_login=account.login,
            account_is_demo=account.is_demo,
            last_error=None,
        )

    @staticmethod
    def _closed_candles(
        candles: list,
        *,
        server_now: datetime,
        timeframe_seconds: int,
    ) -> list:
        return [
            candle
            for candle in candles
            if candle.open_time + timedelta(seconds=timeframe_seconds) <= server_now
        ]

    def _sync_symbol(
        self,
        *,
        connection: MT5Connection,
        broker_symbol: str,
        config: MT5SyncConfig,
        heartbeat: Callable[[], None] | None = None,
    ) -> tuple[int, int]:
        client = connection.client
        client.symbol_select(broker_symbol, True)
        spec = fetch_symbol_specification(client, broker_symbol)
        if spec is None:
            raise RuntimeError(f"Especificacao indisponivel para {broker_symbol}.")

        server_now = fetch_server_time(client, broker_symbol) or datetime.now(UTC)
        inserted_candles = 0
        inserted_ticks = 0
        session = get_session_factory()()
        try:
            symbol = SymbolRepository(session).upsert_from_specification(spec)
            candle_repository = CandleRepository(session)
            quality_repository = DataQualityEventRepository(session)

            for timeframe_code in config.timeframes:
                timeframe = Timeframe(timeframe_code)
                timeframe_seconds = TIMEFRAME_SECONDS[timeframe]
                last_open_time = candle_repository.get_last_open_time(symbol.id, timeframe.value)
                if last_open_time is None:
                    candles = fetch_candles_from_pos(
                        client,
                        broker_symbol,
                        timeframe,
                        config.candle_backfill_count,
                        start_pos=1,
                    )
                else:
                    candles = fetch_candles_range(
                        client,
                        broker_symbol,
                        timeframe,
                        last_open_time + timedelta(seconds=timeframe_seconds),
                        server_now,
                    )
                    candles = self._closed_candles(
                        candles,
                        server_now=server_now,
                        timeframe_seconds=timeframe_seconds,
                    )
                inserted_candles += candle_repository.bulk_upsert(
                    symbol.id, timeframe.value, candles
                )
                quality_repository.bulk_insert(
                    symbol.id,
                    timeframe.value,
                    check_candles(candles, timeframe_seconds=timeframe_seconds),
                )
                if heartbeat is not None:
                    heartbeat()

            if config.collect_ticks:
                now = datetime.now(UTC)
                window_start = now - timedelta(seconds=config.tick_lookback_seconds)
                last_tick = TickRepository(session).get_last_timestamp(symbol.id)
                date_from = (
                    max(last_tick + timedelta(microseconds=1), window_start)
                    if last_tick is not None
                    else window_start
                )
                ticks = fetch_ticks_range(client, broker_symbol, date_from, now)
                inserted_ticks = TickRepository(session).bulk_upsert(symbol.id, ticks)
                quality_repository.bulk_insert(
                    symbol.id,
                    None,
                    check_ticks(
                        ticks,
                        point=float(symbol.point),
                        max_spread_points=self._settings.quality_max_spread_points,
                        now=now,
                        max_feed_delay_seconds=self._settings.quality_max_feed_delay_seconds,
                    ),
                )

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return inserted_candles, inserted_ticks

    def _sync_cycle(
        self,
        connection: MT5Connection,
        config: MT5SyncConfig,
        status: MT5SyncStatus,
    ) -> tuple[int, int, int, str | None]:
        available_names = list_symbols(connection.client)
        ready = 0
        candle_total = 0
        tick_total = 0
        errors: list[str] = []

        def publish_progress() -> None:
            self._publish(
                replace(
                    status,
                    state="SYNCING",
                    connected=True,
                    selected_symbols=len(config.symbols),
                    # Nao derrubar visualmente 4/4 para 0/4 no inicio de
                    # cada ciclo. A disponibilidade confirmada anterior
                    # permanece ate o ciclo atual terminar.
                    ready_symbols=status.ready_symbols,
                    candles_inserted=candle_total,
                    ticks_inserted=tick_total,
                    last_error="; ".join(errors)[:300] if errors else None,
                )
            )

        for canonical_code in config.symbols:
            if self._stop.is_set():
                break
            broker_symbol = resolve_broker_symbol(canonical_code, available_names)
            if broker_symbol is None:
                errors.append(f"{canonical_code} nao existe nesta corretora")
                publish_progress()
                continue
            try:
                candles, ticks = self._sync_symbol(
                    connection=connection,
                    broker_symbol=broker_symbol,
                    config=config,
                    heartbeat=publish_progress,
                )
            except Exception as exc:
                logger.exception(
                    "mt5_auto_sync_symbol_failed",
                    extra={"symbol": broker_symbol},
                )
                errors.append(f"{broker_symbol}: {exc}")
                publish_progress()
                continue
            ready += 1
            candle_total += candles
            tick_total += ticks
            publish_progress()
        error = "; ".join(errors)[:300] if errors else None
        return ready, candle_total, tick_total, error

    def _ensure_autopilot_off(self) -> None:
        """Deixa o status ao vivo coerente quando a automacao esta desligada.

        Sem isso, o painel continuaria exibindo a ultima fase publicada
        (ex.: "aguardando o gatilho") depois que o operador desligou o robo
        — exatamente o tipo de status que mente. So escreve quando ha algo
        a corrigir, para nao gerar escrita a cada ciclo ocioso.
        """
        publisher = AutopilotStatusPublisher(get_session_factory(), worker_id=self._worker_id)
        try:
            status = publisher.load()
            if status.enabled or status.phase != AutopilotPhase.OFF.value:
                publisher.turn_off()
        except Exception:
            logger.exception("autopilot_status_turn_off_failed")

    def _run_autopilot_cycle(
        self,
        connection: MT5Connection,
        config: TradingAutomationConfig,
    ) -> str | None:
        """Delega o ciclo ao piloto automatico, publicando o status ao vivo.

        O piloto escolhe o operacional, o timeframe e o score minimo a
        partir da sessao e do volume do par; aqui so ficam a conexao, a
        fronteira da transacao e a traducao do resultado para o status do
        conector.
        """
        publisher = AutopilotStatusPublisher(get_session_factory(), worker_id=self._worker_id)
        session = get_session_factory()()
        try:
            sync_config = load_sync_config(session)
            account = fetch_account_snapshot(connection.client)
            if account is None:
                publisher.publish(
                    AutopilotPhase.BLOCKED,
                    "Pausado: a conta MT5 nao respondeu.",
                    level=ActivityLevel.ERROR,
                    enabled=True,
                )
                return "Automacao pausada: a conta MT5 nao respondeu."

            result = run_autopilot_cycle(
                session,
                connection.client,
                config=config,
                account=account,
                publisher=publisher,
                available_symbols=list_symbols(connection.client),
                # So os timeframes de execucao que o plano de sincronizacao
                # de fato coleta — o piloto nunca escolhe um timeframe que
                # ficaria sem candles.
                available_timeframes=tuple(
                    code for code in EXECUTION_TIMEFRAMES if code in sync_config.timeframes
                ),
            )
            session.commit()
            if result.events:
                logger.info(
                    "autopilot_events",
                    extra={
                        "symbol": config.symbol,
                        "phase": result.phase.value,
                        "event_count": len(result.events),
                    },
                )
            return result.blocking_error
        except Exception as exc:
            session.rollback()
            logger.exception("autopilot_cycle_failed")
            message = f"Falha no piloto automatico: {exc}"[:300]
            try:
                publisher.publish(
                    AutopilotPhase.ERROR,
                    message,
                    level=ActivityLevel.ERROR,
                    enabled=True,
                    last_error=message,
                )
            except Exception:
                logger.exception("autopilot_status_publish_failed")
            return message
        finally:
            session.close()

    def _run_trading_cycle(
        self,
        connection: MT5Connection,
    ) -> str | None:
        """Avalia e executa uma oportunidade configurada, somente em DEMO.

        Retorna uma mensagem apenas quando existe um bloqueio operacional
        que deve aparecer no status do conector. Ausencia de sinal e vetos
        normais do motor de risco nao sao falhas do worker.
        """
        session = get_session_factory()()
        try:
            config = load_trading_automation_config(session)
        finally:
            session.close()

        if not config.enabled:
            self._ensure_autopilot_off()
            return None
        if config.autopilot:
            return self._run_autopilot_cycle(connection, config)

        session = get_session_factory()()
        try:
            if get_current_mode(session) != SystemMode.DEMO:
                return "Automacao pausada: o modo operacional nao esta em DEMO."

            account = fetch_account_snapshot(connection.client)
            if account is None:
                return "Automacao pausada: a conta MT5 nao respondeu."
            if not account.is_demo:
                return "Automacao bloqueada: a conta MT5 conectada nao e demo."

            available_names = list_symbols(connection.client)
            broker_symbol = resolve_broker_symbol(config.symbol, available_names)
            if broker_symbol is None:
                return (
                    f"Automacao pausada: {config.symbol} nao existe nesta corretora."
                )

            symbol = SymbolRepository(session).get_by_name(broker_symbol)
            spec = fetch_symbol_specification(connection.client, broker_symbol)
            if symbol is None or spec is None:
                return (
                    f"Automacao aguardando sincronizacao de {broker_symbol}."
                )

            timeframe = Timeframe(config.timeframe)
            candles = CandleRepository(session).get_recent(
                symbol.id,
                timeframe.value,
                required_lookback_bars() + 5,
            )
            if len(candles) < 2:
                return (
                    f"Automacao aguardando candles suficientes de "
                    f"{broker_symbol}/{timeframe.value}."
                )

            with calls_from(ORIGIN_ROBOT):
                report = analyze_symbol(
                    session,
                    symbol=broker_symbol,
                    primary_timeframe=timeframe,
                    threshold=config.analysis_threshold,
                )
            strategy = AnalysisReportStrategy(
                report,
                expected_open_time=candles[-1].open_time,
            )
            bar_seconds = TIMEFRAME_SECONDS[timeframe]
            limits = RiskLimits(
                risk_per_trade_pct=config.risk_per_trade_pct,
                max_daily_loss_pct=config.max_daily_loss_pct,
                max_consecutive_losses=config.max_consecutive_losses,
                max_simultaneous_positions=config.max_simultaneous_positions,
                max_trades_per_day=config.max_trades_per_day,
                min_seconds_between_trades=config.min_seconds_between_trades,
                max_spread_points=config.max_spread_points,
                max_feed_delay_seconds=max(
                    float(self._settings.quality_max_feed_delay_seconds),
                    float(bar_seconds * 2),
                ),
            )
            engine = DemoExecutionEngine(
                session,
                connection.client,
                strategy,
                symbol=broker_symbol,
                symbol_id=symbol.id,
                timeframe=timeframe.value,
                point=float(symbol.point),
                account=account,
                symbol_spec=spec,
                risk_limits=limits,
                magic=0,
                model_version="analysis-score",
            )
            result = engine.step(candles)
            session.commit()
            if result.events:
                logger.info(
                    "mt5_trading_automation_events",
                    extra={
                        "symbol": broker_symbol,
                        "timeframe": timeframe.value,
                        "event_count": len(result.events),
                    },
                )
            return None
        except Exception as exc:
            session.rollback()
            logger.exception("mt5_trading_automation_failed")
            return f"Falha na automacao de operacoes: {exc}"[:300]
        finally:
            session.close()

    def _run_observation_cycle(self, now: datetime) -> None:
        """Grava uma amostra do radar quando o modo observacao pede.

        Roda independentemente de o robo estar operando: o diario existe
        justamente para avaliar as escolhas ANTES de confiar nelas, entao
        exigir automacao ligada inverteria a ordem.

        Falha aqui nunca interrompe o ciclo do worker — observar e trabalho
        acessorio, e derrubar a sincronizacao de candles por causa dele
        seria trocar o essencial pelo opcional.
        """
        from app.calendar_feed.factory import get_calendar_provider
        from app.market.scan_journal import record_scan
        from app.market.scan_settings import (
            is_due,
            load_observation_config,
            mark_recorded,
        )
        from app.market.scanner import scan_market

        session = get_session_factory()()
        try:
            config = load_observation_config(session)
            if not is_due(config, now=now):
                return

            settings = get_settings()
            resultado = scan_market(
                session,
                now=now,
                timeframe=settings.analysis_default_timeframe,
                calendar=get_calendar_provider(settings).fetch_events(
                    now=now, horizon_minutes=120
                ),
            )
            record_scan(session, resultado)
            # A marca de tempo sobe mesmo quando nao havia candidato: sem
            # isso, um fim de semana inteiro faria o worker refazer a
            # varredura a cada ciclo, sem nunca gravar nada.
            mark_recorded(session, config, now=now)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("scanner_observation_failed")
        finally:
            session.close()

    def _tick(self) -> None:
        """Um ciclo do laco.

        Extraido de `run` de proposito: assim uma falha aqui custa UM ciclo,
        e nao o processo inteiro. Antes, qualquer excecao — banco fora do ar
        por um segundo, terminal recusando uma chamada — subia ate o topo e
        matava o conector; o Windows reiniciava algumas vezes e desistia, e
        a unica saida visivel virava reinstalar.
        """
        config, persisted_status = self._read_control()
        status = replace(
            persisted_status,
            worker_online=True,
            worker_id=self._worker_id,
            selected_symbols=len(config.symbols),
        )
        test_pending = (
            bool(config.test_request_id)
            and config.test_request_id != status.handled_test_request_id
        )
        manual_sync_pending = (
            bool(config.sync_request_id)
            and config.sync_request_id != status.handled_sync_request_id
        )

        status = self._publish(replace(status, state="CONNECTING", last_error=None))
        connection = self._ensure_connection()
        if connection is None:
            status = self._publish(
                replace(
                    status,
                    state="ERROR",
                    connected=False,
                    last_error=(
                        "Nao foi possivel conectar ao terminal MT5. "
                        "Confirme se ele esta aberto, autenticado e acessivel."
                    ),
                    handled_test_request_id=(
                        config.test_request_id if test_pending else status.handled_test_request_id
                    ),
                )
            )
            self._stop.wait(min(config.interval_seconds, 30))
            return

        status = self._terminal_status(status, connection, state="ONLINE")
        if not config.enabled and not test_pending:
            # Pausar a sincronizacao nao torna o terminal desconectado. O
            # conector continua mantendo a sessao e publicando heartbeat para
            # que todas as telas mostrem a conectividade real com o MT5.
            self._publish(
                replace(status, state="PAUSED", connected=True, last_error=None)
            )
            self._stop.wait(5)
            return

        if test_pending:
            # O conector executa o teste no mesmo cliente persistente usado
            # para sincronizacao e operacao.
            self._run_credential_test(connection.client)
            status = replace(
                status,
                handled_test_request_id=config.test_request_id,
            )
            self._publish(status)
            if not config.enabled:
                self._disconnect()
                self._stop.wait(2)
                return

        now = datetime.now(UTC)
        if manual_sync_pending or now >= self._next_sync_at:
            status = self._publish(replace(status, state="SYNCING", connected=True))
            ready, candles, ticks, error = self._sync_cycle(
                connection, config, status
            )
            # Bater o coracao entre as etapas longas. Analisar consulta a
            # API paga (dois pedidos, ate 10s cada) e observar varre o
            # mercado inteiro: sem isto o painel declara o conector
            # offline aos 90s de silencio enquanto ele esta, na verdade,
            # trabalhando.
            status = self._publish(replace(status, state="SYNCING", connected=True))
            trading_error = self._run_trading_cycle(connection)

            status = self._publish(replace(status, state="SYNCING", connected=True))
            self._run_observation_cycle(now)

            combined_error = "; ".join(
                item for item in (error, trading_error) if item
            ) or None
            status = self._publish(
                replace(
                    status,
                    state="ONLINE" if ready else "ERROR",
                    connected=True,
                    last_sync_at=utc_now_iso(),
                    ready_symbols=ready,
                    candles_inserted=candles,
                    ticks_inserted=ticks,
                    last_error=combined_error,
                    handled_sync_request_id=(
                        config.sync_request_id
                        if manual_sync_pending
                        else status.handled_sync_request_id
                    ),
                )
            )
            self._next_sync_at = now + timedelta(seconds=config.interval_seconds)
        else:
            status = self._publish(replace(status, state="ONLINE", connected=True))

        self._stop.wait(min(5, config.interval_seconds))

    def _run_credential_test(self, client: MT5ClientProtocol) -> None:
        """Testa a credencial cadastrada e publica o resultado no banco.

        Nunca derruba o ciclo: um teste que explode e um teste que falhou,
        nao um conector que morre. E a senha so existe em memoria, entre a
        leitura e a chamada ao terminal.
        """
        from app.core.crypto import CredentialCryptoError
        from app.database.repositories.mt5_credential_repository import (
            Mt5CredentialRepository,
        )
        from app.mt5.connection_service import MT5ConnectionService

        session = get_session_factory()()
        try:
            repo = Mt5CredentialRepository(session)
            credencial = repo.get_active()
            if credencial is None:
                return

            try:
                senha = repo.reveal_password(credencial)
            except CredentialCryptoError as exc:
                repo.record_test(credencial, success=False, error=str(exc)[:500])
                session.commit()
                return

            servico = MT5ConnectionService(client=client)
            resultado = servico.test_connection(
                login=credencial.login,
                password=senha,
                server=credencial.server,
                # Esse caminho pertence ao Windows/Wine remoto e nao existe
                # no filesystem Linux do worker.
                terminal_path=(
                    None if self._settings.mt5_bridge_host else credencial.terminal_path
                ),
            )
            del senha  # fora de escopo o quanto antes

            repo.record_test(
                credencial,
                success=resultado.success,
                error=None if resultado.success else resultado.message[:500],
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("mt5_credential_test_failed")
        finally:
            session.close()

    def run(self) -> None:
        """Executa ate receber encerramento do Windows/processo.

        Supervisiona `_tick`: nada alem de `stop()` encerra este laco. O
        conector existe para ficar de pe sozinho — se ele precisa de alguem
        para reinicia-lo, ele nao esta fazendo o trabalho dele.
        """
        try:
            self._publish(MT5SyncStatus(state="STARTING", worker_online=True))
        except Exception:
            logger.exception("mt5_auto_sync_initial_status_failed")

        while not self._stop.is_set():
            try:
                self._tick()
                self._failures = 0
            except Exception as exc:
                self._failures += 1
                logger.exception("mt5_auto_sync_cycle_failed")
                # A conexao pode ter ficado num estado ruim; derrubar aqui
                # forca reconexao limpa no proximo ciclo.
                try:
                    self._disconnect()
                except Exception:
                    logger.exception("mt5_auto_sync_disconnect_failed")
                self._report_failure(exc, self._failures)
                # Espera crescente ate 60s: falha continua costuma ser algo
                # externo (banco, terminal fechado) e martelar a cada segundo
                # so enche o log e gasta CPU.
                self._stop.wait(min(60, 5 * self._failures))

        self._shutdown()

    def _report_failure(self, exc: Exception, falhas: int) -> None:
        """Publica a falha para o painel dizer o que houve.

        Se o proprio banco for o problema, publicar tambem falha — e tudo
        bem: o painel ja mostra OFFLINE pelo heartbeat velho. O que nao pode
        acontecer e a tentativa de avisar derrubar o worker.
        """
        try:
            _, persisted = self._read_control()
            self._publish(
                replace(
                    persisted,
                    state="ERROR",
                    worker_online=True,
                    connected=False,
                    last_error=(
                        f"Falha no ciclo {falhas}x seguidas: {exc}"[:300]
                    ),
                )
            )
        except Exception:
            logger.exception("mt5_auto_sync_failure_report_failed")

    def _shutdown(self) -> None:
        try:
            self._disconnect()
        except Exception:
            logger.exception("mt5_auto_sync_disconnect_failed")
        try:
            _, persisted = self._read_control()
            self._publish(
                replace(persisted, state="OFFLINE", worker_online=False, connected=False)
            )
        except Exception:
            logger.exception("mt5_auto_sync_final_status_failed")
