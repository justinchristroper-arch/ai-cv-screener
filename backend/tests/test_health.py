"""Health endpoint behaviour.

The distinction under test: liveness must not depend on the database, and a
database outage must be reported as 503 rather than as an unhandled 500.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app import __version__


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["app_env"] == "development"
    assert body["demo_mode"] is True


def test_health_does_not_touch_the_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database outage must not make the process look dead to a supervisor."""

    def _explode() -> None:
        raise AssertionError("/health must not open a database connection")

    monkeypatch.setattr("app.api.routes.health.get_engine", _explode)

    assert client.get("/health").status_code == 200


def test_health_db_reports_503_when_database_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _explode() -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr("app.api.routes.health.get_engine", _explode)

    response = client.get("/health/db")

    assert response.status_code == 503
    body = response.json()
    assert body["database"] == "unavailable"
    # Only the exception class name: SQLAlchemy messages can embed the
    # connection URL, password included.
    assert body["detail"] == "OperationalError"
    assert "connection refused" not in response.text


@pytest.mark.requires_db
def test_health_db_reports_ok_against_a_live_database(client: TestClient) -> None:
    response = client.get("/health/db")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_openapi_schema_is_generated(client: TestClient) -> None:
    """Proves the app is wired up, not merely importable."""
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/health/db" in paths
