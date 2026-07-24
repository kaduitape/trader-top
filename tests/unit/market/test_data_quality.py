from datetime import UTC, datetime, timedelta

from app.market.data_quality import (
    DataQualityIssue,
    Severity,
    check_candles,
    check_ticks,
    compute_score,
    is_acceptable,
)
from app.mt5.market_data import RawCandle, RawTick

_BASE_TIME = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)  # segunda-feira


def _candle(minute_offset: int, **overrides: object) -> RawCandle:
    base: dict[str, object] = {
        "open_time": _BASE_TIME + timedelta(minutes=minute_offset),
        "open": 1.1000,
        "high": 1.1010,
        "low": 1.0990,
        "close": 1.1005,
        "tick_volume": 100,
        "spread": 2,
        "real_volume": 0,
    }
    base.update(overrides)
    return RawCandle(**base)  # type: ignore[arg-type]


def _tick(seconds_offset: float, **overrides: object) -> RawTick:
    base: dict[str, object] = {
        "timestamp": _BASE_TIME + timedelta(seconds=seconds_offset),
        "bid": 1.1000,
        "ask": 1.1002,
        "last": 0.0,
        "volume": 0.0,
        "flags": 6,
    }
    base.update(overrides)
    return RawTick(**base)  # type: ignore[arg-type]


def test_check_candles_clean_series_has_no_issues() -> None:
    candles = [_candle(i) for i in range(5)]
    assert check_candles(candles, timeframe_seconds=60) == []


def test_check_candles_detects_duplicate_open_time() -> None:
    candles = [_candle(0), _candle(0)]
    issues = check_candles(candles, timeframe_seconds=60)
    assert any(i.check == "duplicate_open_time" for i in issues)


def test_check_candles_detects_gap_within_week() -> None:
    candles = [_candle(0), _candle(30)]  # 30 min gap, muito maior que 1min*1.5
    issues = check_candles(candles, timeframe_seconds=60)
    gap_issues = [i for i in issues if i.check == "candle_gap"]
    assert len(gap_issues) == 1
    assert gap_issues[0].severity == Severity.WARNING


