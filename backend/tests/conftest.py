"""Shared test configuration.

Environment variables are pinned here, at import time, before any test module
imports the application. Process environment beats the `.env` file in
pydantic-settings' precedence order, so these values hold regardless of what
`.env` a given developer has — the tests do not depend on local configuration.
"""

from __future__ import annotations

import os

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_cv_screener"

# setdefault, not assignment: CI can override by exporting these first.
os.environ.setdefault("DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("LOG_LEVEL", "warning")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import Settings, get_settings, load_settings  # noqa: E402
from app.llm.client import reset_llm_client_cache  # noqa: E402

get_settings.cache_clear()


def _database_reachable() -> bool:
    """True when the configured PostgreSQL accepts a connection."""
    try:
        engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 3})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except (SQLAlchemyError, OSError):
        return False
    return True


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip `requires_db` tests with a clear reason when PostgreSQL is absent.

    Skipping is honest; passing them without a database would not be.
    """
    if _database_reachable():
        return
    skip_marker = pytest.mark.skip(
        reason=f"PostgreSQL not reachable at DATABASE_URL ({os.environ['DATABASE_URL']}). "
        "Start it with: docker compose up -d --wait"
    )
    for item in items:
        if "requires_db" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _reset_llm_client_cache() -> Iterator[None]:
    """`build_llm_client` memoizes per configuration; tests change configuration."""
    reset_llm_client_cache()
    yield
    reset_llm_client_cache()


@pytest.fixture()
def settings_factory():
    """Build a `Settings` without reading a developer's `.env`.

    `env_file=None` plus explicit overrides means these values are exactly what
    the test asked for, whatever the local environment happens to contain.
    """

    def _factory(**overrides: object) -> Settings:
        base: dict[str, object] = {
            "database_url": os.environ["DATABASE_URL"],
            "app_env": "development",
            "demo_mode": True,
            "llm_model": "claude-opus-5",
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[arg-type]

    return _factory


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """A session whose writes are rolled back when the test ends.

    The services under test call `commit()` themselves — the extraction service
    has to, so its audit rows survive the exception that ends a failed run. The
    session is therefore bound to an outer transaction with
    `join_transaction_mode="create_savepoint"`: those commits land on savepoints
    inside it, are visible to the test, and disappear when the outer transaction
    is rolled back. Tests stay isolated without truncating anyone's tables.
    """
    from app.db.session import get_engine

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """A TestClient over a freshly built application."""
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def storage_root(tmp_path):
    """A throwaway upload directory, one per test.

    Uploads must never land in the developer's real storage directory during a
    test run, and each test starts from an empty one.
    """
    root = tmp_path / "uploads"
    root.mkdir()
    return root


@pytest.fixture()
def storage(storage_root):
    from app.core.storage import DocumentStorage

    return DocumentStorage(storage_root)


@pytest.fixture()
def api(db_session: Session, storage) -> Iterator[TestClient]:
    """A TestClient whose requests share the rolled-back `db_session`.

    Overriding `get_db` is what keeps API tests isolated: every request in a
    test runs against the same transaction the test can inspect, and none of it
    is committed for real. Storage is redirected to a temporary directory for
    the same reason.
    """
    from app.api.deps import get_document_storage
    from app.db.session import get_db
    from app.main import create_app

    get_settings.cache_clear()
    app = create_app()

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_document_storage] = lambda: storage
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def replay_client():
    """The replay LLM client, wired to the bundled fixtures."""
    from app.llm.client import ReplayLlmClient

    return ReplayLlmClient(model="claude-opus-5")


__all__ = ["load_settings"]
