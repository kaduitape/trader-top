"""O que o portao de eventos VE agora, em forma de dados.

Mesmo conteudo do `calendar check` da CLI, sem o `print`: o painel precisa
das mesmas tres respostas — o arquivo esta sendo lido? os horarios estao no
fuso certo? o robo bloquearia neste momento? — e exigir um terminal para
descobrir isso deixava a verificacao para quando ja fosse tarde.

Verificar nao custa nada e nao depende de rede: o calendario vem de um
arquivo escrito pelo proprio MetaTrader.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.calendar_feed.blackout import (
    BlackoutWindow,
    currencies_for_symbol,
    describe,
    find_blocking_event,
)
from app.calendar_feed.factory import get_calendar_provider, reset_calendar_provider
from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class UpcomingEvent:
    title: str
    currency: str
    impact: str
    minutes_ahead: int
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class CalendarCheck:
    status: str
    message: str
    source: str
    usable: bool
    symbol: str
    currencies: tuple[str, ...]
    window_before: int
    window_after: int
    events: tuple[UpcomingEvent, ...]
    blocking_reason: str | None

    @property
    def blocked_now(self) -> bool:
        return self.blocking_reason is not None


def check_calendar(
    settings: Settings, *, symbol: str, horizon_minutes: int = 240, limit: int = 10
) -> CalendarCheck:
    # Leitura fresca de proposito: verificar contra o cache diria que esta
    # tudo bem por ate 15 minutos depois de o exportador ter parado.
    reset_calendar_provider()
    provider = get_calendar_provider(settings)

    agora = datetime.now(UTC)
    snapshot = provider.fetch_events(now=agora, horizon_minutes=max(horizon_minutes, 60))

    moedas = currencies_for_symbol(symbol)
    janela = BlackoutWindow(
        minutes_before=settings.calendar_blackout_before_minutes,
        minutes_after=settings.calendar_blackout_after_minutes,
    )

    relevantes: list[UpcomingEvent] = []
    if snapshot.usable:
        for evento in sorted(snapshot.events, key=lambda item: item.scheduled_at):
            if moedas and evento.currency and evento.currency.upper() not in moedas:
                continue
            relevantes.append(
                UpcomingEvent(
                    title=evento.title,
                    currency=evento.currency or "---",
                    impact=evento.impact,
                    minutes_ahead=int(
                        (evento.scheduled_at - agora).total_seconds() // 60
                    ),
                    scheduled_at=evento.scheduled_at,
                )
            )

    bloqueio = None
    if snapshot.usable:
        evento = find_blocking_event(
            snapshot.events, symbol=symbol, now=agora, window=janela,
            min_impact=settings.calendar_min_impact,
        )
        if evento is not None:
            bloqueio = describe(evento, now=agora)

    return CalendarCheck(
        status=snapshot.status.value,
        message=snapshot.message,
        source=settings.calendar_file_path or "",
        usable=snapshot.usable,
        symbol=symbol.upper(),
        currencies=tuple(sorted(moedas)),
        window_before=janela.minutes_before,
        window_after=janela.minutes_after,
        events=tuple(relevantes[:limit]),
        blocking_reason=bloqueio,
    )
