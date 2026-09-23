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
from urllib.parse import urlparse

from pydantic import SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

#: Hosts a hosted-provider base URL (DeepSeek, OpenRouter) may reach over plain
#: http. Traffic to them never leaves this machine, so the API key does not
#: cross a network in the clear.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


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
    # cost, no server, identical results every run. This is the outer switch and
    # it is checked first — demo mode never reaches a provider of any kind.
    # See docs/architecture.md section 4.2.
    demo_mode: bool = True

    # Which provider answers when demo mode is off. Local by default: a fresh
    # clone should work with no account and no paid API (ADR-0011). DeepSeek and
    # OpenRouter are the hosted options, for a deployment where no machine can
    # run a model.
    llm_provider: Literal["ollama", "anthropic", "deepseek", "openrouter"] = "ollama"

    #: The model a *fixture* was recorded against. It is part of the fixture key,
    #: so changing it invalidates every recording in `app/llm/fixtures/` — which
    #: is why it is separate from the model a live provider actually runs.
    llm_model: str = "claude-opus-5"

    # --- Ollama (LLM_PROVIDER=ollama) -------------------------------------
    #
    # Operator configuration, never user input: nothing in the API lets a caller
    # choose where a request goes. The URL is still validated below, because a
    # setting that points this application at an arbitrary host turns it into a
    # request forwarder for whatever a prompt happens to contain.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    #: Generous on purpose. A 7B model on a laptop CPU can take a minute on a
    #: long CV, and a timeout that fires mid-answer looks like a broken product.
    ollama_timeout_seconds: float = 300.0

    # --- Anthropic (LLM_PROVIDER=anthropic) -------------------------------
    anthropic_api_key: str | None = None

    # --- DeepSeek (LLM_PROVIDER=deepseek) ---------------------------------
    #
    # A hosted API. Every call sends the criteria and the CV text to DeepSeek,
    # and costs money.
    #
    # The key is a SecretStr so that it cannot be printed by accident: str(),
    # repr() and a logged Settings object all show '**********'. It is read from
    # the environment or `.env` only; nothing in the repository holds one.
    deepseek_api_key: SecretStr | None = None
    #: Operator configuration, like OLLAMA_BASE_URL, and validated for the same
    #: reason -- plus one more: the key travels with every request to this URL.
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    #: The whole call, time spent waiting in DeepSeek's queue included. While a
    #: request waits, DeepSeek holds the connection open with empty lines and
    #: gives up only after ten minutes, so without this one call could hold a
    #: screening that long.
    deepseek_timeout_seconds: float = 180.0

    # --- OpenRouter (LLM_PROVIDER=openrouter) -----------------------------
    #
    # A gateway, not a model vendor: OpenRouter forwards each call to one of
    # the upstream providers that host OPENROUTER_MODEL. Every call sends the
    # criteria and the CV text to OpenRouter and on to that provider, and costs
    # money. Deliberately separate from DEEPSEEK_* even when the model is a
    # DeepSeek one: an OpenRouter key does not work against api.deepseek.com,
    # and the name of the variable is what says where the key is sent.
    #
    # A SecretStr for the same reason DEEPSEEK_API_KEY is. Swapping one key for
    # another is a change to the environment only, never to this file.
    openrouter_api_key: SecretStr | None = None
    #: Validated like DEEPSEEK_BASE_URL: the key travels with every request.
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    #: An OpenRouter model slug. Pinned rather than a `~...-latest` alias, so the
    #: model recorded against every call does not change underneath it.
    openrouter_model: str = "deepseek/deepseek-v4.1-flash"
    #: The whole call, including any time OpenRouter spends failing over between
    #: upstream providers.
    openrouter_timeout_seconds: float = 180.0

    # Comma-separated. Kept as a string rather than list[str] on purpose:
    # pydantic-settings parses complex types from the environment as JSON, so a
    # plain comma-separated value in a list[str] field fails to parse. Splitting
    # ourselves is the boring, predictable option.
    cors_allowed_origins: str = "http://localhost:5173"

    max_upload_size_mb: int = 10
    max_files_per_batch: int = 25
    max_pdf_pages: int = 20

    # A ceiling on the whole request, checked from Content-Length before the
    # body is read. Sized to hold a full batch of maximum-size CVs plus
    # multipart overhead, so it never refuses a request the per-file limits
    # would have accepted.
    max_request_body_mb: int = 300

    # A per-client cap on the endpoints that cost money or write files. In-process
    # only: see app/api/limits.py for what that does and does not buy.
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 30

    # Where uploaded CV files are written. Outside any tracked source directory
    # and git-ignored: uploaded documents are personal data and must never
    # reach the repository. Stored paths in the database are relative to this
    # root, so the root can move without a data migration.
    upload_storage_dir: Path = REPO_ROOT / "var" / "uploads"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_request_body_bytes(self) -> int:
        return self.max_request_body_mb * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        """`cors_allowed_origins` split into a list, blanks removed."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def provider_model(self) -> str:
        """The model the selected provider runs when demo mode is off.

        Each provider names its model in its own setting, so that choosing one
        can never rename the fixture key (`llm_model`). Anthropic is the
        exception: it predates that split and still runs `llm_model`.
        """
        if self.llm_provider == "ollama":
            return self.ollama_model
        if self.llm_provider == "deepseek":
            return self.deepseek_model
        if self.llm_provider == "openrouter":
            return self.openrouter_model
        return self.llm_model

    @model_validator(mode="after")
    def _selected_provider_is_configured(self) -> Settings:
        """Whatever the selected provider needs, it needs before the first request.

        Demo mode never falls back to a provider and a provider never falls back
        to a fixture (docs/architecture.md section 4.2), so what each one
        requires is knowable at startup and is enforced here rather than
        surfacing as a confusing failure on someone's first click.

        Only the *selected* provider is checked. Running locally must not
        require an API key for a cloud service nobody asked for.
        """
        if self.demo_mode:
            return self

        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when DEMO_MODE is false and "
                "LLM_PROVIDER is anthropic"
            )

        if self.llm_provider == "ollama":
            _validate_ollama_base_url(self.ollama_base_url)
            if not self.ollama_model.strip():
                raise ValueError("OLLAMA_MODEL must name a model, e.g. qwen2.5:7b-instruct")

        if self.llm_provider == "deepseek":
            _validate_deepseek_api_key(self.deepseek_api_key)
            _validate_deepseek_base_url(self.deepseek_base_url)
            if not self.deepseek_model.strip():
                raise ValueError("DEEPSEEK_MODEL must name a model, e.g. deepseek-flash")
            if self.deepseek_timeout_seconds <= 0:
                raise ValueError("DEEPSEEK_TIMEOUT_SECONDS must be a positive number of seconds")

        if self.llm_provider == "openrouter":
            _validate_openrouter_api_key(self.openrouter_api_key)
            _validate_openrouter_base_url(self.openrouter_base_url)
            if not self.openrouter_model.strip():
                raise ValueError(
                    "OPENROUTER_MODEL must name a model, e.g. deepseek/deepseek-v4.1-flash"
                )
            if self.openrouter_timeout_seconds <= 0:
                raise ValueError("OPENROUTER_TIMEOUT_SECONDS must be a positive number of seconds")

        return self


def _validate_ollama_base_url(url: str) -> None:
    """Refuse a base URL that would make this application a request forwarder.

    `OLLAMA_BASE_URL` is operator configuration read from the environment, and
    no API path lets a caller influence it — so this is not the classic SSRF
    shape where an attacker supplies the address. It is checked anyway, because
    the *consequence* of a wrong value is the same either way: every prompt,
    including CV text, is posted to whatever host is named.

    What this rejects is malformed or obviously wrong configuration: a scheme
    that is not HTTP, a missing host, or credentials embedded in the URL (which
    would then be logged by anything that logs the URL). What it deliberately
    does **not** do is restrict the host to localhost. Running Ollama on another
    machine on a home network, or in a sibling container, is a legitimate setup,
    and a check that forbade it would be security theatre that broke real use.
    The residual risk is recorded in docs/security.md.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"OLLAMA_BASE_URL must start with http:// or https://, got {parsed.scheme or url!r}"
        )
    if not parsed.hostname:
        raise ValueError(f"OLLAMA_BASE_URL has no host: {url!r}")
    if parsed.username or parsed.password:
        raise ValueError(
            "OLLAMA_BASE_URL must not contain credentials. Ollama needs none, and a "
            "URL carrying them ends up in logs."
        )


