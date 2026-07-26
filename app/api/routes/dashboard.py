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
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user_for_web
from app.api.templates_engine import templates
from app.core.config import get_settings
from app.core.enums import SystemMode
from app.core.system_mode import SystemModeError, validate_transition
from app.database.models.user import User
from app.database.repositories.audit_log_repository import AuditLogRepository
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.drift_event_repository import DriftEventRepository
from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.paper_trade_repository import PaperTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.system_setting_repository import (
    SystemSettingRepository,
    get_current_mode,
    set_mode,
)
from app.database.repositories.tick_repository import TickRepository
from app.database.session import get_db
from app.execution.automation_settings import (
    TradingAutomationConfig,
    load_trading_automation_config,
    save_trading_automation_config,
)
from app.execution.autopilot_status import (
    AutopilotStatus,
    load_autopilot_status,
    summarize_activities,
)
from app.market.catalog import (
    GROUP_LABELS,
    MARKET_CATALOG,
    catalog_availability,
    grouped_availability,
)
from app.market.multi_timeframe import ANALYSIS_TIMEFRAMES, SymbolNotFoundError
from app.ml.registry import ModelRegistry
from app.mt5.market_data import Timeframe
from app.mt5.sync_settings import (
    heartbeat_is_fresh,
    load_sync_config,
    load_sync_status,
    save_sync_config,
)
from app.news.factory import AISA_API_BASE_URL_SETTING, AISA_API_KEY_SETTING
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
) -> dict:
    resolved_operation_config = operation_config or TradingAutomationConfig()
    return {
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
) -> RedirectResponse:
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
                url=f"/dashboard/mt5?error={quote('ative a sincronizacao antes de atualizar agora.')}",
                status_code=303,
            )
        updated = replace(config, sync_request_id=uuid4().hex)
        message = "Sincronizacao imediata solicitada"
    elif action == "test":
        updated = replace(config, test_request_id=uuid4().hex)
        message = "Teste de conexao solicitado"
    else:
        return RedirectResponse(
            url=f"/dashboard/mt5?error={quote('acao MT5 desconhecida.')}",
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
    return RedirectResponse(url=f"/dashboard/mt5?action={quote(message)}", status_code=303)


@router.get("/dashboard/analysis", response_class=HTMLResponse)
def dashboard_analysis(
    request: Request,
    symbol: str | None = None,
    timeframe: str = "M15",
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Workbench consultivo: seleciona um simbolo real e aplica score >= 90."""
    settings = get_settings()
    symbols = SymbolRepository(db).list_active()
    persisted_key = SystemSettingRepository(db).get(AISA_API_KEY_SETTING)
    aisa_configured = bool(persisted_key or settings.aisa_api_key)
    operation_config = load_trading_automation_config(db)

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
            ),
            status_code=404,
        )

    try:
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
) -> HTMLResponse:
    candle_summary = CandleRepository(db).summary()
    tick_summary = TickRepository(db).summary()
    unique_symbols = {row[0] for row in candle_summary} | {row[0] for row in tick_summary}
    return templates.TemplateResponse(
        request,
        "dashboard/market_data.html",
        {
            "user": user,
            "candle_summary": candle_summary,
            "tick_summary": tick_summary,
            "symbol_count": len(unique_symbols),
            "candle_count": sum(row[2] for row in candle_summary),
            "tick_count": sum(row[1] for row in tick_summary),
            "timeframe_count": len({row[1] for row in candle_summary}),
        },
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


_AUTOPILOT_READY_MESSAGE = (
    "Escolha a moeda e ligue: o robo decide o operacional pelo horario e pelo volume."
)


def _autopilot_payload(db: Session) -> dict:
    """Estado do piloto para a pagina e para o polling JSON — mesma fonte,
    para que a tela nunca discorde de si mesma entre um refresh e outro."""
    config = load_trading_automation_config(db)
    status = load_autopilot_status(db)
    sync_status = load_sync_status(db)
    worker_online = heartbeat_is_fresh(sync_status)
    fresh = status.is_fresh()

    if not config.enabled:
        headline = "Piloto automatico desligado."
        detail = _AUTOPILOT_READY_MESSAGE
    elif not worker_online:
        headline = "Ligado, mas o conector do MetaTrader esta offline."
        detail = "Abra o terminal MT5 no Windows e mantenha o worker rodando."
    elif not fresh:
        headline = "Ligado, aguardando o primeiro ciclo do worker."
        detail = (
            "O status ao vivo aparece assim que o worker publicar — se demorar, "
            "confira os logs do worker."
        )
    else:
        headline = status.headline
        detail = status.detail

    return {
        "config": config,
        "status": status,
        "worker_online": worker_online,
        "status_fresh": fresh,
        "headline": headline,
        "detail": detail,
        "working": config.enabled and worker_online and fresh and status.is_working,
        "current_mode": get_current_mode(db).value,
    }


def _autopilot_json(payload: dict) -> dict:
    status: AutopilotStatus = payload["status"]
    config: TradingAutomationConfig = payload["config"]
    return {
        "enabled": config.enabled,
        "autopilot": config.autopilot,
        "symbol": config.symbol,
        "broker_symbol": status.broker_symbol,
        "worker_online": payload["worker_online"],
        "status_fresh": payload["status_fresh"],
        "working": payload["working"],
        "current_mode": payload["current_mode"],
        "phase": status.phase,
        "phase_label": status.phase_label,
        "phase_icon": status.phase_icon,
        "headline": payload["headline"],
        "detail": payload["detail"],
        "playbook_label": status.playbook_label,
        "playbook_description": status.playbook_description,
        "playbook_icon": status.playbook_icon,
        "timeframe": status.timeframe,
        "fit_score": status.fit_score,
        "analysis_score": status.analysis_score,
        "analysis_threshold": status.analysis_threshold,
        "analysis_recommendation": status.analysis_recommendation,
        "risk_factor": status.risk_factor,
        "session_label": status.session_label,
        "session_rating": status.session_rating,
        "active_sessions": status.active_sessions,
        "volume_label": status.volume_label,
        "volume_level": status.volume_level,
        "volume_ratio": status.volume_ratio,
        "trend": status.trend,
        "volatility": status.volatility,
        "open_position": status.open_position,
        "trades_today": status.trades_today,
        "pnl_today": status.pnl_today,
        "cycles": status.cycles,
        "updated_at": status.updated_at,
        "reasons": list(status.reasons),
        "blockers": list(status.blockers),
        "last_error": status.last_error,
        "activities": summarize_activities(status.activities),
    }


@router.get("/dashboard/autopilot", response_class=HTMLResponse)
def dashboard_autopilot(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    payload = _autopilot_payload(db)
    symbols = SymbolRepository(db).list_active()
    return templates.TemplateResponse(
        request,
        "dashboard/autopilot.html",
        {
            "user": user,
            **payload,
            "market_groups": grouped_availability(symbols),
            "market_group_labels": GROUP_LABELS,
            "saved": saved,
            "error": error,
        },
    )


@router.get("/dashboard/autopilot/status", response_class=JSONResponse)
def dashboard_autopilot_status(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Consumido pelo polling da pagina — o painel mostra o que o robo esta
    fazendo sem recarregar a tela."""
    return JSONResponse(_autopilot_json(_autopilot_payload(db)))


@router.post("/dashboard/autopilot")
def dashboard_autopilot_save(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    symbol: str = Form(...),
    enabled: str = Form(""),
    confirm_text: str = Form(""),
) -> RedirectResponse:
    """Liga/desliga o piloto e define a moeda.

    Reusa exatamente os mesmos portoes de `/dashboard/settings/trading`
    (confirmacao digitada, modo DEMO, worker online, conta demo) — a tela
    simplificada nao pode ser um caminho mais permissivo para chegar ao
    mesmo lugar.
    """
    normalized_symbol = symbol.strip().upper()
    requested_enabled = bool(enabled)
    config = load_trading_automation_config(db)

    available = {
        availability.instrument.code
        for availability in catalog_availability(SymbolRepository(db).list_active())
        if availability.is_available
    }
    if normalized_symbol not in available:
        return RedirectResponse(
            url=(
                "/dashboard/autopilot?error="
                f"{quote('escolha uma moeda ja sincronizada com o MetaTrader.')}"
            ),
            status_code=303,
        )

    if requested_enabled:
        sync_status = load_sync_status(db)
        if confirm_text.strip().upper() != "DEMO":
            return RedirectResponse(
                url=(
                    "/dashboard/autopilot?error="
                    f"{quote('digite DEMO para ligar o piloto automatico.')}"
                ),
                status_code=303,
            )
        if get_current_mode(db) != SystemMode.DEMO:
            return RedirectResponse(
                url=(
                    "/dashboard/autopilot?error="
                    f"{quote('altere primeiro o modo operacional para DEMO.')}"
                ),
                status_code=303,
            )
        if not (
            heartbeat_is_fresh(sync_status)
            and sync_status.connected
            and sync_status.account_is_demo is True
        ):
            return RedirectResponse(
                url=(
                    "/dashboard/autopilot?error="
                    f"{quote('conecte e teste uma conta MT5 demo antes de ligar.')}"
                ),
                status_code=303,
            )

    updated = replace(
        config,
        enabled=requested_enabled,
        autopilot=True,
        symbol=normalized_symbol,
    )
    save_trading_automation_config(db, updated)

    sync_config = load_sync_config(db)
    if requested_enabled and normalized_symbol not in sync_config.symbols:
        # Ligar o piloto em uma moeda fora do plano de coleta deixaria o
        # robo sem candles para sempre — inclui a moeda em vez de falhar
        # silenciosamente depois.
        save_sync_config(
            db, replace(sync_config, symbols=(*sync_config.symbols, normalized_symbol))
        )
    if requested_enabled and not sync_config.enabled:
        save_sync_config(db, replace(load_sync_config(db), enabled=True))

    AuditLogRepository(db).record(
        action="autopilot_toggle",
        entity="trading_automation",
        detail=(
            f"piloto automatico {'ligado' if requested_enabled else 'desligado'} "
            f"em {normalized_symbol}"
        ),
        user_id=user.id,
    )
    db.commit()
    return RedirectResponse(url="/dashboard/autopilot?saved=1", status_code=303)


@router.get("/dashboard/settings/trading", response_class=HTMLResponse)
def dashboard_settings_trading(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    config = load_trading_automation_config(db)
    sync_config = load_sync_config(db)
    sync_status = load_sync_status(db)
    return templates.TemplateResponse(
        request,
        "dashboard/settings_trading.html",
        {
            "user": user,
            "config": config,
            "sync_config": sync_config,
            "status": sync_status,
            "worker_online": heartbeat_is_fresh(sync_status),
            "current_mode": get_current_mode(db).value,
            "analysis_timeframes": [timeframe.value for timeframe in ANALYSIS_TIMEFRAMES],
            "saved": saved,
            "error": error,
        },
    )


@router.post("/dashboard/settings/trading")
def dashboard_settings_trading_save(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    symbol: str = Form(...),
    timeframe: str = Form(...),
    analysis_threshold: float = Form(...),
    risk_per_trade_pct: float = Form(...),
    max_daily_loss_pct: float = Form(...),
    max_consecutive_losses: int = Form(...),
    max_simultaneous_positions: int = Form(...),
    max_trades_per_day: int = Form(...),
    min_seconds_between_trades: int = Form(...),
    max_spread_points: float = Form(...),
    enabled: str = Form(""),
    confirm_text: str = Form(""),
) -> RedirectResponse:
    normalized_symbol = symbol.strip().upper()
    normalized_timeframe = timeframe.strip().upper()
    requested_enabled = bool(enabled)
    sync_config = load_sync_config(db)

    validations = (
        (normalized_symbol in sync_config.symbols, "selecione um ativo do plano MT5."),
        (
            normalized_timeframe in {item.value for item in ANALYSIS_TIMEFRAMES},
            "timeframe de operacao invalido.",
        ),
        (50.0 <= analysis_threshold <= 100.0, "o score minimo deve ficar entre 50 e 100."),
        (
            0.1 <= risk_per_trade_pct <= 1.0,
            "o risco por operacao deve ficar entre 0,1% e 1%.",
        ),
        (
            0.5 <= max_daily_loss_pct <= 5.0,
            "a perda diaria maxima deve ficar entre 0,5% e 5%.",
        ),
        (
            1 <= max_consecutive_losses <= 10,
            "o limite de perdas consecutivas deve ficar entre 1 e 10.",
        ),
        (
            1 <= max_simultaneous_positions <= 3,
            "o limite de posicoes simultaneas deve ficar entre 1 e 3.",
        ),
        (
            1 <= max_trades_per_day <= 50,
            "o limite diario de operacoes deve ficar entre 1 e 50.",
        ),
        (
            60 <= min_seconds_between_trades <= 86_400,
            "o intervalo entre operacoes deve ficar entre 60 e 86400 segundos.",
        ),
        (
            1.0 <= max_spread_points <= 500.0,
            "o spread maximo deve ficar entre 1 e 500 pontos.",
        ),
    )
    for valid, message in validations:
        if not valid:
            return RedirectResponse(
                url=f"/dashboard/settings/trading?error={quote(message)}",
                status_code=303,
            )

    if requested_enabled:
        status = load_sync_status(db)
        if confirm_text.strip().upper() != "DEMO":
            return RedirectResponse(
                url=(
                    "/dashboard/settings/trading?error="
                    f"{quote('digite DEMO para ativar a automacao.')}"
                ),
                status_code=303,
            )
        if get_current_mode(db) != SystemMode.DEMO:
            return RedirectResponse(
                url=(
                    "/dashboard/settings/trading?error="
                    f"{quote('altere primeiro o modo operacional para DEMO.')}"
                ),
                status_code=303,
            )
        if not (
            heartbeat_is_fresh(status)
            and status.connected
            and status.account_is_demo is True
        ):
            return RedirectResponse(
                url=(
                    "/dashboard/settings/trading?error="
                    f"{quote('conecte e teste uma conta MT5 demo antes de ativar.')}"
                ),
                status_code=303,
            )

    updated = TradingAutomationConfig(
        enabled=requested_enabled,
        symbol=normalized_symbol,
        timeframe=normalized_timeframe,
        analysis_threshold=analysis_threshold,
        risk_per_trade_pct=risk_per_trade_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_consecutive_losses=max_consecutive_losses,
        max_simultaneous_positions=max_simultaneous_positions,
        max_trades_per_day=max_trades_per_day,
        min_seconds_between_trades=min_seconds_between_trades,
        max_spread_points=max_spread_points,
    )
    save_trading_automation_config(db, updated)

    if requested_enabled and not sync_config.enabled:
        save_sync_config(
            db,
            replace(sync_config, enabled=True, sync_request_id=uuid4().hex),
        )

    AuditLogRepository(db).record(
        action="trading_automation_settings_change",
        entity="trading_automation",
        detail=(
            f"{'ativada' if requested_enabled else 'desativada'} para "
            f"{normalized_symbol}/{normalized_timeframe}; score minimo "
            f"{analysis_threshold:.1f}; risco {risk_per_trade_pct:.2f}%; "
            f"maximo {max_trades_per_day} operacoes/dia por {user.username}"
        ),
        user_id=user.id,
    )
    db.commit()
    return RedirectResponse(url="/dashboard/settings/trading?saved=1", status_code=303)


@router.get("/dashboard/settings/aisa", response_class=HTMLResponse)
def dashboard_settings_aisa(
    request: Request,
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    settings = get_settings()
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
            "saved": saved,
            "error": error,
        },
    )


@router.post("/dashboard/settings/aisa", response_class=HTMLResponse)
def dashboard_settings_aisa_save(
    user: User = Depends(get_current_user_for_web),
    db: Session = Depends(get_db),
    api_key: str = Form(""),
    api_base_url: str = Form(""),
    remove_key: str = Form(""),
) -> RedirectResponse:
    repo = SystemSettingRepository(db)
    changes: list[str] = []

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
    return RedirectResponse(url="/dashboard/settings/aisa?saved=1", status_code=303)
