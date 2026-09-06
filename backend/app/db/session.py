"""Database engine and session management.

A synchronous engine is used deliberately. The pipeline's latency is dominated
by LLM calls, not by the database, and FastAPI runs synchronous endpoint
functions in a threadpool. Async SQLAlchemy would add an async driver, async
sessions and async test plumbing to buy throughput this workload does not need.

The engine is created lazily so that importing this module does not require a
reachable database — which is what lets the configuration tests run offline.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,  # drop connections killed by a container restart
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _session_factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Dispose of the engine and factory. For tests that change configuration."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
