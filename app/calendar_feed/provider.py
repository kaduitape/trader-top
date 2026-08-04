"""Calendario economico — eventos AGENDADOS, com hora no futuro.

Existe um tipo proprio aqui, e nao um reaproveitamento de `NewsItem`, por
uma razao concreta: o portao de "nao entre antes de evento de alto impacto"
estava escrito em cima de noticias publicadas e por isso **nunca disparou
uma unica vez**. Manchete tem `published_at` no passado; evento de calendario
tem `scheduled_at` no futuro. Sao conceitos diferentes e agora tem tipos
diferentes — a confusao entre os dois foi o bug.

O provedor tambem informa se conseguiu ler o calendario. Isso importa porque
"nenhum evento nas proximas horas" e "nao consegui ler o calendario" tem o
mesmo formato (lista vazia) e significados opostos.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

EventImpact = Literal["LOW", "MEDIUM", "HIGH"]


class CalendarStatus(enum.StrEnum):
    OK = "OK"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    title: str
    scheduled_at: datetime
    """Quando o dado sai — no futuro, ate acontecer."""

    currency: str
    """Moeda afetada (USD, EUR...). Vazio quando a fonte nao informa."""

    impact: EventImpact = "MEDIUM"

    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    """Preenchidos apos a divulgacao. Abrem caminho para medir surpresa
    (`actual - forecast`), que e fundamento quantitativo de verdade — mas
    isso e outro assunto, deliberadamente fora deste portao."""


@dataclass(frozen=True, slots=True)
class CalendarSnapshot:
    status: CalendarStatus
    events: list[CalendarEvent] = field(default_factory=list)
    message: str = ""

    @property
    def usable(self) -> bool:
        return self.status == CalendarStatus.OK


class CalendarProvider(Protocol):
    def fetch_events(self, *, now: datetime, horizon_minutes: int) -> CalendarSnapshot:
        """Eventos agendados na janela [now - horizon, now + horizon].

        A janela e simetrica de proposito: o momento perigoso nao termina no
        instante da divulgacao — logo depois o spread abre e o preco chicoteia.
        """
        ...
