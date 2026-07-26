"""Status ao vivo do piloto automatico — o que o robo esta fazendo agora.

O worker que opera roda no Windows (junto do terminal MetaTrader) e o
dashboard roda em outro processo, normalmente em outra maquina. O banco ja
compartilhado entre os dois e o canal: o worker PUBLICA cada fase do seu
raciocinio aqui, o dashboard LE e mostra. Mesma mecanica ja usada por
`app.mt5.sync_settings` para o heartbeat do conector.

O status nunca inventa progresso: cada fase e publicada no momento em que
comeca de verdade, e o feed de atividades registra so o que aconteceu. Se
o worker cair, o `updated_at` para de avancar e o dashboard mostra o status
como desatualizado em vez de fingir que o robo continua trabalhando.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository

AUTOPILOT_STATUS_KEY = "autopilot_status"

MAX_ACTIVITIES = 20
MAX_MESSAGE_CHARS = 240
MAX_LIST_ITEMS = 6
STALE_AFTER_SECONDS = 90


class AutopilotPhase(enum.StrEnum):
    OFF = "OFF"
    STARTING = "STARTING"
    READING_MARKET = "READING_MARKET"
    CHOOSING_PLAYBOOK = "CHOOSING_PLAYBOOK"
    ANALYZING = "ANALYZING"
    WAITING_TRIGGER = "WAITING_TRIGGER"
    RISK_CHECK = "RISK_CHECK"
    SENDING_ORDER = "SENDING_ORDER"
    POSITION_OPEN = "POSITION_OPEN"
    STANDING_ASIDE = "STANDING_ASIDE"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


PHASE_LABELS: dict[AutopilotPhase, str] = {
    AutopilotPhase.OFF: "Desligado",
    AutopilotPhase.STARTING: "Iniciando",
    AutopilotPhase.READING_MARKET: "Lendo o mercado",
    AutopilotPhase.CHOOSING_PLAYBOOK: "Escolhendo o operacional",
    AutopilotPhase.ANALYZING: "Analisando a oportunidade",
    AutopilotPhase.WAITING_TRIGGER: "Aguardando o gatilho",
    AutopilotPhase.RISK_CHECK: "Conferindo o risco",
    AutopilotPhase.SENDING_ORDER: "Enviando a ordem",
    AutopilotPhase.POSITION_OPEN: "Operacao aberta",
    AutopilotPhase.STANDING_ASIDE: "Fora do mercado",
    AutopilotPhase.BLOCKED: "Bloqueado",
    AutopilotPhase.ERROR: "Erro",
}

PHASE_ICONS: dict[AutopilotPhase, str] = {
    AutopilotPhase.OFF: "bi-power",
    AutopilotPhase.STARTING: "bi-hourglass-split",
    AutopilotPhase.READING_MARKET: "bi-binoculars",
    AutopilotPhase.CHOOSING_PLAYBOOK: "bi-diagram-3",
    AutopilotPhase.ANALYZING: "bi-search",
    AutopilotPhase.WAITING_TRIGGER: "bi-stopwatch",
    AutopilotPhase.RISK_CHECK: "bi-shield-check",
    AutopilotPhase.SENDING_ORDER: "bi-send",
    AutopilotPhase.POSITION_OPEN: "bi-broadcast-pin",
    AutopilotPhase.STANDING_ASIDE: "bi-pause-circle",
    AutopilotPhase.BLOCKED: "bi-slash-circle",
    AutopilotPhase.ERROR: "bi-exclamation-octagon",
}

WORKING_PHASES: frozenset[AutopilotPhase] = frozenset(
    {
        AutopilotPhase.STARTING,
        AutopilotPhase.READING_MARKET,
        AutopilotPhase.CHOOSING_PLAYBOOK,
        AutopilotPhase.ANALYZING,
        AutopilotPhase.WAITING_TRIGGER,
        AutopilotPhase.RISK_CHECK,
        AutopilotPhase.SENDING_ORDER,
        AutopilotPhase.POSITION_OPEN,
    }
)
"""Fases em que o robo esta de fato trabalhando (indicador pulsando no
dashboard). `STANDING_ASIDE` e uma decisao consciente, nao trabalho em
andamento; `BLOCKED`/`ERROR` exigem acao humana."""


class ActivityLevel(enum.StrEnum):
    INFO = "INFO"
    GOOD = "GOOD"
    WARN = "WARN"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class AutopilotActivity:
    at: str
    phase: str
    message: str
    level: str = ActivityLevel.INFO.value


@dataclass(frozen=True, slots=True)
class AutopilotStatus:
    """Tudo o que o dashboard precisa para explicar o robo sem adivinhar."""

    enabled: bool = False
    phase: str = AutopilotPhase.OFF.value
    headline: str = "Piloto automatico desligado."
    detail: str = ""
    symbol: str = ""
    broker_symbol: str = ""
    timeframe: str = ""
    playbook_kind: str = ""
    playbook_label: str = ""
    playbook_description: str = ""
    playbook_icon: str = "bi-pause-circle"
    fit_score: float | None = None
    analysis_threshold: float | None = None
    analysis_score: float | None = None
    analysis_recommendation: str = ""
    risk_factor: float | None = None
    session_rating: str = ""
    session_label: str = ""
    active_sessions: str = ""
    volume_level: str = ""
    volume_label: str = ""
    volume_ratio: float | None = None
    trend: str = ""
    volatility: str = ""
    open_position: str = ""
    stop_management: str = ""
    """Ultimo resultado do gerenciamento de stop (trailing/break-even)."""

    trades_today: int = 0
    pnl_today: float = 0.0
    equity: float | None = None
    peak_equity: float | None = None
    peak_equity_day: str = ""
    """Dia (UTC) a que o pico se refere. O drawdown medido e INTRADIARIO:
    sem esta data, um pico de semanas atras bloquearia o robo para sempre."""

    drawdown_pct: float | None = None
    latency_seconds: float | None = None
    halt_reason: str = ""
    halt_detail: str = ""
    cycles: int = 0
    last_cycle_at: str | None = None
    updated_at: str | None = None
    worker_id: str = ""
    reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    last_error: str = ""
    activities: tuple[AutopilotActivity, ...] = field(default_factory=tuple)

    @property
    def phase_enum(self) -> AutopilotPhase:
        try:
            return AutopilotPhase(self.phase)
        except ValueError:
            return AutopilotPhase.OFF

    @property
    def phase_label(self) -> str:
        return PHASE_LABELS[self.phase_enum]

    @property
    def phase_icon(self) -> str:
        return PHASE_ICONS[self.phase_enum]

    @property
    def is_working(self) -> bool:
        return self.enabled and self.phase_enum in WORKING_PHASES

    @property
    def is_halted(self) -> bool:
        return bool(self.halt_reason) and self.halt_reason != "NONE"

    def is_fresh(self, *, now: datetime | None = None) -> bool:
        """`False` quando o worker parou de publicar — o dashboard mostra o
        status como desatualizado em vez de sugerir que o robo trabalha."""
        if not self.updated_at:
            return False
        try:
            stamp = datetime.fromisoformat(self.updated_at)
        except ValueError:
            return False
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        return (reference - stamp).total_seconds() <= STALE_AFTER_SECONDS


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: object, limit: int = MAX_MESSAGE_CHARS) -> str:
    return str(value)[:limit] if value is not None else ""


def _clip_list(values: object, *, limit: int = MAX_LIST_ITEMS) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(_clip(item) for item in values[:limit])


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _coerce_activities(raw: object) -> tuple[AutopilotActivity, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[AutopilotActivity] = []
    for item in raw[-MAX_ACTIVITIES:]:
        if not isinstance(item, dict):
            continue
        result.append(
            AutopilotActivity(
                at=_clip(item.get("at"), 40),
                phase=_clip(item.get("phase"), 32),
                message=_clip(item.get("message")),
                level=_clip(item.get("level"), 8) or ActivityLevel.INFO.value,
            )
        )
    return tuple(result)


def load_autopilot_status(session: Session) -> AutopilotStatus:
    raw = SystemSettingRepository(session).get(AUTOPILOT_STATUS_KEY)
    if not raw:
        return AutopilotStatus()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return AutopilotStatus()
    if not isinstance(data, dict):
        return AutopilotStatus()

    defaults = AutopilotStatus()
    return AutopilotStatus(
        enabled=bool(data.get("enabled", defaults.enabled)),
        phase=_clip(data.get("phase", defaults.phase), 32),
        headline=_clip(data.get("headline", defaults.headline)),
        detail=_clip(data.get("detail", defaults.detail)),
        symbol=_clip(data.get("symbol", ""), 32),
        broker_symbol=_clip(data.get("broker_symbol", ""), 32),
        timeframe=_clip(data.get("timeframe", ""), 8),
        playbook_kind=_clip(data.get("playbook_kind", ""), 32),
        playbook_label=_clip(data.get("playbook_label", ""), 64),
        playbook_description=_clip(data.get("playbook_description", "")),
        playbook_icon=_clip(data.get("playbook_icon", defaults.playbook_icon), 32),
        fit_score=_optional_float(data.get("fit_score")),
        analysis_threshold=_optional_float(data.get("analysis_threshold")),
        analysis_score=_optional_float(data.get("analysis_score")),
        analysis_recommendation=_clip(data.get("analysis_recommendation", ""), 32),
        risk_factor=_optional_float(data.get("risk_factor")),
        session_rating=_clip(data.get("session_rating", ""), 16),
        session_label=_clip(data.get("session_label", ""), 64),
        active_sessions=_clip(data.get("active_sessions", ""), 96),
        volume_level=_clip(data.get("volume_level", ""), 16),
        volume_label=_clip(data.get("volume_label", ""), 32),
        volume_ratio=_optional_float(data.get("volume_ratio")),
        trend=_clip(data.get("trend", ""), 16),
        volatility=_clip(data.get("volatility", ""), 16),
        open_position=_clip(data.get("open_position", "")),
        stop_management=_clip(data.get("stop_management", "")),
        trades_today=int(_optional_float(data.get("trades_today")) or 0),
        pnl_today=_optional_float(data.get("pnl_today")) or 0.0,
        equity=_optional_float(data.get("equity")),
        peak_equity=_optional_float(data.get("peak_equity")),
        peak_equity_day=_clip(data.get("peak_equity_day", ""), 16),
        drawdown_pct=_optional_float(data.get("drawdown_pct")),
        latency_seconds=_optional_float(data.get("latency_seconds")),
        halt_reason=_clip(data.get("halt_reason", ""), 32),
        halt_detail=_clip(data.get("halt_detail", "")),
        cycles=int(_optional_float(data.get("cycles")) or 0),
        last_cycle_at=data.get("last_cycle_at") or None,
        updated_at=data.get("updated_at") or None,
        worker_id=_clip(data.get("worker_id", ""), 96),
        reasons=_clip_list(data.get("reasons")),
        blockers=_clip_list(data.get("blockers")),
        last_error=_clip(data.get("last_error", "")),
        activities=_coerce_activities(data.get("activities")),
    )


def save_autopilot_status(session: Session, status: AutopilotStatus) -> None:
    SystemSettingRepository(session).set(
        AUTOPILOT_STATUS_KEY,
        json.dumps(asdict(status), ensure_ascii=True, separators=(",", ":")),
        description="Status ao vivo do piloto automatico de operacoes.",
    )


def append_activity(
    status: AutopilotStatus,
    *,
    phase: AutopilotPhase,
    message: str,
    level: ActivityLevel = ActivityLevel.INFO,
    at: str | None = None,
) -> AutopilotStatus:
    """Acrescenta uma linha ao feed, sem repetir a ultima.

    O worker roda a cada poucos segundos e passa a maior parte do tempo na
    mesma fase ("aguardando o gatilho"). Repetir essa linha centenas de
    vezes empurraria para fora do feed exatamente os eventos que importam,
    entao a repeticao imediata e ignorada.
    """
    clipped = _clip(message)
    if status.activities and status.activities[-1].message == clipped:
        return status
    entry = AutopilotActivity(
        at=at or utc_now_iso(),
        phase=phase.value,
        message=clipped,
        level=level.value,
    )
    return replace(status, activities=(*status.activities, entry)[-MAX_ACTIVITIES:])


class AutopilotStatusPublisher:
    """Publica o status usando uma sessao propria e curta por escrita.

    Sessao propria de proposito: o ciclo de operacao mantem uma transacao
    longa (analise + envio de ordem + persistencia do trade) e o status
    precisa ficar visivel no dashboard ANTES dessa transacao terminar —
    caso contrario o operador so veria o resultado, nunca o processo.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        worker_id: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now_iso(self) -> str:
        return self._clock().isoformat()

    def load(self) -> AutopilotStatus:
        session = self._session_factory()
        try:
            return load_autopilot_status(session)
        finally:
            session.close()

    def _save(self, status: AutopilotStatus) -> AutopilotStatus:
        published = replace(
            status,
            updated_at=self._now_iso(),
            worker_id=self._worker_id or status.worker_id,
        )
        session = self._session_factory()
        try:
            save_autopilot_status(session, published)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return published

    def update(self, **fields: Any) -> AutopilotStatus:
        return self._save(replace(self.load(), **fields))

    def publish(
        self,
        phase: AutopilotPhase,
        headline: str,
        *,
        detail: str = "",
        level: ActivityLevel = ActivityLevel.INFO,
        log: bool = True,
        **fields: Any,
    ) -> AutopilotStatus:
        """Muda a fase corrente e (por padrao) registra a linha no feed."""
        status = replace(
            self.load(),
            phase=phase.value,
            headline=_clip(headline),
            detail=_clip(detail),
            **fields,
        )
        if log:
            status = append_activity(
                status, phase=phase, message=headline, level=level, at=self._now_iso()
            )
        return self._save(status)

    def note(
        self,
        message: str,
        *,
        level: ActivityLevel = ActivityLevel.INFO,
    ) -> AutopilotStatus:
        """Registra um acontecimento sem mudar a fase corrente."""
        status = self.load()
        return self._save(
            append_activity(
                status,
                phase=status.phase_enum,
                message=message,
                level=level,
                at=self._now_iso(),
            )
        )

    def turn_off(self, headline: str = "Piloto automatico desligado.") -> AutopilotStatus:
        return self.publish(
            AutopilotPhase.OFF,
            headline,
            enabled=False,
            playbook_kind="",
            playbook_label="",
            playbook_description="",
            playbook_icon="bi-power",
            reasons=(),
            blockers=(),
        )


def summarize_activities(activities: Iterable[AutopilotActivity], *, limit: int = 8) -> list[dict]:
    """Serializa o feed para JSON do dashboard, do mais recente ao mais antigo."""
    ordered = list(activities)[-limit:]
    ordered.reverse()
    return [asdict(activity) for activity in ordered]
