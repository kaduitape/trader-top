from datetime import UTC, datetime, timedelta

from app.risk.feed_health import check_feed_health

_NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def test_fresh_feed_is_healthy() -> None:
    result = check_feed_health(
        last_update_time=_NOW - timedelta(seconds=10), now=_NOW, max_delay_seconds=60.0
    )
    assert result.is_healthy is True
    assert result.reason is None
    assert result.age_seconds == 10.0


def test_stale_feed_is_unhealthy() -> None:
    result = check_feed_health(
        last_update_time=_NOW - timedelta(seconds=120), now=_NOW, max_delay_seconds=60.0
    )
    assert result.is_healthy is False
    assert result.reason is not None
    assert "atrasad" in result.reason
    assert result.age_seconds == 120.0


def test_exactly_at_limit_is_healthy() -> None:
    result = check_feed_health(
        last_update_time=_NOW - timedelta(seconds=60), now=_NOW, max_delay_seconds=60.0
    )
    assert result.is_healthy is True


def test_one_second_past_limit_is_unhealthy() -> None:
    result = check_feed_health(
        last_update_time=_NOW - timedelta(seconds=61), now=_NOW, max_delay_seconds=60.0
    )
    assert result.is_healthy is False
