"""Status ao vivo do piloto (`app.execution.autopilot_status`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.execution.autopilot_status import (
    AUTOPILOT_STATUS_KEY,
    MAX_ACTIVITIES,
    ActivityLevel,
    AutopilotPhase,
    AutopilotStatus,
    AutopilotStatusPublisher,
    append_activity,
    load_autopilot_status,
    save_autopilot_status,
    summarize_activities,
)


def publisher_for(db_session) -> AutopilotStatusPublisher:
    """O publisher abre a propria sessao; nos testes ela e a mesma sessao
    do teste, para que os asserts enxerguem o que foi gravado."""

    class _KeepOpenSession:
        def __init__(self, session) -> None:
            self._session = session

        def __getattr__(self, item):
            return getattr(self._session, item)

        def close(self) -> None:  # nunca fecha a sessao do teste
            return None

        def commit(self) -> None:
            self._session.flush()

    return AutopilotStatusPublisher(
        lambda: _KeepOpenSession(db_session), worker_id="test-worker"
    )


def test_default_status_is_off_and_stale(db_session) -> None:
    status = load_autopilot_status(db_session)
    assert status.phase == AutopilotPhase.OFF.value
    assert not status.enabled
    assert not status.is_fresh()


def test_round_trip_preserves_fields(db_session) -> None:
    original = AutopilotStatus(
        enabled=True,
        phase=AutopilotPhase.ANALYZING.value,
        headline="Analisando EURUSD",
        symbol="EURUSD",
        timeframe="M15",
        analysis_score=91.5,
        risk_factor=0.75,
        reasons=("um motivo",),
        activities=(),
    )
    save_autopilot_status(db_session, original)
    loaded = load_autopilot_status(db_session)
    assert loaded.enabled
    assert loaded.phase == AutopilotPhase.ANALYZING.value
    assert loaded.analysis_score == 91.5
    assert loaded.risk_factor == 0.75
    assert loaded.reasons == ("um motivo",)


def test_corrupted_payload_falls_back_to_default(db_session) -> None:
    SystemSettingRepository(db_session).set(AUTOPILOT_STATUS_KEY, "{nao e json")
    assert load_autopilot_status(db_session).phase == AutopilotPhase.OFF.value


def test_activity_feed_is_bounded(db_session) -> None:
    status = AutopilotStatus()
    for index in range(MAX_ACTIVITIES + 10):
        status = append_activity(
            status, phase=AutopilotPhase.ANALYZING, message=f"evento {index}"
        )
    assert len(status.activities) == MAX_ACTIVITIES
    assert status.activities[-1].message == f"evento {MAX_ACTIVITIES + 9}"


def test_repeated_message_is_not_appended_twice() -> None:
    status = append_activity(
        AutopilotStatus(), phase=AutopilotPhase.WAITING_TRIGGER, message="aguardando"
    )
    status = append_activity(
        status, phase=AutopilotPhase.WAITING_TRIGGER, message="aguardando"
    )
    assert len(status.activities) == 1


def test_publisher_marks_updated_at_and_worker(db_session) -> None:
    publisher = publisher_for(db_session)
    published = publisher.publish(
        AutopilotPhase.READING_MARKET, "Lendo o mercado", enabled=True
    )
    assert published.updated_at is not None
    assert published.worker_id == "test-worker"
    assert published.is_fresh()
    assert published.activities[-1].message == "Lendo o mercado"


def test_publisher_note_keeps_current_phase(db_session) -> None:
    publisher = publisher_for(db_session)
    publisher.publish(AutopilotPhase.POSITION_OPEN, "Operacao aberta", enabled=True)
    after = publisher.note("Stop movido pela corretora", level=ActivityLevel.GOOD)
    assert after.phase == AutopilotPhase.POSITION_OPEN.value
    assert after.activities[-1].message == "Stop movido pela corretora"


def test_turn_off_clears_playbook(db_session) -> None:
    publisher = publisher_for(db_session)
    publisher.publish(
        AutopilotPhase.ANALYZING,
        "Analisando",
        enabled=True,
        playbook_label="Tendencia com pullback",
    )
    off = publisher.turn_off()
    assert not off.enabled
    assert off.phase == AutopilotPhase.OFF.value
    assert off.playbook_label == ""


def test_stale_status_is_not_fresh_and_not_working() -> None:
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    status = AutopilotStatus(
        enabled=True, phase=AutopilotPhase.ANALYZING.value, updated_at=old
    )
    assert not status.is_fresh()
    # `is_working` reflete a fase; a frescura e checada separadamente por
    # quem monta a tela, para poder dizer "ligado, mas sem publicar".
    assert status.is_working


def test_stand_aside_is_not_counted_as_working() -> None:
    status = AutopilotStatus(enabled=True, phase=AutopilotPhase.STANDING_ASIDE.value)
    assert not status.is_working


def test_summarize_activities_is_newest_first(db_session) -> None:
    status = AutopilotStatus()
    for index in range(3):
        status = append_activity(
            status, phase=AutopilotPhase.ANALYZING, message=f"evento {index}"
        )
    rows = summarize_activities(status.activities)
    assert [row["message"] for row in rows] == ["evento 2", "evento 1", "evento 0"]


def test_long_status_survives_persistence(db_session) -> None:
    """O feed passa de 1000 caracteres — a coluna precisa ser TEXT
    (migration 0008), nao VARCHAR(1000)."""
    status = AutopilotStatus(enabled=True, headline="x" * 200)
    for index in range(MAX_ACTIVITIES):
        status = append_activity(
            status,
            phase=AutopilotPhase.ANALYZING,
            message=f"motivo longo numero {index} " + "y" * 150,
        )
    save_autopilot_status(db_session, status)
    loaded = load_autopilot_status(db_session)
    assert len(loaded.activities) == MAX_ACTIVITIES
    raw = SystemSettingRepository(db_session).get(AUTOPILOT_STATUS_KEY)
    assert raw is not None and len(raw) > 1000
