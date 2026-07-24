from app.database.repositories.audit_log_repository import AuditLogRepository


def test_record_creates_entry_with_defaults(db_session) -> None:
    repo = AuditLogRepository(db_session)
    entry = repo.record(action="login", entity="user", detail="user logged in")

    assert entry.id is not None
    assert entry.result == "SUCCESS"
    assert entry.action == "login"


def test_list_recent_orders_by_occurred_at_descending(db_session) -> None:
    repo = AuditLogRepository(db_session)
    repo.record(action="first")
    repo.record(action="second")
    repo.record(action="third")

    entries = repo.list_recent(limit=10)
    actions = [e.action for e in entries]
    assert actions.index("third") < actions.index("second") < actions.index("first")


def test_list_recent_respects_limit(db_session) -> None:
    repo = AuditLogRepository(db_session)
    for i in range(5):
        repo.record(action=f"action_{i}")

    entries = repo.list_recent(limit=2)
    assert len(entries) == 2


def test_record_accepts_failure_result(db_session) -> None:
    repo = AuditLogRepository(db_session)
    entry = repo.record(action="mode_change", result="FAILURE", detail="blocked transition")
    assert entry.result == "FAILURE"
