"""Configuracao global de testes.

A suite principal nunca exige um MySQL real nem um terminal MetaTrader
instalado (ver docs/assumptions.md secao 2.2). `APP_ENV=test` faz
`Settings.database_url` apontar para SQLite em memoria automaticamente.

As variaveis de ambiente sao definidas aqui, no escopo de import do modulo,
para que ja estejam presentes antes de qualquer teste importar `app.*` e
antes da primeira chamada (cacheada) a `get_settings()`.
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("APP_DEBUG", "true")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
# Isola a suite de um bypass de login local (.env de desenvolvimento) --
# a suite sempre exige autenticacao real, independente do que estiver
# configurado no `.env` da maquina de quem roda os testes.
os.environ.setdefault("DASHBOARD_AUTH_DISABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.database.models  # noqa: E402,F401
from app.core.config import get_settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.session import get_engine, get_session_factory  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _settings_cache_cleared() -> None:
    get_settings.cache_clear()
    get_settings()


@pytest.fixture(scope="session")
def engine():
    eng = get_engine()
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture
def db_session(engine) -> Session:
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(engine) -> TestClient:
    from app.api.app import app

    with TestClient(app) as test_client:
        yield test_client
