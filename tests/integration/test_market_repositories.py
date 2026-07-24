from datetime import UTC, datetime, timedelta

from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.data_quality_repository import DataQualityEventRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.tick_repository import TickRepository
from app.market.data_quality import DataQualityIssue, Severity
from app.mt5.market_data import RawCandle, RawTick
from app.mt5.symbol_mapper import SymbolSpecification


def _spec(name: str, **overrides: object) -> SymbolSpecification:
    base: dict[str, object] = {
        "name": name,
        "description": "Test symbol",
        "digits": 5,
        "point": 0.00001,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_contract_size": 100_000.0,
        "spread": 2,
        "trade_mode": 4,
        "visible": True,
    }
    base.update(overrides)
    return SymbolSpecification(**base)  # type: ignore[arg-type]


def test_symbol_repository_upsert_creates_then_updates(db_session) -> None:
    repo = SymbolRepository(db_session)

    created = repo.upsert_from_specification(_spec("REPO_SYM1", volume_min=0.01))
    assert float(created.volume_min) == 0.01

    updated = repo.upsert_from_specification(_spec("REPO_SYM1", volume_min=0.05))
    db_session.flush()

    assert updated.id == created.id
    assert float(repo.get_by_name("REPO_SYM1").volume_min) == 0.05


def test_candle_repository_bulk_upsert_deduplicates(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM2"))

    candles = [
        RawCandle(
            open_time=datetime(2026, 1, 1, 10, i, tzinfo=UTC),
            open=1.1,
            high=1.2,
            low=1.0,
            close=1.15,
            tick_volume=10,
            spread=2,
            real_volume=0,
        )
        for i in range(3)
    ]

    repo = CandleRepository(db_session)
    first_pass = repo.bulk_upsert(symbol.id, "M1", candles)
    second_pass = repo.bulk_upsert(symbol.id, "M1", candles)

    assert first_pass == 3
    assert second_pass == 0


def test_tick_repository_bulk_upsert_deduplicates(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM3"))

    ticks = [
        RawTick(
            timestamp=datetime(2026, 1, 1, 10, 0, i, tzinfo=UTC),
            bid=1.1000 + i * 0.0001,
            ask=1.1002 + i * 0.0001,
            last=0.0,
            volume=0.0,
            flags=6,
        )
        for i in range(5)
    ]

    repo = TickRepository(db_session)
    first_pass = repo.bulk_upsert(symbol.id, ticks)
    second_pass = repo.bulk_upsert(symbol.id, ticks)

    assert first_pass == 5
    assert second_pass == 0


def test_tick_repository_deduplicates_float_artifact_at_database_scale(
    db_session,
) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_FLOAT_TICK"))
    tick = RawTick(
        timestamp=datetime(2026, 7, 23, 12, 0, 0, 184000, tzinfo=UTC),
        bid=1.3315299999999999,
        ask=1.33156,
        last=0.0,
        volume=0.0,
        flags=4,
    )
    repository = TickRepository(db_session)

    first_pass = repository.bulk_upsert(symbol.id, [tick])
    db_session.flush()
    second_pass = repository.bulk_upsert(symbol.id, [tick])

    assert first_pass == 1
    assert second_pass == 0


def test_tick_repository_bulk_upsert_deduplicates_within_same_batch(db_session) -> None:
    """Bug real, achado coletando ticks de producao contra um MySQL de
    verdade (a suite so usa SQLite): duas ticks com o MESMO (timestamp,
    bid, ask) no MESMO lote buscado do MetaTrader (nenhuma ainda no
    banco) batiam na unique constraint no INSERT -- a checagem antiga so
    comparava contra o que ja estava persistido, nunca contra o proprio
    lote sendo inserido."""
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM3B"))

    duplicate_timestamp = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    ticks = [
        RawTick(
            timestamp=duplicate_timestamp,
            bid=1.1000,
            ask=1.1002,
            last=0.0,
            volume=0.0,
            flags=6,
        ),
        RawTick(
            timestamp=duplicate_timestamp,
            bid=1.1000,
            ask=1.1002,
            last=0.0,
            volume=0.0,
            flags=6,
        ),
    ]

    inserted = TickRepository(db_session).bulk_upsert(symbol.id, ticks)

    assert inserted == 1


def test_candle_repository_get_last_open_time(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM4"))
    repo = CandleRepository(db_session)

    assert repo.get_last_open_time(symbol.id, "M1") is None

    candles = [
        RawCandle(
            open_time=datetime(2026, 1, 1, 10, i, tzinfo=UTC),
            open=1.1,
            high=1.2,
            low=1.0,
            close=1.15,
            tick_volume=10,
            spread=2,
            real_volume=0,
        )
        for i in range(3)
    ]
    repo.bulk_upsert(symbol.id, "M1", candles)

    last = repo.get_last_open_time(symbol.id, "M1")
    assert last is not None
    assert last.replace(tzinfo=UTC) == datetime(2026, 1, 1, 10, 2, tzinfo=UTC)


def test_candle_repository_get_recent_is_chronological(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM5"))
    repo = CandleRepository(db_session)

    candles = [
        RawCandle(
            open_time=datetime(2026, 1, 1, 10, i, tzinfo=UTC),
            open=1.1,
            high=1.2,
            low=1.0,
            close=1.15,
            tick_volume=10,
            spread=2,
            real_volume=0,
        )
        for i in range(5)
    ]
    repo.bulk_upsert(symbol.id, "M1", candles)

    recent = repo.get_recent(symbol.id, "M1", limit=3)
    open_times = [c.open_time.replace(tzinfo=UTC) for c in recent]
    assert open_times == sorted(open_times)
    assert len(recent) == 3


def test_tick_repository_get_last_timestamp_and_recent(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM6"))
    repo = TickRepository(db_session)

    assert repo.get_last_timestamp(symbol.id) is None

    ticks = [
        RawTick(
            timestamp=datetime(2026, 1, 1, 10, 0, i, tzinfo=UTC),
            bid=1.1000,
            ask=1.1002,
            last=0.0,
            volume=0.0,
            flags=6,
        )
        for i in range(4)
    ]
    repo.bulk_upsert(symbol.id, ticks)

    last = repo.get_last_timestamp(symbol.id)
    assert last is not None
    assert last.replace(tzinfo=UTC) == datetime(2026, 1, 1, 10, 0, 3, tzinfo=UTC)

    recent = repo.get_recent(symbol.id, limit=2)
    timestamps = [t.timestamp.replace(tzinfo=UTC) for t in recent]
    assert timestamps == sorted(timestamps)
    assert len(recent) == 2


def test_tick_repository_purge_older_than(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM7"))
    repo = TickRepository(db_session)

    now = datetime(2026, 3, 1, tzinfo=UTC)
    old_tick = RawTick(
        timestamp=now - timedelta(days=40),
        bid=1.1,
        ask=1.1002,
        last=0.0,
        volume=0.0,
        flags=6,
    )
    recent_tick = RawTick(
        timestamp=now - timedelta(days=1),
        bid=1.1,
        ask=1.1002,
        last=0.0,
        volume=0.0,
        flags=6,
    )
    repo.bulk_upsert(symbol.id, [old_tick, recent_tick])

    # Escopado por symbol_id: purge_older_than sem esse filtro e global
    # (todos os simbolos) por design (politica de retencao do sistema) —
    # aqui isolamos para nao depender de nenhum outro simbolo/teste que
    # compartilhe o mesmo banco.
    deleted = repo.purge_older_than(30, now=now, symbol_id=symbol.id)
    db_session.flush()

    assert deleted == 1
    remaining = repo.get_recent(symbol.id, limit=10)
    assert len(remaining) == 1
    assert remaining[0].timestamp.replace(tzinfo=UTC) == recent_tick.timestamp


def test_tick_repository_purge_older_than_scoped_to_symbol_does_not_affect_others(
    db_session,
) -> None:
    symbol_a = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM7A"))
    symbol_b = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM7B"))
    repo = TickRepository(db_session)

    now = datetime(2026, 3, 1, tzinfo=UTC)
    old_tick = RawTick(
        timestamp=now - timedelta(days=40),
        bid=1.1,
        ask=1.1002,
        last=0.0,
        volume=0.0,
        flags=6,
    )
    repo.bulk_upsert(symbol_a.id, [old_tick])
    repo.bulk_upsert(symbol_b.id, [old_tick])

    deleted = repo.purge_older_than(30, now=now, symbol_id=symbol_a.id)
    db_session.flush()

    assert deleted == 1
    assert repo.get_recent(symbol_a.id, limit=10) == []
    assert len(repo.get_recent(symbol_b.id, limit=10)) == 1


def test_data_quality_event_repository_bulk_insert(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM8"))
    repo = DataQualityEventRepository(db_session)

    issues = [
        DataQualityIssue("tick_negative_spread", Severity.CRITICAL, "ask < bid"),
        DataQualityIssue("feed_delay", Severity.WARNING, "atraso de 500s"),
    ]

    inserted = repo.bulk_insert(symbol.id, None, issues)
    db_session.flush()

    assert inserted == 2
    assert repo.bulk_insert(symbol.id, None, []) == 0


def test_candle_repository_summary_groups_by_symbol_and_timeframe(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM9"))
    candles = [
        RawCandle(
            open_time=datetime(2026, 1, 1, 10, i, tzinfo=UTC),
            open=1.1,
            high=1.2,
            low=1.0,
            close=1.15,
            tick_volume=10,
            spread=2,
            real_volume=0,
        )
        for i in range(3)
    ]
    CandleRepository(db_session).bulk_upsert(symbol.id, "M1", candles)

    summary = CandleRepository(db_session).summary()
    row = next(r for r in summary if r[0] == "REPO_SYM9")

    assert row[1] == "M1"
    assert row[2] == 3
    assert row[3].replace(tzinfo=UTC) == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert row[4].replace(tzinfo=UTC) == datetime(2026, 1, 1, 10, 2, tzinfo=UTC)


def test_candle_repository_summary_empty_when_no_candles(db_session) -> None:
    SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM10"))
    summary = CandleRepository(db_session).summary()
    assert not any(r[0] == "REPO_SYM10" for r in summary)


def test_tick_repository_summary_groups_by_symbol(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(_spec("REPO_SYM11"))
    ticks = [
        RawTick(
            timestamp=datetime(2026, 1, 1, 10, 0, i, tzinfo=UTC),
            bid=1.1000,
            ask=1.1002,
            last=0.0,
            volume=0.0,
            flags=6,
        )
        for i in range(4)
    ]
    TickRepository(db_session).bulk_upsert(symbol.id, ticks)

    summary = TickRepository(db_session).summary()
    row = next(r for r in summary if r[0] == "REPO_SYM11")

    assert row[1] == 4
    assert row[2].replace(tzinfo=UTC) == datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert row[3].replace(tzinfo=UTC) == datetime(2026, 1, 1, 10, 0, 3, tzinfo=UTC)
