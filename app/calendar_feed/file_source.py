"""Calendario lido de um arquivo JSON local.

Pensado para o calendario NATIVO do MetaTrader 5: ele ja existe na maquina
onde o conector roda, e um Expert Advisor pequeno consegue exporta-lo para
disco. Isso da uma fonte gratuita, sem dependencia de terceiro, sem
Cloudflare no caminho e sem cota para estourar — e traz `forecast`/`actual`,
que abrem caminho para medir surpresa depois.

Formato esperado (lista, ou objeto com a chave `events`):

    [
      {
        "title": "Non-Farm Payrolls",
        "scheduled_at": "2026-09-04T12:30:00Z",
        "currency": "USD",
        "impact": "HIGH",
        "forecast": 165000,
        "previous": 142000,
        "actual": null
      }
    ]

Arquivo ausente, ilegivel ou velho demais NAO vira lista vazia silenciosa:
vira `CalendarStatus.ERROR` com mensagem. "Nenhum evento" e "nao consegui
ler" tem o mesmo formato e significados opostos — confundir os dois foi
exatamente o tipo de erro que este modulo existe para nao repetir.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.calendar_feed.provider import CalendarEvent, CalendarSnapshot, CalendarStatus

logger = logging.getLogger(__name__)

_IMPACTS = {"LOW", "MEDIUM", "HIGH"}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    texto = str(value).strip().replace("Z", "+00:00")
    try:
        momento = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _parse_number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def parse_event(raw: dict[str, Any]) -> CalendarEvent | None:
    """Um registro invalido e descartado, nunca adivinhado."""
    quando = _parse_datetime(raw.get("scheduled_at") or raw.get("time"))
    titulo = str(raw.get("title") or raw.get("event") or "").strip()
    if quando is None or not titulo:
        return None

    impacto = str(raw.get("impact", "MEDIUM")).strip().upper()
    return CalendarEvent(
        title=titulo,
        scheduled_at=quando,
        currency=str(raw.get("currency", "")).strip().upper(),
        impact=impacto if impacto in _IMPACTS else "MEDIUM",  # type: ignore[arg-type]
        actual=_parse_number(raw.get("actual")),
        forecast=_parse_number(raw.get("forecast")),
        previous=_parse_number(raw.get("previous")),
    )


class FileCalendarProvider:
    """Le o calendario de um arquivo mantido por fora do processo."""

    def __init__(self, path: str | Path, *, max_age_hours: int = 36) -> None:
        self._path = Path(path)
        self._max_age = timedelta(hours=max_age_hours)

    def fetch_events(self, *, now: datetime, horizon_minutes: int) -> CalendarSnapshot:
        if not self._path.exists():
            return CalendarSnapshot(
                status=CalendarStatus.NOT_CONFIGURED,
                message=f"Calendario nao encontrado em {self._path}.",
            )

        idade = now - datetime.fromtimestamp(self._path.stat().st_mtime, tz=UTC)
        if idade > self._max_age:
            # Calendario velho e pior que nenhum: os eventos de hoje nao
            # estao la, e o sistema acreditaria que o dia esta limpo.
            return CalendarSnapshot(
                status=CalendarStatus.ERROR,
                message=(
                    f"Calendario desatualizado ({int(idade.total_seconds() // 3600)}h "
                    "sem atualizacao) — o exportador do MetaTrader parou?"
                ),
            )

        try:
            dados = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("calendar_file_unreadable", extra={"error": str(exc)})
            return CalendarSnapshot(
                status=CalendarStatus.ERROR,
                message=f"Calendario ilegivel: {type(exc).__name__}.",
            )

        if isinstance(dados, dict):
            # Dicionario SEM a chave `events` e arquivo malformado, nao dia
            # vazio. Devolver "0 eventos" aqui seria exatamente a confusao
            # que este modulo existe para evitar.
            if "events" not in dados:
                return CalendarSnapshot(
                    status=CalendarStatus.ERROR,
                    message="Calendario sem a chave 'events'.",
                )
            registros = dados["events"]
        else:
            registros = dados
        if not isinstance(registros, list):
            return CalendarSnapshot(
                status=CalendarStatus.ERROR,
                message="Calendario com formato inesperado (esperada uma lista).",
            )

        limite = timedelta(minutes=horizon_minutes)
        eventos = [
            evento
            for evento in (parse_event(item) for item in registros if isinstance(item, dict))
            if evento is not None and abs(evento.scheduled_at - now) <= limite
        ]
        return CalendarSnapshot(
            status=CalendarStatus.OK,
            events=eventos,
            message=f"{len(eventos)} evento(s) na janela.",
        )
