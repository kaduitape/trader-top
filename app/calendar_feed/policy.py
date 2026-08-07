"""Operar ou nao durante evento economico — decisao do operador, no painel.

Isto morava so no `.env` (`CALENDAR_MIN_IMPACT`, `CALENDAR_BLACKOUT_*`), o
que significa que mudar exigia acesso ao servidor e reinicio. E uma decisao
de ESTRATEGIA, nao de infraestrutura: tem gente que evita payroll a todo
custo e tem gente que so opera nele. Quem decide isso e quem opera.

O `.env` continua sendo o padrao; o painel apenas sobrepoe.

Uma observacao que a tela repete e vale repetir aqui: desligar o bloqueio
NAO e neutro. Em evento de alto impacto o spread abre, o slippage cresce e
o stop pode ser executado longe do preco pedido. O risco real da operacao
deixa de ser o risco calculado.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.database.repositories.system_setting_repository import SystemSettingRepository

AVOID_EVENTS_SETTING = "calendar_avoid_events"
BEFORE_SETTING = "calendar_blackout_before_minutes"
AFTER_SETTING = "calendar_blackout_after_minutes"
MIN_IMPACT_SETTING = "calendar_min_impact"

IMPACTS = ("HIGH", "MEDIUM", "LOW")
BEFORE_MIN, BEFORE_MAX = 0, 240
AFTER_MIN, AFTER_MAX = 0, 240


@dataclass(frozen=True, slots=True)
class CalendarPolicy:
    avoid_events: bool
    """False significa OPERAR normalmente durante o evento."""

    minutes_before: int
    minutes_after: int
    min_impact: str
    source: str
    """"dashboard" quando veio do painel, "env" quando veio do arquivo."""

    @property
    def horizon_minutes(self) -> int:
        return max(self.minutes_before, self.minutes_after)


def _read(session: Session, key: str) -> str | None:
    raw = SystemSettingRepository(session).get(key)
    return raw.strip() if raw and raw.strip() else None


def _read_int(session: Session, key: str) -> int | None:
    bruto = _read(session, key)
    if bruto is None:
        return None
    try:
        return int(float(bruto))
    except (TypeError, ValueError):
        return None


def load_calendar_policy(session: Session, settings: Settings) -> CalendarPolicy:
    evitar_bruto = _read(session, AVOID_EVENTS_SETTING)
    antes = _read_int(session, BEFORE_SETTING)
    depois = _read_int(session, AFTER_SETTING)
    impacto = _read(session, MIN_IMPACT_SETTING)

    do_painel = any(
        item is not None for item in (evitar_bruto, antes, depois, impacto)
    )
    return CalendarPolicy(
        # Ausente = evitar. O padrao protege; desligar tem que ser escolha
        # registrada, nunca resultado de um campo em branco.
        avoid_events=(evitar_bruto or "1") not in ("0", "false", "nao", "no"),
        minutes_before=(
            antes if antes is not None else settings.calendar_blackout_before_minutes
        ),
        minutes_after=(
            depois if depois is not None else settings.calendar_blackout_after_minutes
        ),
        min_impact=(impacto or settings.calendar_min_impact).upper(),
        source="dashboard" if do_painel else "env",
    )


def validate_calendar_policy(
    *, minutes_before: int | None, minutes_after: int | None, min_impact: str | None
) -> str | None:
    if minutes_before is not None and not BEFORE_MIN <= minutes_before <= BEFORE_MAX:
        return f"os minutos antes devem ficar entre {BEFORE_MIN} e {BEFORE_MAX}."
    if minutes_after is not None and not AFTER_MIN <= minutes_after <= AFTER_MAX:
        return f"os minutos depois devem ficar entre {AFTER_MIN} e {AFTER_MAX}."
    if min_impact is not None and min_impact.upper() not in IMPACTS:
        return f"o impacto minimo deve ser um de: {', '.join(IMPACTS)}."
    return None


def save_calendar_policy(
    session: Session,
    *,
    avoid_events: bool,
    minutes_before: int,
    minutes_after: int,
    min_impact: str,
) -> list[str]:
    repo = SystemSettingRepository(session)
    repo.set(
        AVOID_EVENTS_SETTING,
        "1" if avoid_events else "0",
        description="Bloquear entradas em torno de evento economico (painel).",
    )
    repo.set(BEFORE_SETTING, str(minutes_before), description="Minutos antes do evento.")
    repo.set(AFTER_SETTING, str(minutes_after), description="Minutos depois do evento.")
    repo.set(MIN_IMPACT_SETTING, min_impact.upper(), description="Impacto minimo.")
    return [
        "evita eventos" if avoid_events else "OPERA durante eventos",
        f"janela -{minutes_before}/+{minutes_after} min",
        f"impacto minimo {min_impact.upper()}",
    ]
