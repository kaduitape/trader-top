"""Calendario lido de arquivo local.

O ponto sensivel aqui e um so: "nao ha evento" e "nao consegui ler" tem o
mesmo formato (lista vazia) e significados opostos. Confundir os dois faria o
sistema acreditar que o dia esta limpo justamente quando a fonte quebrou.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.calendar_feed.file_source import FileCalendarProvider, parse_event
from app.calendar_feed.provider import CalendarStatus

AGORA = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def escrever(tmp_path: Path, dados, *, atualizado_em: datetime = AGORA) -> Path:
    """Escreve o arquivo E fixa a data de modificacao.

    A verificacao de frescor compara o `now` do teste com o mtime do arquivo;
    deixar o mtime no relogio real tornaria o teste dependente da data em que
    ele roda.
    """
    caminho = tmp_path / "calendario.json"
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    momento = atualizado_em.timestamp()
    os.utime(caminho, (momento, momento))
    return caminho


def evento_bruto(**overrides):
    base = {
        "title": "Non-Farm Payrolls",
        "scheduled_at": "2026-09-04T12:30:00Z",
        "currency": "USD",
        "impact": "HIGH",
        "forecast": 165000,
        "previous": 142000,
    }
    base.update(overrides)
    return base


def test_a_missing_file_is_reported_not_treated_as_a_clear_day(tmp_path: Path) -> None:
    snapshot = FileCalendarProvider(tmp_path / "nao-existe.json").fetch_events(
        now=AGORA, horizon_minutes=120
    )

    assert snapshot.status == CalendarStatus.NOT_CONFIGURED
    assert not snapshot.usable


def test_events_are_parsed_with_their_values(tmp_path: Path) -> None:
    caminho = escrever(tmp_path, [evento_bruto()])

    snapshot = FileCalendarProvider(caminho).fetch_events(now=AGORA, horizon_minutes=120)

    assert snapshot.usable
    assert len(snapshot.events) == 1
    evento = snapshot.events[0]
    assert evento.title == "Non-Farm Payrolls"
    assert evento.currency == "USD"
    assert evento.impact == "HIGH"
    assert evento.forecast == 165000
    assert evento.scheduled_at.tzinfo is not None


def test_the_events_key_wrapper_is_accepted(tmp_path: Path) -> None:
    caminho = escrever(tmp_path, {"events": [evento_bruto()]})

    assert FileCalendarProvider(caminho).fetch_events(
        now=AGORA, horizon_minutes=120
    ).events


def test_events_outside_the_horizon_are_dropped(tmp_path: Path) -> None:
    caminho = escrever(
        tmp_path,
        [evento_bruto(scheduled_at="2026-09-06T12:30:00Z")],
    )

    snapshot = FileCalendarProvider(caminho).fetch_events(now=AGORA, horizon_minutes=120)

    assert snapshot.usable
    assert snapshot.events == []


def test_a_stale_file_is_an_error_not_a_clear_day(tmp_path: Path) -> None:
    """Calendario velho e pior que nenhum: os eventos de hoje nao estao la
    e o sistema acreditaria que o dia esta limpo."""
    caminho = escrever(tmp_path, [evento_bruto()], atualizado_em=AGORA - timedelta(hours=48))

    snapshot = FileCalendarProvider(caminho, max_age_hours=36).fetch_events(
        now=AGORA, horizon_minutes=120
    )

    assert snapshot.status == CalendarStatus.ERROR
    assert "desatualizado" in snapshot.message


def test_broken_json_is_an_error(tmp_path: Path) -> None:
    caminho = tmp_path / "calendario.json"
    caminho.write_text("isso nao e json", encoding="utf-8")
    os.utime(caminho, (AGORA.timestamp(), AGORA.timestamp()))

    snapshot = FileCalendarProvider(caminho).fetch_events(now=AGORA, horizon_minutes=120)

    assert snapshot.status == CalendarStatus.ERROR


def test_an_unexpected_shape_is_an_error(tmp_path: Path) -> None:
    caminho = escrever(tmp_path, {"eventos": "nada disso"})

    snapshot = FileCalendarProvider(caminho).fetch_events(now=AGORA, horizon_minutes=120)

    assert snapshot.status == CalendarStatus.ERROR


def test_a_single_invalid_record_does_not_lose_the_valid_ones(tmp_path: Path) -> None:
    caminho = escrever(
        tmp_path,
        [{"title": "sem horario"}, evento_bruto()],
    )

    snapshot = FileCalendarProvider(caminho).fetch_events(now=AGORA, horizon_minutes=120)

    assert snapshot.usable
    assert len(snapshot.events) == 1


def test_an_unknown_impact_falls_back_to_medium() -> None:
    evento = parse_event(evento_bruto(impact="CATASTROFICO"))
    assert evento is not None
    assert evento.impact == "MEDIUM"


def test_a_naive_timestamp_is_read_as_utc() -> None:
    evento = parse_event(evento_bruto(scheduled_at="2026-09-04T12:30:00"))
    assert evento is not None
    assert evento.scheduled_at.tzinfo is not None


def test_a_dash_placeholder_becomes_none_not_zero() -> None:
    """"-" e como calendarios marcam "sem previsao". Virar 0.0 inventaria um
    numero que ninguem publicou."""
    evento = parse_event(evento_bruto(forecast="-", actual=""))
    assert evento is not None
    assert evento.forecast is None
    assert evento.actual is None


def test_numbers_with_separators_are_understood() -> None:
    evento = parse_event(evento_bruto(actual="165,000", forecast="3.5%"))
    assert evento is not None
    assert evento.actual == 165000
    assert evento.forecast == 3.5
