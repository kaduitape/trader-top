"""Testes de `SystemSettingRepository` e da orquestracao de modo do sistema.

O modo do sistema e um valor GLOBAL/singleton (uma unica linha em
`system_settings`) — diferente de outras entidades do projeto (símbolos,
candles etc.), que sempre podem ganhar um nome único por teste para evitar
colisão. Comandos da CLI (Fase 10) chamam `session.commit()` diretamente,
e o `engine` de testes é compartilhado (escopo de sessão) por toda a
suíte — então usar a fixture `db_session` (que só dá rollback) NÃO isola
os testes de modo uns dos outros nem dos testes de integração da CLI que
também mexem nessa mesma chave. Por isso os testes que dependem de um
estado inicial conhecido (`DISABLED`) usam um engine SQLite totalmente
isolado (`_isolated_session`), em vez do `db_session` compartilhado."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.database.models  # noqa: F401 - registra os modelos em Base.metadata
from app.core.enums import SystemMode
from app.core.system_mode import SystemModeError
from app.database.base import Base
from app.database.repositories.audit_log_repository import AuditLogRepository
from app.database.repositories.system_setting_repository import (
    SystemSettingRepository,
    get_current_mode,
    set_mode,
)


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


def test_generic_get_returns_none_for_unknown_key(db_session) -> None:
    repo = SystemSettingRepository(db_session)
    assert repo.get("does_not_exist_xyz") is None


def test_generic_set_then_get_round_trips(db_session) -> None:
    repo = SystemSettingRepository(db_session)
    repo.set("my_key_xyz", "my_value", description="test")
    assert repo.get("my_key_xyz") == "my_value"


def test_generic_set_overwrites_existing_value(db_session) -> None:
    repo = SystemSettingRepository(db_session)
    repo.set("my_key_xyz2", "first")
    repo.set("my_key_xyz2", "second")
    assert repo.get("my_key_xyz2") == "second"


def test_get_current_mode_defaults_to_disabled_when_unset(isolated_session) -> None:
    assert get_current_mode(isolated_session) == SystemMode.DISABLED


def test_set_mode_persists_and_is_read_back(isolated_session) -> None:
    set_mode(isolated_session, SystemMode.DATA_ONLY, reason="teste")
    assert get_current_mode(isolated_session) == SystemMode.DATA_ONLY


def test_set_mode_writes_audit_log_entry(isolated_session) -> None:
    set_mode(isolated_session, SystemMode.DATA_ONLY, reason="motivo especifico do teste")

    entries = AuditLogRepository(isolated_session).list_recent(limit=5)
    assert any(
        e.action == "system_mode_change" and "motivo especifico do teste" in (e.detail or "")
        for e in entries
    )


def test_set_mode_rejects_invalid_transition_without_side_effects(isolated_session) -> None:
    with pytest.raises(SystemModeError):
        set_mode(isolated_session, SystemMode.BACKTEST, reason="pulando estados")

    # Nao deve ter persistido nada -- ainda em DISABLED.
    assert get_current_mode(isolated_session) == SystemMode.DISABLED


def test_set_mode_sequential_progression_through_forward_order(isolated_session) -> None:
    for target in (
        SystemMode.DATA_ONLY,
        SystemMode.BACKTEST,
        SystemMode.REPLAY,
        SystemMode.PAPER,
        SystemMode.DEMO,
    ):
        set_mode(isolated_session, target, reason="avancando")
        assert get_current_mode(isolated_session) == target
