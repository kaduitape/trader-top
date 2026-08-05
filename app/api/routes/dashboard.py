"""Dashboard HTML (Fase 12, estendido nas Fases 13/16/18) — a maioria das
rotas continua somente leitura: paper trades, live trades, modelos de
ML registrados, eventos de drift e o log de auditoria. `/dashboard/mode`
(Fase 16) e `/dashboard/settings/aisa` (Fase 18.6) sao excecoes
deliberadas a essa regra: a primeira muda o modo do sistema pela web,
sempre atras da mesma validacao/auditoria da CLI
(`app.database.repositories.system_setting_repository.set_mode`) e
exigindo que o usuario digite o nome do modo-alvo como confirmacao
explicita — nunca um clique unico dispara a mudanca; a segunda permite
configurar a chave da API de noticias/fundamentos (Fase 18.6) sem editar
`.env`/reiniciar o processo, sempre auditada e nunca exibindo a chave em
texto puro de volta na tela."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.apexflow.config import load_apexflow_config, save_apexflow_config
from app.api.dependencies.auth import get_current_user_for_web
from app.api.templates_engine import templates
from app.core.config import get_settings
from app.core.enums import SystemMode
from app.core.system_mode import SystemModeError, validate_transition
from app.database.models.user import User
from app.database.repositories.apexflow_decision_repository import (
    ApexFlowDecisionRepository,
)
from app.database.repositories.audit_log_repository import AuditLogRepository
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.drift_event_repository import DriftEventRepository
from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.paper_trade_repository import PaperTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.system_setting_repository import (
    SystemSettingRepository,
    activate_trading_mode,
    get_current_mode,
    set_mode,
)
from app.database.repositories.tick_repository import TickRepository
from app.database.session import get_db
from app.execution.automation_settings import (
    ENGINE_APEXFLOW,
    ENGINE_PLAYBOOK,
    TRADING_MODE_DEMO,
    TRADING_MODE_REAL,
    TRADING_MODES,
    TradingAutomationConfig,
    load_trading_automation_config,
    save_trading_automation_config,
)
from app.execution.autopilot_status import (
    AutopilotStatus,
    load_autopilot_status,
    summarize_activities,
)
from app.execution.blocker_stats import load_blocker_stats
from app.market.catalog import (
    GROUP_LABELS,
    MARKET_CATALOG,
    catalog_availability,
    grouped_availability,
)
from app.market.multi_timeframe import ANALYSIS_TIMEFRAMES, SymbolNotFoundError
from app.market.scan_settings import (
    INTERVAL_MAX_MINUTES,
    INTERVAL_MIN_MINUTES,
    ObservationConfig,
    clamp_interval,
    load_observation_config,
    save_observation_config,
)
from app.ml.registry import ModelRegistry
from app.mt5.market_data import Timeframe
from app.mt5.sync_settings import (
    heartbeat_age_label,
    heartbeat_is_fresh,
    load_sync_config,
    load_sync_status,
    save_sync_config,
)
from app.news.api_settings import (
    BUDGET_MAX,
    BUDGET_MIN,
    TTL_MAX,
    TTL_MIN,
    load_api_settings,
    save_api_settings,
    validate_api_settings,
)
from app.news.call_log import (
    ORIGIN_PANEL,
    calls_from,
    load_calls,
    summarize_calls,
)
from app.news.diagnostics import probe_api
from app.news.factory import (
    AISA_API_BASE_URL_SETTING,
    AISA_API_KEY_SETTING,
    get_assessment_cache,
    get_budget_usage,
    reset_assessment_cache,
)
from app.services.analysis_service import AnalysisReport, analyze_symbol

router = APIRouter(tags=["dashboard"])

_FACTOR_LABELS = {
    "structure": "Estrutura",
    "price_action": "Price Action",
    "liquidity": "Liquidez / SMC",
    "volume": "Volume",
    "news": "Noticias",
    "fundamentals": "Fundamentos",
    "correlation": "Correlacao",
}

_TREND_LABELS = {
    "UP": "Tendencia de alta",
    "DOWN": "Tendencia de baixa",
    "SIDEWAYS": "Lateralizacao",
}


def _analysis_context(
    *,
    user: User,
    symbols: list,
    selected_symbol: str | None,
    selected_timeframe: Timeframe,
    report: AnalysisReport | None = None,
    error: str | None = None,
    aisa_configured: bool = False,
    price_format: str = "%.5f",
    operation_config: TradingAutomationConfig | None = None,
    trading: dict,
) -> dict:
    resolved_operation_config = operation_config or TradingAutomationConfig()
    return {
        # O estado do robo vem inteiro do mesmo payload da tela de operacao,
        # para que o widget embutido aqui nunca discorde dela.
        **trading,
        "robot_status_compact": True,
        "robot_status_origin": "/dashboard/analysis",
        "robot_status_symbol": selected_symbol or trading["config"].symbol,
        "user": user,
        "symbols": symbols,
        "selected_symbol": selected_symbol,
        "selected_timeframe": selected_timeframe.value,
        "analysis_timeframes": [tf.value for tf in ANALYSIS_TIMEFRAMES],
        "report": report,
        "error": error,
        "factor_labels": _FACTOR_LABELS,
        "trend_labels": _TREND_LABELS,
        "aisa_configured": aisa_configured,
        "price_format": price_format,
        "market_groups": grouped_availability(symbols),
        "market_group_labels": GROUP_LABELS,
        "analysis_threshold": resolved_operation_config.analysis_threshold,
        "risk_per_trade_pct": resolved_operation_config.risk_per_trade_pct,
        "max_simultaneous_positions": resolved_operation_config.max_simultaneous_positions,
        "trading_automation_enabled": resolved_operation_config.enabled,
    }


def _allowed_targets(current: SystemMode) -> list[SystemMode]:
    """Reusa a MESMA validacao da CLI/`app.core.system_mode` (nunca uma
    copia das regras) -- tenta cada modo e mantem so os que nao levantam
    `SystemModeError`, para que o formulario nunca ofereca uma transicao
    que o backend recusaria."""
    allowed = []
    for candidate in SystemMode:
        try:
            validate_transition(current, candidate)
        except SystemModeError:
            continue
        allowed.append(candidate)
    return allowed


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_home(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    mode = get_current_mode(db)
    recent_paper_trades = PaperTradeRepository(db).list_all_recent(limit=5)
    recent_live_trades = LiveTradeRepository(db).list_all_recent(limit=5)
    recent_audit = AuditLogRepository(db).list_recent(limit=5)
    recent_drift = DriftEventRepository(db).list_recent(limit=5)
    symbols = SymbolRepository(db).list_active()
    candle_summary = CandleRepository(db).summary()
    coverage_by_symbol: dict[str, set[str]] = {}
    for symbol_name, timeframe, *_ in candle_summary:
        coverage_by_symbol.setdefault(symbol_name, set()).add(timeframe)
    required_timeframes = {tf.value for tf in ANALYSIS_TIMEFRAMES}
    full_coverage_count = sum(
        1 for coverage in coverage_by_symbol.values() if required_timeframes <= coverage
    )
    catalog = catalog_availability(symbols)
    settings = get_settings()
    persisted_key = SystemSettingRepository(db).get(AISA_API_KEY_SETTING)
    mt5_sync_config = load_sync_config(db)
    mt5_sync_status = load_sync_status(db)

    return templates.TemplateResponse(
        request,
        "dashboard/home.html",
        {
            "user": user,
            "system_mode": mode.value,
            "recent_paper_trades": recent_paper_trades,
            "recent_live_trades": recent_live_trades,
            "recent_audit": recent_audit,
            "recent_drift": recent_drift,
            "synced_symbol_count": len(symbols),
            "catalog_ready_count": sum(1 for item in catalog if item.is_available),
            "catalog_total_count": len(catalog),
            "full_coverage_count": full_coverage_count,
            "total_candles": sum(row[2] for row in candle_summary),
            "aisa_configured": bool(persisted_key or settings.aisa_api_key),
            "critical_drift_count": sum(
                1 for event in recent_drift if event.severity == "CRITICAL"
            ),
            "mt5_sync_enabled": mt5_sync_config.enabled,
            "mt5_worker_online": heartbeat_is_fresh(mt5_sync_status),
            "mt5_connected": mt5_sync_status.connected
            and heartbeat_is_fresh(mt5_sync_status),
        },
    )


@router.get("/dashboard/markets", response_class=HTMLResponse)
def dashboard_markets(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    symbols = SymbolRepository(db).list_active()
    availability = catalog_availability(symbols)
    matched_names = {
        item.synced_symbol for item in availability if item.synced_symbol is not None
    }
    return templates.TemplateResponse(
        request,
        "dashboard/markets.html",
        {
            "user": user,
            "market_groups": grouped_availability(symbols),
            "market_group_labels": GROUP_LABELS,
            "ready_count": sum(1 for item in availability if item.is_available),
            "catalog_count": len(availability),
            "unmapped_symbols": [
                symbol for symbol in symbols if symbol.name not in matched_names
            ],
        },
    )


@router.get("/dashboard/mt5", response_class=HTMLResponse)
def dashboard_mt5(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    action: str | None = None,
    error: str | None = None,
    select: str | None = None,
) -> HTMLResponse:
    """Central de controle do worker Windows; nunca recebe credenciais MT5."""
    config = load_sync_config(db)
    status = load_sync_status(db)
    worker_online = heartbeat_is_fresh(status)
    # Ja instalado alguma vez? Um batimento antigo prova que sim. Sem essa
    # distincao a tela mandava INSTALAR toda vez que o conector caia — e
    # reinstalar virou o remedio para tudo, refazendo o ambiente Python
    # inteiro por causa de um processo que so precisava voltar a subir.
    ever_installed = bool(status.heartbeat_at or status.worker_id)
    groups: dict[str, list] = {group: [] for group in GROUP_LABELS}
    for instrument in MARKET_CATALOG:
        groups[instrument.group].append(instrument)
    preselected = (select or "").strip().upper()
    selected_codes = set(config.symbols)
    if preselected in {instrument.code for instrument in MARKET_CATALOG}:
        selected_codes.add(preselected)

    return templates.TemplateResponse(
        request,
        "dashboard/mt5.html",
        {
            "user": user,
            "config": config,
            "status": status,
            "worker_online": worker_online,
            "ever_installed": ever_installed,
            "last_heartbeat": heartbeat_age_label(status),
            "market_groups": groups,
            "market_group_labels": GROUP_LABELS,
            "selected_codes": selected_codes,
            "saved": saved,
            "action_result": action,
            "error": error,
        },
    )


@router.get("/dashboard/mt5/status", response_class=JSONResponse)
def dashboard_mt5_status(
    _user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> JSONResponse:
    status = load_sync_status(db)
    fresh = heartbeat_is_fresh(status)
    return JSONResponse(
        {
            "state": status.state if fresh else "OFFLINE",
            "worker_online": fresh,
            "connected": status.connected and fresh,
            "heartbeat_at": status.heartbeat_at,
            "last_sync_at": status.last_sync_at,
            "terminal_name": status.terminal_name,
            "company": status.company,
            "broker_server": status.broker_server,
            "account_login": status.account_login,
            "account_is_demo": status.account_is_demo,
            "selected_symbols": status.selected_symbols,
            "ready_symbols": status.ready_symbols,
            "candles_inserted": status.candles_inserted,
            "ticks_inserted": status.ticks_inserted,
            "last_error": status.last_error,
        }
    )


@router.get("/dashboard/mt5/installer", response_class=PlainTextResponse)
def dashboard_mt5_installer(
    _user: User = Depends(get_current_user_for_web),
) -> PlainTextResponse:
    """Baixa launcher autocontido que localiza o projeto no host Windows."""
    powershell = r"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Selecione a pasta mt5_ai_scalper do AI Trader PRO"
$dialog.ShowNewFolderButton = $false
if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
    throw "Instalacao cancelada."
}
$projectRoot = $dialog.SelectedPath
$installer = Join-Path $projectRoot "scripts\instalar_conector_mt5.ps1"
if (-not (Test-Path $installer)) {
    throw "A pasta selecionada nao contem scripts\instalar_conector_mt5.ps1."
}
& $installer
"""
    encoded = base64.b64encode(powershell.encode("utf-16le")).decode("ascii")
    content = (
        "@echo off\r\n"
        "title AI Trader PRO - Instalador MT5\r\n"
        f"powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}\r\n"
        "pause\r\n"
    )
    return PlainTextResponse(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="Instalar-Conector-MT5.cmd"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/dashboard/mt5/config")
def dashboard_mt5_config_save(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    symbols: list[str] = Form(default=[]),
    interval_seconds: int = Form(15),
    candle_backfill_count: int = Form(2_000),
    collect_ticks: str = Form(""),
) -> RedirectResponse:
    allowed = {instrument.code for instrument in MARKET_CATALOG}
    selected = tuple(
        dict.fromkeys(
            symbol.strip().upper()
            for symbol in symbols
            if symbol.strip().upper() in allowed
        )
    )
    if not selected:
        return RedirectResponse(
            url=f"/dashboard/mt5?error={quote('selecione ao menos um ativo.')}#markets",
            status_code=303,
        )

    current = load_sync_config(db)
    updated = replace(
        current,
        symbols=selected,
        interval_seconds=min(max(interval_seconds, 5), 3_600),
        candle_backfill_count=min(max(candle_backfill_count, 200), 100_000),
        collect_ticks=bool(collect_ticks),
    )
    save_sync_config(db, updated)
    AuditLogRepository(db).record(
        action="mt5_sync_config_change",
        entity="mt5_auto_sync",
        detail=(
            f"{len(selected)} ativo(s), intervalo {updated.interval_seconds}s, "
            f"{len(updated.timeframes)} timeframes por {user.username}"
        ),
        user_id=user.id,
    )
    db.commit()
    return RedirectResponse(url="/dashboard/mt5?saved=1", status_code=303)


@router.post("/dashboard/mt5/action")
def dashboard_mt5_action(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    requested_action: str = Form(...),
    origin: str = Form("/dashboard/mt5"),
) -> RedirectResponse:
    # `origin` deixa o botao "Atualizar dados agora" existir nas telas onde a
    # falta de dado aparece, e nao so na tela do conector. Valor fora da
    # lista conhecida cai no conector, para que um parametro manipulado nao
    # vire redirecionamento aberto.
    destino = (
        origin
        if origin in {"/dashboard/mt5", "/dashboard/market-data", "/dashboard/analysis"}
        else "/dashboard/mt5"
    )
    config = load_sync_config(db)
    action = requested_action.strip().lower()
    if action == "start":
        updated = replace(config, enabled=True, sync_request_id=uuid4().hex)
        message = "Sincronizacao automatica ativada"
    elif action == "pause":
        updated = replace(config, enabled=False)
        message = "Sincronizacao automatica pausada"
    elif action == "sync":
        if not config.enabled:
            return RedirectResponse(
                url=f"{destino}?error={quote('ative a sincronizacao antes de atualizar agora.')}",
                status_code=303,
            )
        updated = replace(config, sync_request_id=uuid4().hex)
        message = "Sincronizacao imediata solicitada"
    elif action == "test":
        updated = replace(config, test_request_id=uuid4().hex)
        message = "Teste de conexao solicitado"
    else:
        return RedirectResponse(
            url=f"{destino}?error={quote('acao MT5 desconhecida.')}",
            status_code=303,
        )

    save_sync_config(db, updated)
    AuditLogRepository(db).record(
        action=f"mt5_sync_{action}",
        entity="mt5_auto_sync",
        detail=f"{message} por {user.username}",
        user_id=user.id,
    )
    db.commit()
    # A tela do conector tem um bloco proprio para `action`; as outras usam o
    # mesmo `saved` que ja exibem para qualquer confirmacao.
    campo = "action" if destino == "/dashboard/mt5" else "saved"
    return RedirectResponse(url=f"{destino}?{campo}={quote(message)}", status_code=303)


@router.get("/dashboard/analysis", response_class=HTMLResponse)
def dashboard_analysis(
    request: Request,
    symbol: str | None = None,
    timeframe: str = "M15",
    saved: str | None = None,
    error: str | None = None,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Workbench consultivo: seleciona um simbolo real e aplica score >= 90."""
    settings = get_settings()
    symbols = SymbolRepository(db).list_active()
    persisted_key = SystemSettingRepository(db).get(AISA_API_KEY_SETTING)
    aisa_configured = bool(persisted_key or settings.aisa_api_key)
    trading = _trading_payload(db)
    trading["robot_saved"] = bool(saved)
    trading["robot_error"] = error
    operation_config = trading["config"]

    try:
        selected_timeframe = Timeframe(timeframe)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "dashboard/analysis.html",
            _analysis_context(
                user=user,
                symbols=symbols,
                selected_symbol=None,
                selected_timeframe=Timeframe.M15,
                error=f"Timeframe invalido: {timeframe}.",
                aisa_configured=aisa_configured,
                operation_config=operation_config,
                trading=trading,
            ),
            status_code=400,
        )

    if selected_timeframe not in ANALYSIS_TIMEFRAMES:
        return templates.TemplateResponse(
            request,
            "dashboard/analysis.html",
            _analysis_context(
                user=user,
                symbols=symbols,
                selected_symbol=None,
                selected_timeframe=Timeframe.M15,
                error="O timeframe selecionado nao pertence a matriz profissional de analise.",
                aisa_configured=aisa_configured,
                operation_config=operation_config,
                trading=trading,
            ),
            status_code=400,
        )

    normalized_symbol = symbol.strip().upper() if symbol else None
    if normalized_symbol is None:
        return templates.TemplateResponse(
            request,
            "dashboard/analysis.html",
            _analysis_context(
                user=user,
                symbols=symbols,
                selected_symbol=None,
                selected_timeframe=selected_timeframe,
                aisa_configured=aisa_configured,
                operation_config=operation_config,
                trading=trading,
            ),
        )

    selected = SymbolRepository(db).get_by_name(normalized_symbol)
    if selected is None or not selected.is_active:
        return templates.TemplateResponse(
            request,
            "dashboard/analysis.html",
            _analysis_context(
                user=user,
                symbols=symbols,
                selected_symbol=normalized_symbol,
                selected_timeframe=selected_timeframe,
                error="Ativo indisponivel. Selecione um simbolo sincronizado com o MetaTrader 5.",
                aisa_configured=aisa_configured,
                operation_config=operation_config,
                trading=trading,
            ),
            status_code=404,
        )

    try:
        # Abrir a tela consulta a API paga. Marcar a origem e o que permite
        # ver depois, no registro, que a cota foi gasta pelo painel e nao
        # pelo robo.
        with calls_from(ORIGIN_PANEL):
            report = analyze_symbol(
                db,
                symbol=selected.name,
                primary_timeframe=selected_timeframe,
                threshold=operation_config.analysis_threshold,
            )
    except SymbolNotFoundError as exc:
        error = str(exc)
        status_code = 404
    except NotImplementedError as exc:
        error = str(exc)
        status_code = 503
    except Exception:
        # Nao expor stack trace, detalhes internos ou credenciais no HTML.
        error = (
            "A analise nao pôde ser concluida. Verifique a cobertura dos "
            "dados e a integracao AIsa."
        )
        status_code = 500
    else:
        return templates.TemplateResponse(
            request,
            "dashboard/analysis.html",
            _analysis_context(
                user=user,
                symbols=symbols,
                selected_symbol=selected.name,
                selected_timeframe=selected_timeframe,
                report=report,
                aisa_configured=aisa_configured,
                price_format=f"%.{selected.digits}f",
                operation_config=operation_config,
                trading=trading,
            ),
        )

    return templates.TemplateResponse(
        request,
        "dashboard/analysis.html",
        _analysis_context(
            user=user,
            symbols=symbols,
            selected_symbol=selected.name,
            selected_timeframe=selected_timeframe,
            error=error,
            aisa_configured=aisa_configured,
            operation_config=operation_config,
            trading=trading,
        ),
        status_code=status_code,
    )


@router.get("/dashboard/paper-trades", response_class=HTMLResponse)
def dashboard_paper_trades(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    trades = PaperTradeRepository(db).list_all_recent(limit=100)
    return templates.TemplateResponse(
        request,
        "dashboard/paper_trades.html",
        {
            "user": user,
            "trades": trades,
            "open_trade_count": sum(1 for trade, _ in trades if trade.status == "OPEN"),
        },
    )


@router.get("/dashboard/live-trades", response_class=HTMLResponse)
def dashboard_live_trades(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    trades = LiveTradeRepository(db).list_all_recent(limit=100)
    return templates.TemplateResponse(
        request, "dashboard/live_trades.html", {"user": user, "trades": trades}
    )


@router.get("/dashboard/models", response_class=HTMLResponse)
def dashboard_models(
    request: Request,
    user: User = Depends(get_current_user_for_web),
) -> HTMLResponse:
    settings = get_settings()
    registry = ModelRegistry(settings.ml_models_dir)
    versions = sorted(registry.list_versions(), key=lambda entry: entry.version, reverse=True)
    return templates.TemplateResponse(
        request,
        "dashboard/models.html",
        {
            "user": user,
            "versions": versions,
            "current_version": registry.current_version(),
        },
    )


@router.get("/dashboard/audit-log", response_class=HTMLResponse)
def dashboard_audit_log(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    entries = AuditLogRepository(db).list_recent(limit=100)
    return templates.TemplateResponse(
        request, "dashboard/audit_log.html", {"user": user, "entries": entries}
    )


@router.get("/dashboard/drift", response_class=HTMLResponse)
def dashboard_drift(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    events = DriftEventRepository(db).list_recent(limit=100)
    return templates.TemplateResponse(
        request, "dashboard/drift.html", {"user": user, "events": events}
    )


@router.get("/dashboard/market-data", response_class=HTMLResponse)
def dashboard_market_data(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    candle_summary = CandleRepository(db).summary()
    tick_summary = TickRepository(db).summary()
    unique_symbols = {row[0] for row in candle_summary} | {row[0] for row in tick_summary}
    return templates.TemplateResponse(
        request,
        "dashboard/market_data.html",
        {
            "user": user,
            # Mesmo payload da tela de operacao: o widget daqui liga o robo
            # sem sair da pagina e mostra o estado real dele.
            **_trading_payload(db),
            "robot_status_compact": True,
            "robot_status_origin": "/dashboard/market-data",
            "robot_saved": bool(saved),
            "robot_error": error,
            "candle_summary": candle_summary,
            "tick_summary": tick_summary,
            "symbol_count": len(unique_symbols),
            "candle_count": sum(row[2] for row in candle_summary),
            "tick_count": sum(row[1] for row in tick_summary),
            "timeframe_count": len({row[1] for row in candle_summary}),
        },
    )


def _run_scan(db: Session, now: datetime):
    """Varredura + calendario, do jeito que a tela e o botao precisam.

    Existe para que "ver o ranking" e "gravar esta escolha" usem exatamente
    o mesmo caminho: se o botao gravasse algo diferente do que a tela
    mostra, o diario deixaria de descrever o que o operador viu.
    """
    from app.calendar_feed.factory import get_calendar_provider
    from app.market.scanner import scan_market

    settings = get_settings()
    calendario = get_calendar_provider(settings).fetch_events(
        now=now, horizon_minutes=120
    )
    resultado = scan_market(
        db,
        now=now,
        timeframe=settings.analysis_default_timeframe,
        calendar=calendario,
    )
    return resultado, calendario


@router.get("/dashboard/scanner", response_class=HTMLResponse)
def dashboard_scanner(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Ranking de oportunidades entre todos os instrumentos coletados.

    Tela de LEITURA quanto a operar — ela nunca envia ordem. Gravar uma
    amostra no diario e a unica escrita, e e escrita de observacao.
    """
    from app.market.scan_journal import summarize

    agora = datetime.now(UTC)
    resultado, calendario = _run_scan(db, agora)
    observacao = load_observation_config(db)

    return templates.TemplateResponse(
        request,
        "dashboard/scanner.html",
        {
            "user": user,
            "generated_at": agora,
            "candidates": resultado.candidates,
            "best": resultado.best,
            "calendar_status": calendario.status.value,
            "calendar_message": calendario.message,
            "journal": summarize(db),
            "observation": observacao,
            "observation_next_at": observacao.next_due_at(),
            "interval_min": INTERVAL_MIN_MINUTES,
            "interval_max": INTERVAL_MAX_MINUTES,
            "worker_online": heartbeat_is_fresh(load_sync_status(db)),
            "saved": saved,
            "error": error,
        },
    )


@router.post("/dashboard/scanner/record")
def dashboard_scanner_record(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Grava agora a escolha do radar — o que antes exigia `scanner run --record`.

    Varredura sem candidato nao vira registro (regra do proprio diario),
    entao a tela precisa dizer que nada foi gravado em vez de fingir
    sucesso.
    """
    from app.market.scan_journal import record_scan

    agora = datetime.now(UTC)
    resultado, _ = _run_scan(db, agora)
    observacao = record_scan(db, resultado)
    if observacao is None:
        db.rollback()
        return RedirectResponse(
            url=(
                "/dashboard/scanner?error="
                + quote(
                    "nenhum instrumento aprovado agora — nada foi gravado. "
                    "Varredura sem candidato nao vira amostra."
                )
            ),
            status_code=303,
        )

    AuditLogRepository(db).record(
        action="scanner_observation_record",
        entity="scanner",
        detail=(
            f"{observacao.symbol} nota {observacao.score:.0f} "
            f"gravado manualmente por {user.username}"
        ),
        user_id=user.id,
    )
    db.commit()
    return RedirectResponse(
        url="/dashboard/scanner?saved=" + quote(f"{observacao.symbol} gravado no diario."),
        status_code=303,
    )


@router.post("/dashboard/scanner/observation")
def dashboard_scanner_observation(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    action: str = Form("start"),
    interval_minutes: int = Form(30),
) -> RedirectResponse:
    """Liga/desliga o registro automatico. Quem executa e o worker MT5."""
    atual = load_observation_config(db)
    ligado = action == "start"
    intervalo = clamp_interval(interval_minutes)

    save_observation_config(
        db,
        ObservationConfig(
            enabled=ligado,
            interval_minutes=intervalo,
            last_recorded_at=atual.last_recorded_at,
        ),
    )
    AuditLogRepository(db).record(
        action="scanner_observation_toggle",
        entity="scanner",
        detail=(
            f"modo observacao {'ligado' if ligado else 'desligado'} "
            f"(a cada {intervalo} min) por {user.username}"
        ),
        user_id=user.id,
    )
    db.commit()

    mensagem = (
        f"Modo observacao ligado — o worker grava uma amostra a cada {intervalo} min."
        if ligado
        else "Modo observacao desligado."
    )
    return RedirectResponse(
        url="/dashboard/scanner?saved=" + quote(mensagem), status_code=303
    )


@router.get("/dashboard/mode", response_class=HTMLResponse)
def dashboard_mode(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    error: str | None = None,
    changed_to: str | None = None,
) -> HTMLResponse:
    current = get_current_mode(db)
    return templates.TemplateResponse(
        request,
        "dashboard/mode.html",
        {
            "user": user,
            "current_mode": current.value,
            "allowed_targets": [m.value for m in _allowed_targets(current)],
            "error": error,
            "changed_to": changed_to,
        },
    )


@router.post("/dashboard/mode", response_class=HTMLResponse)
def dashboard_mode_change(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    target_mode: str = Form(...),
    confirm_text: str = Form(...),
    reason: str = Form(""),
) -> RedirectResponse:
    try:
        target = SystemMode(target_mode)
    except ValueError:
        return RedirectResponse(
            url=f"/dashboard/mode?error={quote(f'modo desconhecido: {target_mode}')}",
            status_code=303,
        )

    if confirm_text.strip().upper() != target.value:
        return RedirectResponse(
            url=f"/dashboard/mode?error={quote('confirmacao nao bate com o modo escolhido — nada foi alterado.')}",
            status_code=303,
        )

    try:
        set_mode(
            db,
            target,
            reason=reason.strip() or f"definido via dashboard por {user.username}",
            user_id=user.id,
        )
    except SystemModeError as exc:
        db.rollback()
        return RedirectResponse(url=f"/dashboard/mode?error={quote(str(exc))}", status_code=303)

    db.commit()
    return RedirectResponse(url=f"/dashboard/mode?changed_to={target.value}", status_code=303)


def _mask_api_key(key: str) -> str:
    if len(key) <= 4:
        return "*" * len(key)
    return "*" * (len(key) - 4) + key[-4:]


_TRADING_START_ERRORS = {
    "symbol": "escolha uma moeda ja sincronizada com o MetaTrader.",
    "mode": "escolha DEMO ou REAL.",
    "worker": "o conector MT5 precisa estar online. Abra o terminal no Windows.",
    "account_demo": "modo DEMO escolhido, mas a conta conectada e REAL.",
    "account_real": "modo REAL escolhido, mas a conta conectada e demo.",
}


def _trading_payload(db: Session) -> dict:
    """Tudo o que a tela de operacao precisa, em uma consulta so.

    Fonte unica para a pagina, para o polling e para os widgets embutidos em
    outras telas — assim nenhuma delas mostra um estado diferente das
    outras.
    """
    config = load_trading_automation_config(db)
    status = load_autopilot_status(db)
    sync_status = load_sync_status(db)
    worker_online = heartbeat_is_fresh(sync_status)
    fresh = status.is_fresh()
    wants_real = config.mode == TRADING_MODE_REAL

    # Pronto para ligar? Cada bloqueio vira uma frase acionavel, nunca um
    # "indisponivel" sem motivo.
    blockers: list[str] = []
    if not worker_online:
        blockers.append(
            "Conector MT5 offline — abra o terminal no Windows e deixe o worker rodando."
        )
    elif not sync_status.connected:
        blockers.append("O terminal MT5 respondeu, mas nao esta conectado a corretora.")
    elif sync_status.account_is_demo is None:
        blockers.append("Aguardando o MT5 informar o tipo da conta.")
    elif sync_status.account_is_demo is wants_real:
        conectada = "demo" if sync_status.account_is_demo else "REAL"
        blockers.append(
            f"Modo {config.mode} escolhido, mas a conta conectada e {conectada}. "
            "Troque o modo ou conecte a conta certa."
        )

    if config.enabled:
        if not worker_online:
            headline = "Ligado, mas o conector do MetaTrader esta offline."
            detail = "Nenhuma ordem sera enviada ate o worker voltar."
        elif not fresh:
            headline = "Ligado, aguardando o primeiro ciclo do worker."
            detail = "O status ao vivo aparece assim que o worker publicar."
        else:
            headline = status.headline
            detail = status.detail
    else:
        headline = "Robo parado."
        detail = "Escolha a moeda, escolha DEMO ou REAL e clique em Comecar a operar."

    return {
        "config": config,
        "status": status,
        "blocker_stats": load_blocker_stats(db),
        "sync_status": sync_status,
        "worker_online": worker_online,
        "status_fresh": fresh,
        "headline": headline,
        "detail": detail,
        "working": config.enabled and worker_online and fresh and status.is_working,
        "wants_real": wants_real,
        "blockers": blockers,
        "ready_to_start": not blockers,
        "current_mode": get_current_mode(db).value,
    }


def _trading_json(payload: dict) -> dict:
    status: AutopilotStatus = payload["status"]
    config: TradingAutomationConfig = payload["config"]
    return {
        "enabled": config.enabled,
        "mode": config.mode,
        "symbol": config.symbol,
        "broker_symbol": status.broker_symbol,
        "engine": config.engine,
        "worker_online": payload["worker_online"],
        "status_fresh": payload["status_fresh"],
        "working": payload["working"],
        "ready_to_start": payload["ready_to_start"],
        "blockers": payload["blockers"],
        "headline": payload["headline"],
        "detail": payload["detail"],
        "phase": status.phase,
        "phase_label": status.phase_label,
        "phase_icon": status.phase_icon,
        "playbook_label": status.playbook_label,
        "playbook_description": status.playbook_description,
        "playbook_icon": status.playbook_icon,
        "timeframe": status.timeframe,
        "session_label": status.session_label,
        "active_sessions": status.active_sessions,
        "volume_label": status.volume_label,
        "analysis_score": status.analysis_score,
        "open_position": status.open_position,
        "stop_management": status.stop_management,
        "trades_today": status.trades_today,
        "pnl_today": status.pnl_today,
        "drawdown_pct": status.drawdown_pct,
        "halt_reason": status.halt_reason,
        "halt_detail": status.halt_detail,
        "updated_at": status.updated_at,
        "reasons": list(status.reasons),
        "activities": summarize_activities(status.activities),
        "day_cycles": payload["blocker_stats"].cycles,
        "day_blockers": [
            {
                "reason": item.reason,
                "count": item.count,
                "share": round(item.share * 100, 1),
            }
            for item in payload["blocker_stats"].reasons[:5]
        ],
    }


@router.get("/dashboard/trading", response_class=HTMLResponse)
def dashboard_trading(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Tela unica de operacao: escolher moeda, escolher DEMO/REAL, ligar.

    Substitui `/dashboard/autopilot` e `/dashboard/settings/trading`, que
    agora redirecionam para ca.
    """
    payload = _trading_payload(db)
    return templates.TemplateResponse(
        request,
        "dashboard/trading.html",
        {
            "user": user,
            **payload,
            "market_groups": grouped_availability(SymbolRepository(db).list_active()),
            "market_group_labels": GROUP_LABELS,
            "analysis_timeframes": [tf.value for tf in ANALYSIS_TIMEFRAMES],
            "saved": saved,
            "error": error,
        },
    )


@router.get("/dashboard/trading/status", response_class=JSONResponse)
def dashboard_trading_status(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> JSONResponse:
    return JSONResponse(_trading_json(_trading_payload(db)))


# Faixas dos limites de risco. Sao as MESMAS que a tela antiga de
# "Operacoes automaticas" aplicava: simplificar a tela nao pode afrouxar o
# que o operador tem permissao de configurar.
_TRADING_LIMIT_RULES: tuple[tuple[str, float, float, str], ...] = (
    ("analysis_threshold", 50.0, 100.0, "o score minimo deve ficar entre 50 e 100."),
    ("risk_per_trade_pct", 0.1, 1.0, "o risco por operacao deve ficar entre 0,1% e 1%."),
    ("max_daily_loss_pct", 0.5, 5.0, "a perda diaria maxima deve ficar entre 0,5% e 5%."),
    (
        "max_consecutive_losses",
        1,
        10,
        "o limite de perdas consecutivas deve ficar entre 1 e 10.",
    ),
    (
        "max_simultaneous_positions",
        1,
        3,
        "o limite de posicoes simultaneas deve ficar entre 1 e 3.",
    ),
    ("max_trades_per_day", 1, 50, "o limite diario de operacoes deve ficar entre 1 e 50."),
    (
        "min_seconds_between_trades",
        60,
        86_400,
        "o intervalo entre operacoes deve ficar entre 60 e 86400 segundos.",
    ),
    ("max_spread_points", 1.0, 500.0, "o spread maximo deve ficar entre 1 e 500 pontos."),
)


def _validate_trading_limits(values: dict) -> str | None:
    """Primeira faixa violada, em texto pronto para a tela — ou None."""
    for field, low, high, message in _TRADING_LIMIT_RULES:
        value = values.get(field)
        if value is not None and not low <= value <= high:
            return message
    return None


def _apply_trading_start(
    db: Session,
    *,
    user: User,
    symbol: str,
    mode: str,
    enabled: bool,
    redirect_to: str,
    limits: dict | None = None,
) -> RedirectResponse:
    """Liga/desliga o robo. Caminho unico, usado pela tela de operacao e
    pelos botoes embutidos em Dados de mercado / Analise PRO.

    Desligar nunca falha por pre-requisito: parar tem que funcionar sempre.
    """
    normalized_symbol = symbol.strip().upper()
    normalized_mode = mode.strip().upper()

    def fail(key: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"{redirect_to}?error={quote(_TRADING_START_ERRORS[key])}", status_code=303
        )

    if enabled:
        if normalized_mode not in TRADING_MODES:
            return fail("mode")
        available = {
            item.instrument.code
            for item in catalog_availability(SymbolRepository(db).list_active())
            if item.is_available
        }
        if normalized_symbol not in available:
            return fail("symbol")

        sync_status = load_sync_status(db)
        if not (heartbeat_is_fresh(sync_status) and sync_status.connected):
            return fail("worker")
        wants_real = normalized_mode == TRADING_MODE_REAL
        if sync_status.account_is_demo is wants_real:
            return fail("account_real" if wants_real else "account_demo")

        # A escada de modos e percorrida pelo sistema, nao pelo operador.
        activate_trading_mode(
            db,
            SystemMode.REAL_ENABLED if wants_real else SystemMode.DEMO,
            reason=f"inicio de operacao em {normalized_symbol}",
            user_id=user.id,
        )

    config = load_trading_automation_config(db)
    save_trading_automation_config(
        db,
        replace(
            config,
            enabled=enabled,
            autopilot=True,
            mode=normalized_mode if normalized_mode in TRADING_MODES else config.mode,
            symbol=normalized_symbol or config.symbol,
            **(limits or {}),
        ),
    )

    if enabled:
        sync_config = load_sync_config(db)
        updates = {}
        if normalized_symbol not in sync_config.symbols:
            # Ligar numa moeda fora do plano de coleta deixaria o robo sem
            # candles para sempre.
            updates["symbols"] = (*sync_config.symbols, normalized_symbol)
        if not sync_config.enabled:
            updates["enabled"] = True
        if updates:
            save_sync_config(db, replace(sync_config, **updates))

    AuditLogRepository(db).record(
        action="trading_start" if enabled else "trading_stop",
        entity="trading_automation",
        detail=(
            f"{'ligado' if enabled else 'desligado'} em {normalized_symbol} "
            f"modo={normalized_mode}"
        ),
        user_id=user.id,
    )
    db.commit()
    return RedirectResponse(url=f"{redirect_to}?saved=1", status_code=303)


@router.post("/dashboard/trading")
def dashboard_trading_save(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    symbol: str = Form(...),
    mode: str = Form(TRADING_MODE_DEMO),
    action: str = Form("start"),
    timeframe: str | None = Form(None),
    analysis_threshold: float | None = Form(None),
    risk_per_trade_pct: float | None = Form(None),
    max_daily_loss_pct: float | None = Form(None),
    max_consecutive_losses: int | None = Form(None),
    max_simultaneous_positions: int | None = Form(None),
    max_trades_per_day: int | None = Form(None),
    min_seconds_between_trades: int | None = Form(None),
    max_spread_points: float | None = Form(None),
) -> RedirectResponse:
    """Liga/desliga e, opcionalmente, ajusta os limites de risco.

    Os limites vivem no bloco avancado da mesma tela: quem so quer operar
    nao precisa toca-los, e quem precisa nao ficou sem lugar para faze-lo.
    """
    limits = {
        field: value
        for field, value in (
            ("analysis_threshold", analysis_threshold),
            ("risk_per_trade_pct", risk_per_trade_pct),
            ("max_daily_loss_pct", max_daily_loss_pct),
            ("max_consecutive_losses", max_consecutive_losses),
            ("max_simultaneous_positions", max_simultaneous_positions),
            ("max_trades_per_day", max_trades_per_day),
            ("min_seconds_between_trades", min_seconds_between_trades),
            ("max_spread_points", max_spread_points),
        )
        if value is not None
    }
    invalid = _validate_trading_limits(limits)
    if invalid is not None:
        return RedirectResponse(
            url=f"/dashboard/trading?error={quote(invalid)}", status_code=303
        )

    normalized_timeframe = (timeframe or "").strip().upper()
    if normalized_timeframe:
        if normalized_timeframe not in {item.value for item in ANALYSIS_TIMEFRAMES}:
            return RedirectResponse(
                url=f"/dashboard/trading?error={quote('timeframe de operacao invalido.')}",
                status_code=303,
            )
        limits["timeframe"] = normalized_timeframe

    return _apply_trading_start(
        db,
        user=user,
        symbol=symbol,
        mode=mode,
        enabled=action == "start",
        redirect_to="/dashboard/trading",
        limits=limits,
    )


@router.post("/dashboard/trading/quick")
def dashboard_trading_quick(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    symbol: str = Form(...),
    mode: str = Form(TRADING_MODE_DEMO),
    action: str = Form("start"),
    origin: str = Form("/dashboard/market-data"),
) -> RedirectResponse:
    """Mesmo caminho, chamado dos botoes de Dados de mercado / Analise PRO.

    `origin` volta para a tela de onde o operador clicou; qualquer valor
    fora da lista conhecida cai na tela de operacao, para que um parametro
    manipulado nao vire redirecionamento aberto.
    """
    allowed = {"/dashboard/market-data", "/dashboard/analysis", "/dashboard/trading"}
    return _apply_trading_start(
        db,
        user=user,
        symbol=symbol,
        mode=mode,
        enabled=action == "start",
        redirect_to=origin if origin in allowed else "/dashboard/trading",
    )


@router.get("/dashboard/autopilot", response_class=HTMLResponse)
def dashboard_autopilot_redirect() -> RedirectResponse:
    """Tela antiga do piloto — a configuracao agora vive em um lugar so."""
    return RedirectResponse(url="/dashboard/trading", status_code=307)


@router.get("/dashboard/settings/trading", response_class=HTMLResponse)
def dashboard_settings_trading_redirect() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/trading", status_code=307)


def _apexflow_payload(db: Session) -> dict:
    """Estado do ApexFlow para a pagina e para o polling.

    Metricas de desempenho vem do historico REAL de decisoes
    (`apexflow_decisions`) e ficam `None` ate haver amostra suficiente — o
    painel mostra "—", nunca um profit factor inventado com dois trades.
    """
    config = load_apexflow_config(db)
    automation = load_trading_automation_config(db)
    symbol_row = SymbolRepository(db).get_by_name(automation.symbol)
    symbol_id = symbol_row.id if symbol_row is not None else None

    repository = ApexFlowDecisionRepository(db)
    performance = repository.performance(symbol_id=symbol_id)
    recent = repository.list_recent(symbol_id=symbol_id, limit=15)
    latest = recent[0] if recent else None
    # Latencia, drawdown e estado de parada vivem no status ao vivo (o worker
    # os publica a cada ciclo), nao na tabela de decisoes: eles descrevem o
    # ROBO agora, nao uma decisao passada.
    status = load_autopilot_status(db)

    return {
        "apexflow_config": config,
        "automation": automation,
        "performance": performance,
        "recent_decisions": recent,
        "latest": latest,
        "status": status,
        "engine_active": automation.enabled and automation.engine == ENGINE_APEXFLOW,
    }


def _apexflow_json(payload: dict) -> dict:
    latest = payload["latest"]
    performance = payload["performance"]
    config = payload["apexflow_config"]
    status: AutopilotStatus = payload["status"]

    def optional(value) -> float | None:
        return None if value is None else float(value)

    return {
        "engine_active": payload["engine_active"],
        "min_confidence": config.min_confidence,
        "model_version": latest.model_version if latest else "",
        "decided_at": latest.decided_at.isoformat() if latest else None,
        "action": latest.action if latest else "",
        "probability_buy": optional(latest.probability_buy) if latest else None,
        "probability_sell": optional(latest.probability_sell) if latest else None,
        "probability_abstain": optional(latest.probability_abstain) if latest else None,
        "confidence": optional(latest.confidence) if latest else None,
        "context_state": latest.context_state if latest else "",
        "session_rating": latest.session_rating if latest else "",
        "volume_level": latest.volume_level if latest else "",
        "spread_points": optional(latest.spread_points) if latest else None,
        "atr_points": optional(latest.atr_points) if latest else None,
        "ticks_per_second": optional(latest.ticks_per_second) if latest else None,
        "mtf_alignment": optional(latest.mtf_alignment) if latest else None,
        "completeness": optional(latest.completeness) if latest else None,
        "total_decisions": performance.total_decisions,
        "entries": performance.entries,
        "abstentions": performance.abstentions,
        "abstention_rate": performance.abstention_rate,
        "closed_trades": performance.closed_trades,
        "has_statistics": performance.has_statistics,
        "win_rate": performance.win_rate,
        "profit_factor": performance.profit_factor,
        "expectancy": performance.expectancy,
        "net_pnl": performance.net_pnl,
        "latency_seconds": status.latency_seconds,
        "drawdown_pct": status.drawdown_pct,
        "equity": status.equity,
        "pnl_today": status.pnl_today,
        "trades_today": status.trades_today,
        "halt_reason": status.halt_reason,
        "halt_detail": status.halt_detail,
        "stop_management": status.stop_management,
        "robot_phase": status.phase_label,
        "robot_working": status.enabled and status.is_fresh() and status.is_working,
        "session_label": status.session_label,
        "decisions": [
            {
                "decided_at": record.decided_at.isoformat(),
                "action": record.action,
                "confidence": float(record.confidence),
                "context_state": record.context_state,
                "net_pnl": (
                    None if record.result_net_pnl is None else float(record.result_net_pnl)
                ),
            }
            for record in payload["recent_decisions"]
        ],
    }


@router.get("/dashboard/apexflow", response_class=HTMLResponse)
def dashboard_apexflow(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    payload = _apexflow_payload(db)
    return templates.TemplateResponse(
        request,
        "dashboard/apexflow.html",
        {"user": user, **payload, "saved": saved, "error": error},
    )


@router.get("/dashboard/apexflow/status", response_class=JSONResponse)
def dashboard_apexflow_status(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> JSONResponse:
    return JSONResponse(_apexflow_json(_apexflow_payload(db)))


@router.post("/dashboard/apexflow")
def dashboard_apexflow_save(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    min_confidence: float = Form(...),
    min_atr_points: float = Form(...),
    max_spread_points: float = Form(...),
    risk_reward_min: float = Form(...),
    daily_profit_target_pct: float = Form(...),
    max_drawdown_pct: float = Form(...),
    tick_window_seconds: int = Form(...),
    use_engine: str = Form(""),
) -> RedirectResponse:
    """Salva os parametros do motor e escolhe qual cerebro decide.

    NAO liga a automacao: ligar continua exigindo os portoes de
    `/dashboard/autopilot` (confirmacao digitada, modo DEMO, conta demo).
    Trocar de motor com o robo desligado nunca envia ordem.
    """
    validations = (
        (0.50 <= min_confidence <= 0.99, "a confianca minima deve ficar entre 50% e 99%."),
        (1.0 <= min_atr_points <= 10_000.0, "o ATR minimo deve ficar entre 1 e 10000 pontos."),
        (1.0 <= max_spread_points <= 500.0, "o spread maximo deve ficar entre 1 e 500 pontos."),
        (1.0 <= risk_reward_min <= 10.0, "o risco/retorno minimo deve ficar entre 1 e 10."),
        (
            0.5 <= daily_profit_target_pct <= 20.0,
            "a meta diaria de lucro deve ficar entre 0,5% e 20%.",
        ),
        (1.0 <= max_drawdown_pct <= 30.0, "o drawdown maximo deve ficar entre 1% e 30%."),
        (
            10 <= tick_window_seconds <= 3_600,
            "a janela de ticks deve ficar entre 10 e 3600 segundos.",
        ),
    )
    for valid, message in validations:
        if not valid:
            return RedirectResponse(
                url=f"/dashboard/apexflow?error={quote(message)}", status_code=303
            )

    save_apexflow_config(
        db,
        replace(
            load_apexflow_config(db),
            enabled=bool(use_engine),
            min_confidence=min_confidence,
            min_atr_points=min_atr_points,
            max_spread_points=max_spread_points,
            risk_reward_min=risk_reward_min,
            daily_profit_target_pct=daily_profit_target_pct,
            max_drawdown_pct=max_drawdown_pct,
            tick_window_seconds=tick_window_seconds,
        ),
    )
    save_trading_automation_config(
        db,
        replace(
            load_trading_automation_config(db),
            engine=ENGINE_APEXFLOW if use_engine else ENGINE_PLAYBOOK,
        ),
    )
    AuditLogRepository(db).record(
        action="apexflow_config_change",
        entity="apexflow",
        detail=(
            f"motor={'apexflow' if use_engine else 'playbook'} "
            f"confianca_minima={min_confidence:.2f}"
        ),
        user_id=user.id,
    )
    db.commit()
    return RedirectResponse(url="/dashboard/apexflow?saved=1", status_code=303)


@router.get("/dashboard/settings", response_class=HTMLResponse)
def dashboard_settings_hub(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Indice de tudo que se configura, com o estado de cada area.

    A configuracao continua morando em telas separadas — juntar tudo em um
    formulario gigante trocaria "nao acho" por "nao entendo". O que faltava
    era um lugar que respondesse "onde fica X?" e, de quebra, mostrasse o
    que ja esta configurado e o que ainda falta.
    """
    from app.calendar_feed.factory import get_calendar_provider

    settings = get_settings()
    repo = SystemSettingRepository(db)
    trading = load_trading_automation_config(db)
    sync_config = load_sync_config(db)
    sync_status = load_sync_status(db)
    observacao = load_observation_config(db)
    api = load_api_settings(db, settings)
    chave = repo.get(AISA_API_KEY_SETTING) or settings.aisa_api_key
    calendario = get_calendar_provider(settings).fetch_events(
        now=datetime.now(UTC), horizon_minutes=120
    )

    return templates.TemplateResponse(
        request,
        "dashboard/settings.html",
        {
            "user": user,
            "trading": trading,
            "worker_online": heartbeat_is_fresh(sync_status),
            "sync_enabled": sync_config.enabled,
            "sync_connected": sync_status.connected,
            "api_configured": bool(chave),
            "api_budget": get_budget_usage(db, settings),
            "api_settings": api,
            "observation": observacao,
            "calendar_status": calendario.status.value,
            "calendar_message": calendario.message,
            "current_mode": get_current_mode(db).value,
            "broker": settings.broker,
            "calendar_file": settings.calendar_file_path or "",
        },
    )


@router.get("/dashboard/settings/aisa", response_class=HTMLResponse)
def dashboard_settings_aisa(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    settings = get_settings()
    api = load_api_settings(db, settings)
    cache = get_assessment_cache(settings, ttl_seconds=api.cache_ttl_seconds)
    repo = SystemSettingRepository(db)
    persisted_key = repo.get(AISA_API_KEY_SETTING)
    persisted_base_url = repo.get(AISA_API_BASE_URL_SETTING)

    effective_key = persisted_key or settings.aisa_api_key
    key_source = "dashboard" if persisted_key else ("env" if settings.aisa_api_key else None)
    effective_base_url = persisted_base_url or settings.aisa_api_base_url

    return templates.TemplateResponse(
        request,
        "dashboard/settings_aisa.html",
        {
            "user": user,
            "masked_key": _mask_api_key(effective_key) if effective_key else None,
            "key_source": key_source,
            "base_url": effective_base_url or "",
            "cache_ttl_seconds": api.cache_ttl_seconds,
            "cache_hits": cache.hits,
            "cache_misses": cache.misses,
            "budget": get_budget_usage(db, settings),
            "api_settings": api,
            # Mais recente primeiro: quem abre a tela quer saber o que
            # acabou de gastar cota, nao o que gastou semana passada.
            "calls": list(reversed(load_calls(db)))[:40],
            "call_summary": summarize_calls(db),
            "budget_min": BUDGET_MIN,
            "budget_max": BUDGET_MAX,
            "ttl_min": TTL_MIN,
            "ttl_max": TTL_MAX,
            "saved": saved,
            "error": error,
        },
    )


@router.post("/dashboard/settings/aisa/test")
def dashboard_settings_aisa_test(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    symbol: str = Form("EURUSD"),
) -> RedirectResponse:
    """Faz UMA consulta real de cada tipo e devolve o erro cru.

    Consome cota — duas chamadas. Um teste que nao gasta nao prova que a
    chave gasta, e descobrir que a credencial nao funciona custa menos que
    descobrir isso no meio de um pregao.
    """
    settings = get_settings()
    repo = SystemSettingRepository(db)
    chave = repo.get(AISA_API_KEY_SETTING) or settings.aisa_api_key
    if not chave:
        return RedirectResponse(
            url="/dashboard/settings/aisa?error="
            + quote("configure a chave antes de testar."),
            status_code=303,
        )

    resultado = probe_api(
        db,
        settings,
        api_key=chave,
        base_url=repo.get(AISA_API_BASE_URL_SETTING) or settings.aisa_api_base_url,
        symbol=(symbol or "EURUSD").strip().upper()[:20],
    )
    AuditLogRepository(db).record(
        action="aisa_probe",
        entity="aisa_api",
        detail=f"teste de conexao ({resultado.symbol}) por {user.username}",
        user_id=user.id,
    )
    db.commit()

    linhas = " | ".join(
        f"{item.kind}: {item.status} — {item.message}" for item in resultado.outcomes
    )
    campo = "saved" if resultado.ok else "error"
    return RedirectResponse(
        url=f"/dashboard/settings/aisa?{campo}={quote(linhas[:900])}", status_code=303
    )


@router.post("/dashboard/settings/aisa", response_class=HTMLResponse)
def dashboard_settings_aisa_save(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    api_key: str = Form(""),
    api_base_url: str = Form(""),
    remove_key: str = Form(""),
    daily_budget: int | None = Form(None),
    cache_ttl_seconds: int | None = Form(None),
) -> RedirectResponse:
    repo = SystemSettingRepository(db)
    changes: list[str] = []

    problema = validate_api_settings(
        daily_budget=daily_budget, cache_ttl_seconds=cache_ttl_seconds
    )
    if problema:
        db.rollback()
        return RedirectResponse(
            url=f"/dashboard/settings/aisa?error={quote(problema)}", status_code=303
        )

    if remove_key:
        repo.set(
            AISA_API_KEY_SETTING,
            "",
            description="Chave da API AIsa (Fase 18.6) — removida via dashboard.",
        )
        changes.append("chave removida")
    elif api_key.strip():
        repo.set(
            AISA_API_KEY_SETTING,
            api_key.strip(),
            description="Chave da API AIsa (Fase 18.6) — configurada via dashboard.",
        )
        changes.append("chave atualizada")

    if api_base_url.strip():
        repo.set(
            AISA_API_BASE_URL_SETTING,
            api_base_url.strip(),
            description="URL base da API AIsa (Fase 18.6) — configurada via dashboard.",
        )
        changes.append("URL base atualizada")

    changes.extend(
        save_api_settings(
            db, daily_budget=daily_budget, cache_ttl_seconds=cache_ttl_seconds
        )
    )

    if not changes:
        db.rollback()
        return RedirectResponse(
            url=f"/dashboard/settings/aisa?error={quote('nada foi alterado — preencha ao menos um campo.')}",
            status_code=303,
        )

    AuditLogRepository(db).record(
        action="aisa_settings_change",
        entity="aisa_api",
        detail=f"{', '.join(changes)} por {user.username} (chave nunca gravada no log de auditoria)",
        user_id=user.id,
    )
    db.commit()
    # Trocar a credencial invalida o que ja estava guardado: a proxima
    # analise tem que falar com a API nova, nao repetir a resposta da antiga.
    reset_assessment_cache()
    return RedirectResponse(url="/dashboard/settings/aisa?saved=1", status_code=303)
