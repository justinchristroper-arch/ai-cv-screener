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
    llm_provider: str = Field(
        description=(
            "Which provider answers when demo mode is off: 'ollama' for a model running "
            "on the server's own machine, 'deepseek' or 'anthropic' for a hosted API. "
            "Surfaced so the UI can tell a user where their documents actually go rather "
            "than guessing."
        )
    )
    llm_model: str = Field(
        description=(
            "The model that will answer. In demo mode this is the model the recordings "
            "were made against, which is what a replayed answer honestly is."
        )
    )


class DatabaseHealthResponse(BaseModel):
    """Database reachability. Reported separately from liveness on purpose."""

    database: Literal["ok", "unavailable"]
    detail: str | None = Field(
        default=None,
        description="Exception class name when unavailable. Never the raw driver "
        "message, which can contain the connection URL and its password.",
    )
