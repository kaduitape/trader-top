"""Engine e sessoes SQLAlchemy.

Uma unica engine e criada por processo (cacheada), a partir da configuracao
tipada. Sessoes sao criadas por requisicao/tarefa via `SessionLocal` — nunca
compartilhadas entre tarefas concorrentes (cada dependencia do FastAPI, cada
comando de CLI e cada worker cria e fecha a sua propria sessao).
"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings


def create_database_engine(settings: Settings) -> Engine:
    is_sqlite = settings.database_url.startswith("sqlite")

    engine_kwargs: dict = {
        "echo": settings.db_echo,
        "pool_pre_ping": not is_sqlite,
    }

    if is_sqlite:
        # SQLite em memoria so existe pela duracao de uma conexao. StaticPool
        # garante que todas as sessoes do processo compartilhem a mesma
        # conexao/banco, o que e o comportamento esperado nos testes (ver
        # docs/assumptions.md secao 2.2 sobre uso de SQLite ate haver MySQL
        # real disponivel).
        engine_kwargs["connect_args"] = {"check_same_thread": False}
        engine_kwargs["poolclass"] = StaticPool
    else:
        engine_kwargs["pool_size"] = settings.db_pool_size
        engine_kwargs["max_overflow"] = settings.db_pool_max_overflow

    return create_engine(settings.database_url, **engine_kwargs)


@lru_cache
def get_engine() -> Engine:
    return create_database_engine(get_settings())


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
    )


def get_db() -> Generator[Session, None, None]:
    """Dependencia FastAPI: fornece uma sessao por requisicao e garante o
    fechamento mesmo em caso de excecao."""
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
