"""Application factory and ASGI entry point.

Importing this module loads settings, so a missing or invalid environment
variable fails the process at startup with a readable message rather than at
the first request.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import health, jobs, requirements
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    app = FastAPI(
        title="AI CV Screener API",
        version=__version__,
        description=(
            "Decision-support API for CV screening. Job description processing and "
            "the human confirmation gate are implemented; CV upload, matching, "
            "scoring and ranking are not — see docs/roadmap.md."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Business-rule failures become structured responses here, so no route has
    # to translate a domain error into HTTP itself.
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(requirements.router)

    logger.info(
        "AI CV Screener backend %s starting (env=%s, demo_mode=%s)",
        __version__,
        settings.app_env,
        settings.demo_mode,
    )
    return app


app = create_app()
