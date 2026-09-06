"""Response contracts for the health endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness. Deliberately depends on nothing but the process itself."""

    status: Literal["ok"] = "ok"
    version: str = Field(description="Backend application version")
    app_env: str = Field(description="Configured environment name")
    demo_mode: bool = Field(description="True when LLM calls are served from fixtures")


class DatabaseHealthResponse(BaseModel):
    """Database reachability. Reported separately from liveness on purpose."""

    database: Literal["ok", "unavailable"]
    detail: str | None = Field(
        default=None,
        description="Exception class name when unavailable. Never the raw driver "
        "message, which can contain the connection URL and its password.",
    )
