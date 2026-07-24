"""`drift_events` é uma tabela global (sem chave natural por teste, como
o nome de símbolo dá a outras tabelas) — testes que dependem de uma
contagem exata usam um engine SQLite isolado (`isolated_session`), não o
`db_session` compartilhado por toda a suíte (mesma razão da Fase 10 em
`test_system_setting_repository.py`)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.database.models  # noqa: F401 - registra os modelos em Base.metadata
from app.database.base import Base
from app.database.repositories.drift_event_repository import DriftEventRepository


@pytest.fixture
def isolated_session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_record_creates_event_with_all_fields(db_session) -> None:
    repo = DriftEventRepository(db_session)
    event = repo.record(
        drift_type="FEATURE",
        severity="WARNING",
        metric_name="rsi_14",
        current_value=0.15,
        detail="PSI 0.15 acima do limiar de aviso (0.10).",
        model_version="20260101T000000000000",
        symbol_id=None,
        timeframe="M1",
        baseline_value=0.0,
        threshold_value=0.10,
    )

    assert event.id is not None
    assert event.drift_type == "FEATURE"
    assert event.severity == "WARNING"
    assert event.metric_name == "rsi_14"


def test_record_allows_minimal_fields(db_session) -> None:
    repo = DriftEventRepository(db_session)
    event = repo.record(
        drift_type="DATA_FEED",
        severity="CRITICAL",
        metric_name="feed_age_seconds",
        current_value=999.0,
        detail="feed atrasado",
    )
    assert event.model_version is None
    assert event.symbol_id is None


def test_list_recent_orders_by_detected_at_descending(isolated_session) -> None:
    repo = DriftEventRepository(isolated_session)
    for i in range(3):
        repo.record(
            drift_type="PERFORMANCE",
            severity="WARNING",
            metric_name=f"metric_{i}",
            current_value=float(i),
            detail="teste",
        )

    events = repo.list_recent(limit=10)
    assert len(events) == 3
    metric_names = [e.metric_name for e in events]
    assert (
        metric_names.index("metric_2")
        < metric_names.index("metric_1")
        < metric_names.index("metric_0")
    )


def test_list_recent_respects_limit(isolated_session) -> None:
    repo = DriftEventRepository(isolated_session)
    for i in range(5):
        repo.record(
            drift_type="CALIBRATION",
            severity="WARNING",
            metric_name=f"m{i}",
            current_value=1.0,
            detail="x",
        )
    assert len(repo.list_recent(limit=2)) == 2
