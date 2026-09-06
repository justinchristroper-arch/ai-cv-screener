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


def test_live_mode_without_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live mode never falls back to fixtures, so a missing key is a boot failure."""
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


def test_live_mode_with_api_key_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")

    settings = load_settings(env_file=None)

    assert settings.demo_mode is False
    assert settings.anthropic_api_key == "sk-ant-not-a-real-key"


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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")

    assert load_settings(env_file=None).demo_mode is False
