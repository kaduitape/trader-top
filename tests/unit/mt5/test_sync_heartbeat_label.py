"""Idade do batimento em texto.

"Offline" sozinho nao ajuda: parou agora ou faz tres dias? A diferenca
decide se o operador espera o conector voltar sozinho ou vai atras do
problema — e foi a falta dessa distincao que fez "reinstalar" virar o
remedio para qualquer queda.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.mt5.sync_settings import MT5SyncStatus, heartbeat_age_label


def _status(minutos: float) -> MT5SyncStatus:
    momento = datetime.now(UTC) - timedelta(minutes=minutos)
    return MT5SyncStatus(heartbeat_at=momento.isoformat())


def test_without_a_heartbeat_there_is_nothing_to_say() -> None:
    """Nunca respondeu e parou de responder sao situacoes diferentes."""
    assert heartbeat_age_label(MT5SyncStatus()) is None


def test_a_corrupt_timestamp_does_not_break_the_screen() -> None:
    assert heartbeat_age_label(MT5SyncStatus(heartbeat_at="ontem")) is None


def test_a_recent_heartbeat_reads_as_seconds() -> None:
    assert heartbeat_age_label(_status(0.2)) == "ha menos de um minuto"


def test_minutes_are_shown_in_minutes() -> None:
    assert heartbeat_age_label(_status(7)) == "ha 7 min"


def test_hours_are_shown_in_hours() -> None:
    assert heartbeat_age_label(_status(200)) == "ha 3 h"


def test_days_are_shown_in_days() -> None:
    assert heartbeat_age_label(_status(60 * 24 * 2 + 30)) == "ha 2 dia(s)"


def test_a_naive_timestamp_is_read_as_utc() -> None:
    """O worker grava com fuso; um registro antigo sem fuso nao pode virar
    uma idade absurda so por causa disso."""
    sem_fuso = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)

    assert heartbeat_age_label(MT5SyncStatus(heartbeat_at=sem_fuso.isoformat())) == "ha 5 min"