def test_check_candles_weekend_gap_is_only_info() -> None:
    candles = [_candle(0), _candle(60 * 60 * 60 // 60)]  # 60h de gap
    issues = check_candles(candles, timeframe_seconds=60)
    gap_issues = [i for i in issues if i.check == "candle_gap"]
    assert len(gap_issues) == 1
    assert gap_issues[0].severity == Severity.INFO


def test_check_candles_realistic_weekend_gap_is_only_info() -> None:
    """Bug real (Fase 16): um fechamento de fim de semana comum de forex
    (~48h, sexta a domingo a noite UTC) tem que ser INFO, nao WARNING --
    o limiar antigo (55h) ficava ACIMA de 48h e classificava TODO fim de
    semana normal como problema de coleta."""
    candles = [_candle(0), _candle(48 * 60)]  # 48h de gap
    issues = check_candles(candles, timeframe_seconds=3600)
    gap_issues = [i for i in issues if i.check == "candle_gap"]
    assert len(gap_issues) == 1
    assert gap_issues[0].severity == Severity.INFO


def test_check_candles_detects_high_below_low() -> None:
    candles = [_candle(0, high=1.0, low=1.1)]
    issues = check_candles(candles, timeframe_seconds=60)
    assert any(
        i.check == "candle_high_below_low" and i.severity == Severity.CRITICAL for i in issues
    )


def test_check_candles_detects_ohlc_out_of_range() -> None:
    candles = [_candle(0, open=1.2, high=1.1010, low=1.0990)]
    issues = check_candles(candles, timeframe_seconds=60)
    assert any(i.check == "candle_ohlc_out_of_range" for i in issues)


def test_check_candles_detects_invalid_price() -> None:
    candles = [_candle(0, open=0.0)]
    issues = check_candles(candles, timeframe_seconds=60)
    assert any(i.check == "candle_invalid_price" for i in issues)


def test_check_candles_detects_negative_volume() -> None:
    candles = [_candle(0, tick_volume=-1)]
    issues = check_candles(candles, timeframe_seconds=60)
    assert any(i.check == "candle_invalid_volume" for i in issues)


def test_check_ticks_clean_series_has_no_issues() -> None:
    ticks = [_tick(i) for i in range(5)]
    now = ticks[-1].timestamp
    issues = check_ticks(
        ticks, point=0.00001, max_spread_points=50, now=now, max_feed_delay_seconds=300
    )
    assert issues == []


def test_check_ticks_detects_out_of_order() -> None:
    ticks = [_tick(5), _tick(0)]
    issues = check_ticks(
        ticks,
        point=0.00001,
        max_spread_points=50,
        now=ticks[0].timestamp,
        max_feed_delay_seconds=300,
    )
    assert any(i.check == "tick_out_of_order" for i in issues)


def test_check_ticks_detects_negative_spread() -> None:
    ticks = [_tick(0, bid=1.1010, ask=1.1000)]
    issues = check_ticks(
        ticks,
        point=0.00001,
        max_spread_points=50,
        now=ticks[0].timestamp,
        max_feed_delay_seconds=300,
    )
    assert any(i.check == "tick_negative_spread" for i in issues)


def test_check_ticks_detects_wide_spread() -> None:
    ticks = [_tick(0, bid=1.1000, ask=1.1100)]  # 1000 pontos de spread
    issues = check_ticks(
        ticks,
        point=0.00001,
        max_spread_points=50,
        now=ticks[0].timestamp,
        max_feed_delay_seconds=300,
    )
    assert any(i.check == "tick_spread_too_wide" and i.severity == Severity.WARNING for i in issues)


def test_check_ticks_detects_invalid_price() -> None:
    ticks = [_tick(0, bid=0.0)]
    issues = check_ticks(
        ticks,
        point=0.00001,
        max_spread_points=50,
        now=ticks[0].timestamp,
        max_feed_delay_seconds=300,
    )
    assert any(i.check == "tick_invalid_price" for i in issues)


def test_check_ticks_detects_negative_volume() -> None:
    ticks = [_tick(0, volume=-1.0)]
    issues = check_ticks(
        ticks,
        point=0.00001,
        max_spread_points=50,
        now=ticks[0].timestamp,
        max_feed_delay_seconds=300,
    )
    assert any(i.check == "tick_invalid_volume" for i in issues)


def test_check_ticks_detects_future_timestamp() -> None:
    now = _BASE_TIME
    ticks = [_tick(0, timestamp=now + timedelta(hours=1))]
    issues = check_ticks(
        ticks, point=0.00001, max_spread_points=50, now=now, max_feed_delay_seconds=300
    )
    assert any(
        i.check == "tick_timestamp_in_future" and i.severity == Severity.CRITICAL for i in issues
    )


def test_check_ticks_detects_feed_delay() -> None:
    ticks = [_tick(0)]
    now = ticks[0].timestamp + timedelta(seconds=600)
    issues = check_ticks(
        ticks, point=0.00001, max_spread_points=50, now=now, max_feed_delay_seconds=300
    )
    assert any(i.check == "feed_delay" for i in issues)


def test_check_ticks_tolerates_naive_tick_timestamps_against_aware_now() -> None:
    """Bug real, achado rodando `quality check` contra ticks reais ja
    persistidos (Fase 16): o SQLite/MySQL devolvem `DateTime(timezone=
    True)` como NAIVE na leitura, mesmo gravado aware -- `cmd_quality_
    check` le ticks do banco (naive) e compara contra `datetime.now(UTC)`
    (aware), o que levantava `TypeError: can't subtract offset-naive and
    offset-aware datetimes` antes da normalizacao via `_as_naive`."""
    naive_tick = _tick(0, timestamp=_BASE_TIME.replace(tzinfo=None))
    now_aware = _BASE_TIME + timedelta(seconds=30)

    issues = check_ticks(
        [naive_tick],
        point=0.00001,
        max_spread_points=50,
        now=now_aware,
        max_feed_delay_seconds=300,
    )

    assert not any(i.check == "tick_timestamp_in_future" for i in issues)
    assert not any(i.check == "feed_delay" for i in issues)


def test_compute_score_penalizes_by_severity() -> None:
    assert compute_score([]) == 100
    critical = DataQualityIssue("x", Severity.CRITICAL, "msg")
    warning = DataQualityIssue("y", Severity.WARNING, "msg")
    info = DataQualityIssue("z", Severity.INFO, "msg")
    assert compute_score([critical]) == 85
    assert compute_score([warning]) == 95
    assert compute_score([info]) == 99
    assert (
        compute_score([critical, critical, critical, critical, critical, critical, critical]) == 0
    )


def test_is_acceptable_false_on_any_critical() -> None:
    critical = DataQualityIssue("x", Severity.CRITICAL, "msg")
    assert is_acceptable([critical], min_score=0) is False


def test_is_acceptable_uses_min_score_threshold() -> None:
    warnings = [DataQualityIssue("x", Severity.WARNING, "msg") for _ in range(10)]  # score 50
    assert is_acceptable(warnings, min_score=70) is False
    assert is_acceptable(warnings, min_score=40) is True
