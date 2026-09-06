"""Health endpoints.

Two endpoints, kept separate on purpose:

* ``GET /health``    — liveness. Answers "is this process up and configured?"
  It touches no external system, so a database outage cannot make the
  application look dead to a process supervisor.
* ``GET /health/db`` — dependency check. Answers "can this process reach
  PostgreSQL?" and returns 503 when it cannot.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.deps import SettingsDep
from app.db.session import get_engine
from app.schemas.api.health import DatabaseHealthResponse, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        version=__version__,
        app_env=settings.app_env,
        demo_mode=settings.demo_mode,
    )


@router.get(
    "/health/db",
    response_model=DatabaseHealthResponse,
    summary="Database reachability check",
    responses={503: {"description": "Database unreachable"}},
)
def health_database(response: Response) -> DatabaseHealthResponse:
    # The engine is used directly rather than through the get_db dependency:
    # a connection failure inside a dependency becomes an unhandled 500 before
    # this function ever runs, which is precisely the case being reported on.
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        # Only the exception class name. SQLAlchemy error strings frequently
        # embed the connection URL, password included.
        return DatabaseHealthResponse(database="unavailable", detail=type(exc).__name__)
    return DatabaseHealthResponse(database="ok")