def _validate_deepseek_api_key(key: SecretStr | None) -> None:
    """Present, and nothing but the key. No message here ever contains it.

    A key pasted with a trailing line break, a space or a typographic quote
    would otherwise fail at the first request -- and Python's HTTP client names
    the offending header value in that error, which would put the key in a log.
    """
    value = key.get_secret_value() if key is not None else ""
    if not value.strip():
        raise ValueError(
            "DEEPSEEK_API_KEY is required when DEMO_MODE is false and LLM_PROVIDER is deepseek"
        )
    if not value.isascii() or any(char.isspace() or not char.isprintable() for char in value):
        raise ValueError(
            "DEEPSEEK_API_KEY contains a space, a line break or a character that is not "
            "plain ASCII. Paste the key alone, with nothing before or after it."
        )


def _validate_deepseek_base_url(url: str) -> None:
    """Where the key and every CV are sent. HTTPS, except to this machine.

    The checks `OLLAMA_BASE_URL` gets -- a scheme, a host, no credentials -- plus
    one more: the API key rides in a header on every request, so a URL that
    would send it across a network unencrypted is refused. Plain http is allowed
    for loopback only (a local proxy, or a test double), which crosses no
    network. The host is deliberately not pinned to api.deepseek.com: a gateway
    in front of the API is a legitimate setup.

    Messages name the scheme at most, never the URL, so that a malformed value
    carrying credentials is not repeated into a log.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"DEEPSEEK_BASE_URL must start with https://, got {parsed.scheme or '(no scheme)'!r}"
        )
    if parsed.username or parsed.password:
        raise ValueError(
            "DEEPSEEK_BASE_URL must not contain credentials. The key belongs in "
            "DEEPSEEK_API_KEY, and a URL carrying one ends up in logs."
        )
    if not parsed.hostname:
        raise ValueError("DEEPSEEK_BASE_URL has no host, e.g. https://api.deepseek.com")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError(
            "DEEPSEEK_BASE_URL must use https://. The API key is sent with every "
            "request and must not cross the network unencrypted."
        )


def _validate_openrouter_api_key(key: SecretStr | None) -> None:
    """Present, and nothing but the key. No message here ever contains it.

    The same reasoning as `_validate_deepseek_api_key`: a key pasted with a
    trailing line break or a typographic quote would otherwise fail at the first
    request, in an error that quotes the header value.
    """
    value = key.get_secret_value() if key is not None else ""
    if not value.strip():
        raise ValueError(
            "OPENROUTER_API_KEY is required when DEMO_MODE is false and LLM_PROVIDER is openrouter"
        )
    if not value.isascii() or any(char.isspace() or not char.isprintable() for char in value):
        raise ValueError(
            "OPENROUTER_API_KEY contains a space, a line break or a character that is not "
            "plain ASCII. Paste the key alone, with nothing before or after it."
        )


def _validate_openrouter_base_url(url: str) -> None:
    """Where the key and every CV are sent. HTTPS, except to this machine.

    The same checks as `_validate_deepseek_base_url`, for the same reasons. The
    host is not pinned to openrouter.ai; messages name the scheme at most,
    never the URL.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"OPENROUTER_BASE_URL must start with https://, got {parsed.scheme or '(no scheme)'!r}"
        )
    if parsed.username or parsed.password:
        raise ValueError(
            "OPENROUTER_BASE_URL must not contain credentials. The key belongs in "
            "OPENROUTER_API_KEY, and a URL carrying one ends up in logs."
        )
    if not parsed.hostname:
        raise ValueError("OPENROUTER_BASE_URL has no host, e.g. https://openrouter.ai/api/v1")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError(
            "OPENROUTER_BASE_URL must use https://. The API key is sent with every "
            "request and must not cross the network unencrypted."
        )


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
        # `from None`, not `from exc`. Pydantic's own message repeats the raw
        # input -- every setting, strings cut short but with their first and
        # last characters intact -- and a traceback would print it underneath
        # this one, putting fragments of API keys and the database password into
        # a log. The message built here already names every offending variable.
        raise ConfigurationError(_format_validation_error(exc)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Call `get_settings.cache_clear()` in tests."""
    return load_settings()
