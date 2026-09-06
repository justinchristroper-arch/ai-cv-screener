"""Typed application settings, loaded from the environment.

The application **fails fast**: `load_settings()` raises `ConfigurationError`
with a message naming every offending variable, rather than letting a missing
value propagate into a database URL or an API call and surface later as an
obscure `NoneType` error.

Precedence (pydantic-settings): process environment > `.env` file > defaults.
That ordering is what lets the test suite pin configuration deterministically
without depending on whatever `.env` a given developer happens to have.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class ConfigurationError(RuntimeError):
    """Raised when environment configuration is missing or invalid."""


class Settings(BaseSettings):
    """Every configurable value the backend reads, with its type."""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "production"] = "development"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # Required. No default — an unset DATABASE_URL is a startup failure, not a
    # value to guess at.
    database_url: str

    # When true, all LLM calls are served from recorded fixtures: no key, no
    # cost, identical results every run. See docs/architecture.md section 4.2.
    demo_mode: bool = True
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5"

    # Comma-separated. Kept as a string rather than list[str] on purpose:
    # pydantic-settings parses complex types from the environment as JSON, so a
    # plain comma-separated value in a list[str] field fails to parse. Splitting
    # ourselves is the boring, predictable option.
    cors_allowed_origins: str = "http://localhost:5173"

    max_upload_size_mb: int = 10
    max_files_per_batch: int = 25
    max_pdf_pages: int = 20

    @property
    def cors_origins(self) -> list[str]:
        """`cors_allowed_origins` split into a list, blanks removed."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _live_mode_requires_api_key(self) -> Settings:
        """Live mode without a key would fail at the first LLM call, not at boot.

        Demo mode never falls back to a live call and live mode never falls back
        to a fixture (docs/architecture.md section 4.2), so the key requirement
        is knowable at startup and is enforced here.
        """
        if not self.demo_mode and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when DEMO_MODE is false")
        return self


def _format_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic ValidationError into something a developer can act on."""
    lines = ["Invalid application configuration:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        name = location.upper() if location else "CONFIGURATION"
        if error["type"] == "missing":
            lines.append(f"  - {name}: required environment variable is not set")
        else:
            lines.append(f"  - {name}: {error['msg']}")
    lines.append("")
    lines.append(
        f"Copy .env.example to .env and fill in the values. Expected at: {DEFAULT_ENV_FILE}"
    )
    return "\n".join(lines)


def load_settings(env_file: Path | str | None = DEFAULT_ENV_FILE) -> Settings:
    """Build a `Settings`, converting validation failures into `ConfigurationError`.

    `env_file=None` skips the dotenv file entirely — used by the test suite so
    that a developer's local `.env` cannot influence test outcomes.
    """
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(exc)) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Call `get_settings.cache_clear()` in tests."""
    return load_settings()
