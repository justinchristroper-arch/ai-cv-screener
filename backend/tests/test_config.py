"""Configuration validation.

The behaviour under test is fail-fast: a misconfigured process must refuse to
start with a message naming the offending variable, rather than failing later
with an obscure error at the first database call or LLM call.

Every test passes `env_file=None` so a developer's local `.env` cannot
influence the outcome.
"""

from __future__ import annotations

import pytest

from app.core.config import ConfigurationError, load_settings

VALID_DB_URL = "postgresql+psycopg://user:pass@localhost:5432/testdb"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test from an environment with no application variables set."""
    for name in (
        "DATABASE_URL",
        "APP_ENV",
        "LOG_LEVEL",
        "DEMO_MODE",
        "ANTHROPIC_API_KEY",
        "LLM_MODEL",
        "LLM_PROVIDER",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "CORS_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_missing_database_url_fails_fast() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    assert "DATABASE_URL" in message
    assert "required environment variable is not set" in message
    # The message must tell the developer what to do about it.
    assert ".env.example" in message


def test_valid_configuration_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)

    settings = load_settings(env_file=None)

    assert settings.database_url == VALID_DB_URL
    assert settings.demo_mode is True, "demo mode must be the default"
    assert settings.app_env == "development"


def test_the_local_provider_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh clone should reach a model on this machine, not an account."""
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)

    settings = load_settings(env_file=None)

    assert settings.llm_provider == "ollama"
    assert settings.ollama_base_url.startswith("http://")
    assert settings.ollama_model


def test_the_cloud_provider_without_an_api_key_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider never falls back to fixtures, so a missing key is a boot failure."""
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


def test_the_local_provider_needs_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the *selected* provider is checked.

    Requiring a key for a cloud service nobody selected would defeat the point
    of running locally.
    """
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")

    settings = load_settings(env_file=None)

    assert settings.demo_mode is False
    assert settings.llm_provider == "ollama"
    assert settings.anthropic_api_key is None


def test_the_cloud_provider_with_api_key_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")

    settings = load_settings(env_file=None)

    assert settings.demo_mode is False
    assert settings.anthropic_api_key == "sk-ant-not-a-real-key"


def test_an_unknown_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not silently fall through to a default."""
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert "LLM_PROVIDER" in str(excinfo.value)


def test_invalid_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert "APP_ENV" in str(excinfo.value)


def test_cors_origins_are_split_on_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173 ,, ")

    settings = load_settings(env_file=None)

    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_demo_mode_accepts_string_booleans(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` files carry strings; "false" must not be truthy."""
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")

    assert load_settings(env_file=None).demo_mode is False
