import json
import logging

from app.core.logging import JsonFormatter


def _make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="something happened",
        args=None,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_produces_valid_json() -> None:
    formatter = JsonFormatter()
    record = _make_record(correlation_id="abc-123")
    payload = json.loads(formatter.format(record))

    assert payload["event"] == "something happened"
    assert payload["level"] == "INFO"
    assert payload["module"] == "app.test"
    assert payload["correlation_id"] == "abc-123"
    assert "timestamp" in payload


def test_json_formatter_masks_sensitive_fields() -> None:
    formatter = JsonFormatter()
    record = _make_record(password="hunter2", mt5_login_token="secret-token")
    payload = json.loads(formatter.format(record))

    assert payload["password"] == "***MASKED***"
    assert payload["mt5_login_token"] == "***MASKED***"
