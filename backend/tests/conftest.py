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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.core.config import get_settings  # noqa: E402

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


@pytest.fixture()
def client() -> TestClient:
    """A TestClient over a freshly built application."""
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
