"""Escolha e cache do provedor de calendario.

Eficiencia: o calendario muda devagar (uma agenda do dia), entao a leitura
fica em cache por processo com prazo proprio. Mesmo lendo de disco local, sem
isso o sistema abriria e parseria o arquivo a cada ciclo do piloto — a cada
15 segundos, para uma informacao que muda uma vez por dia.
"""

from __future__ import annotations

import time
from datetime import datetime
from threading import Lock

from app.calendar_feed.provider import CalendarProvider, CalendarSnapshot, CalendarStatus
from app.core.config import Settings


class UnconfiguredCalendarProvider:
    """Sem fonte configurada. Nao inventa evento, e nao finge que o dia esta
    limpo: reporta `NOT_CONFIGURED` para que quem decide saiba a diferenca."""

    def fetch_events(self, *, now: datetime, horizon_minutes: int) -> CalendarSnapshot:
        return CalendarSnapshot(
            status=CalendarStatus.NOT_CONFIGURED,
            message=(
                "Calendario economico nao configurado — o filtro de eventos de "
                "alto impacto esta inativo (CALENDAR_FILE_PATH)."
            ),
        )


class CachedCalendarProvider:
    """Reaproveita a ultima leitura BOA por `ttl_seconds`.

    Falha nao entra no cache, pela mesma razao de sempre: congelar uma falha
    esconderia que o exportador parou.
    """

    def __init__(
        self, inner: CalendarProvider, *, ttl_seconds: float = 900.0, clock=time.monotonic
    ) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = Lock()
        self._cached: tuple[float, int, CalendarSnapshot] | None = None

    def fetch_events(self, *, now: datetime, horizon_minutes: int) -> CalendarSnapshot:
        if self._ttl > 0:
            with self._lock:
                if self._cached is not None:
                    guardado_em, horizonte, snapshot = self._cached
                    # Horizonte diferente pede leitura nova: a janela filtra
                    # os eventos na origem.
                    if (
                        horizonte == horizon_minutes
                        and self._clock() - guardado_em <= self._ttl
                    ):
                        return snapshot

        snapshot = self._inner.fetch_events(now=now, horizon_minutes=horizon_minutes)
        if snapshot.usable and self._ttl > 0:
            with self._lock:
                self._cached = (self._clock(), horizon_minutes, snapshot)
        return snapshot


_PROVIDER: CalendarProvider | None = None
_PROVIDER_KEY: tuple | None = None
_PROVIDER_LOCK = Lock()


def _build(settings: Settings) -> CalendarProvider:
    if not settings.calendar_file_path:
        return UnconfiguredCalendarProvider()
    from app.calendar_feed.file_source import FileCalendarProvider

    return CachedCalendarProvider(
        FileCalendarProvider(
            settings.calendar_file_path,
            max_age_hours=settings.calendar_max_age_hours,
        ),
        ttl_seconds=settings.calendar_cache_ttl_seconds,
    )


def get_calendar_provider(settings: Settings) -> CalendarProvider:
    global _PROVIDER, _PROVIDER_KEY
    chave = (
        settings.calendar_file_path,
        settings.calendar_cache_ttl_seconds,
        settings.calendar_max_age_hours,
    )
    with _PROVIDER_LOCK:
        if _PROVIDER is None or chave != _PROVIDER_KEY:
            _PROVIDER = _build(settings)
            _PROVIDER_KEY = chave
        return _PROVIDER


def reset_calendar_provider() -> None:
    """Descarta o provedor — usado pelos testes e ao trocar configuracao."""
    global _PROVIDER, _PROVIDER_KEY
    with _PROVIDER_LOCK:
        _PROVIDER = None
        _PROVIDER_KEY = None
