"""Domain errors and their HTTP mapping.

Services raise these; the API layer never constructs an HTTP error itself for a
business-rule violation. That keeps the rules in one place and stops two routes
from disagreeing about what "already confirmed" means.

Responses are structured and deliberately thin: an error ``code`` a client can
branch on and a human-readable ``message``. No stack traces, no SQL, no
provider text, no file paths — docs/architecture.md section 8.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Base for every business-rule failure the API converts to a response."""

    #: HTTP status this maps to.
    status_code: int = status.HTTP_400_BAD_REQUEST
    #: Stable, machine-readable identifier. Clients branch on this, not prose.
    code: str = "domain_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(DomainError):
    """The request is well-formed but the resource is in the wrong state.

    Used for every confirmation-gate violation: editing frozen fields, adding
    or removing a requirement on a confirmed job, extracting without a job
    description.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class RequirementsNotConfirmedError(DomainError):
    """A downstream consumer asked for requirements the human has not confirmed.

    This is the server-side half of the gate in ADR-0004. It exists so that the
    refusal is a specific, catchable, testable condition rather than an empty
    list that a caller might mistake for "this job has no requirements".
    """

    status_code = status.HTTP_409_CONFLICT
    code = "requirements_not_confirmed"


class ExtractionFailedError(DomainError):
    """The model produced output this application could not use.

    502 rather than 500: the failure is in an upstream dependency's response,
    not in this service. Nothing partial is persisted when this is raised.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "extraction_failed"


class LlmUnavailableError(DomainError):
    """The model could not be reached, or demo mode has no fixture for this input."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "llm_unavailable"


def register_exception_handlers(app: FastAPI) -> None:
    """Map `DomainError` to a structured response; catch everything else safely."""

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        body: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.details:
            body["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content=body)

    # Imported here rather than at module scope: `api.limits` imports Starlette
    # middleware, and `core` is the layer everything else depends on, so it must
    # not depend on the API layer at import time.
    from app.api.limits import RateLimitedError

    @app.exception_handler(RateLimitedError)
    async def _rate_limited_handler(_: Request, exc: RateLimitedError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(exc.retry_after_seconds)},
            content={
                "code": "rate_limited",
                "message": (
                    f"Too many requests. This endpoint allows {exc.limit} per minute; "
                    f"try again in about {exc.retry_after_seconds} seconds."
                ),
                "details": {
                    "limit_per_minute": exc.limit,
                    "retry_after_seconds": exc.retry_after_seconds,
                },
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # An error id correlates the opaque client response with the full
        # traceback in the server log. The client learns nothing about the
        # internals; the operator loses nothing.
        error_id = uuid.uuid4().hex[:12]
        logger.exception("Unhandled error [%s]", error_id, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": "internal_error",
                "message": "An internal error occurred.",
                "error_id": error_id,
            },
        )
