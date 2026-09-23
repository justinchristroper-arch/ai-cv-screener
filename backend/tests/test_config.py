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
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_TIMEOUT_SECONDS",
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_MODEL",
        "OPENROUTER_TIMEOUT_SECONDS",
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


# --------------------------------------------------------------------------
# DeepSeek (LLM_PROVIDER=deepseek)
# --------------------------------------------------------------------------

#: Obviously fake, and without the `sk-` prefix real keys carry.
FAKE_DEEPSEEK_KEY = "test-deepseek-key-not-real-0123456789"


def _select_deepseek(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def test_deepseek_without_an_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """No fallback to fixtures, so a missing key is a boot failure that names it."""
    _select_deepseek(monkeypatch)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert "DEEPSEEK_API_KEY" in str(excinfo.value)


def test_the_empty_placeholder_from_env_example_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.env.example` ships `DEEPSEEK_API_KEY=` empty, so a copied file fails fast."""
    _select_deepseek(monkeypatch, DEEPSEEK_API_KEY="")

    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY is required"):
        load_settings(env_file=None)


def test_deepseek_with_an_api_key_is_accepted_and_the_key_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_deepseek(monkeypatch, DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY)

    settings = load_settings(env_file=None)

    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_api_key.get_secret_value() == FAKE_DEEPSEEK_KEY
    # A Settings object printed or logged by accident shows asterisks.
    assert FAKE_DEEPSEEK_KEY not in repr(settings)
    assert FAKE_DEEPSEEK_KEY not in str(settings)


def test_deepseek_defaults_are_the_documented_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    _select_deepseek(monkeypatch, DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY)

    settings = load_settings(env_file=None)

    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-flash"
    assert settings.deepseek_timeout_seconds == 180.0
    assert settings.provider_model == "deepseek-flash"


@pytest.mark.parametrize(
    "key",
    [
        "   ",
        f"{FAKE_DEEPSEEK_KEY}\n",
        f"{FAKE_DEEPSEEK_KEY} trailing",
        f"“{FAKE_DEEPSEEK_KEY}”",
    ],
    ids=["blank", "line-break", "space", "typographic-quotes"],
)
def test_a_key_pasted_with_extra_characters_is_refused_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Python's HTTP client would quote such a header value in its own error."""
    _select_deepseek(monkeypatch, DEEPSEEK_API_KEY=key)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert "DEEPSEEK_API_KEY" in str(excinfo.value)
    assert FAKE_DEEPSEEK_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://api.deepseek.com", "https://"),
        ("ftp://api.deepseek.com", "https://"),
        ("not-a-url", "https://"),
        ("https://", "no host"),
        ("https://user:secret@api.deepseek.com", "credentials"),
    ],
)
def test_a_deepseek_url_that_would_expose_the_key_is_refused(
    monkeypatch: pytest.MonkeyPatch, url: str, reason: str
) -> None:
    _select_deepseek(monkeypatch, DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY, DEEPSEEK_BASE_URL=url)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert reason in str(excinfo.value)
    assert "secret" not in str(excinfo.value)


def test_plain_http_is_allowed_to_this_machine_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback crosses no network: a local proxy, or a test double."""
    _select_deepseek(
        monkeypatch, DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY, DEEPSEEK_BASE_URL="http://127.0.0.1:8123"
    )

    assert load_settings(env_file=None).deepseek_base_url == "http://127.0.0.1:8123"


@pytest.mark.parametrize(
    ("name", "value"),
    [("DEEPSEEK_MODEL", "  "), ("DEEPSEEK_TIMEOUT_SECONDS", "0")],
)
def test_a_blank_model_or_a_non_positive_timeout_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _select_deepseek(monkeypatch, DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY, **{name: value})

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert name in str(excinfo.value)


def test_no_configuration_error_carries_the_key_even_in_its_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic's own message repeats the raw input, so it is not chained on.

    The key is valid and something else is wrong, which is exactly when a
    chained ValidationError would have printed the start and end of the key.
    """
    import traceback

    _select_deepseek(
        monkeypatch, DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY, DEEPSEEK_BASE_URL="http://example.invalid"
    )

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    printed = "".join(traceback.format_exception(excinfo.value))
    assert FAKE_DEEPSEEK_KEY[:12] not in printed
    assert FAKE_DEEPSEEK_KEY[-8:] not in printed
    assert excinfo.value.__cause__ is None


def test_demo_mode_needs_no_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Demo mode is the outer switch: selecting DeepSeek there asks for nothing."""
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")

    settings = load_settings(env_file=None)

    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_api_key is None


def test_ollama_needs_no_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the selected provider is checked; local development owes DeepSeek nothing."""
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    settings = load_settings(env_file=None)

    assert settings.deepseek_api_key is None
    assert settings.provider_model == settings.ollama_model


# --------------------------------------------------------------------------
# OpenRouter (LLM_PROVIDER=openrouter)
# --------------------------------------------------------------------------

#: Obviously fake, and without the prefix real OpenRouter keys carry.
FAKE_OPENROUTER_KEY = "test-openrouter-key-not-real-0123456789"


def _select_openrouter(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def test_openrouter_without_an_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _select_openrouter(monkeypatch)

    with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY is required"):
        load_settings(env_file=None)


def test_openrouter_never_borrows_the_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in DEEPSEEK_API_KEY is for api.deepseek.com, and is not read here."""
    _select_openrouter(monkeypatch, DEEPSEEK_API_KEY="test-deepseek-key-not-real-0123456789")

    with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY is required"):
        load_settings(env_file=None)


def test_the_empty_openrouter_placeholder_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.env.example` ships `OPENROUTER_API_KEY=` empty, so a copied file fails fast."""
    _select_openrouter(monkeypatch, OPENROUTER_API_KEY="")

    with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY is required"):
        load_settings(env_file=None)


def test_openrouter_with_an_api_key_is_accepted_and_the_key_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_openrouter(monkeypatch, OPENROUTER_API_KEY=FAKE_OPENROUTER_KEY)

    settings = load_settings(env_file=None)

    assert settings.llm_provider == "openrouter"
    assert settings.openrouter_api_key.get_secret_value() == FAKE_OPENROUTER_KEY
    assert FAKE_OPENROUTER_KEY not in repr(settings)
    assert FAKE_OPENROUTER_KEY not in str(settings)


def test_openrouter_defaults_are_the_documented_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    _select_openrouter(monkeypatch, OPENROUTER_API_KEY=FAKE_OPENROUTER_KEY)

    settings = load_settings(env_file=None)

    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.openrouter_model == "deepseek/deepseek-v4.1-flash"
    assert settings.openrouter_timeout_seconds == 180.0
    assert settings.provider_model == "deepseek/deepseek-v4.1-flash"


def test_a_different_openrouter_key_needs_only_a_different_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing a shared key with your own is configuration, not a code change."""
    _select_openrouter(monkeypatch, OPENROUTER_API_KEY=FAKE_OPENROUTER_KEY)
    first = load_settings(env_file=None).openrouter_api_key.get_secret_value()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-other-key-not-real-98765")
    second = load_settings(env_file=None).openrouter_api_key.get_secret_value()

    assert first == FAKE_OPENROUTER_KEY
    assert second == "test-openrouter-other-key-not-real-98765"


@pytest.mark.parametrize(
    "key",
    [
        "   ",
        f"{FAKE_OPENROUTER_KEY}\n",
        f"{FAKE_OPENROUTER_KEY} trailing",
        f"“{FAKE_OPENROUTER_KEY}”",
    ],
    ids=["blank", "line-break", "space", "typographic-quotes"],
)
def test_an_openrouter_key_pasted_with_extra_characters_is_refused_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    _select_openrouter(monkeypatch, OPENROUTER_API_KEY=key)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert "OPENROUTER_API_KEY" in str(excinfo.value)
    assert FAKE_OPENROUTER_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://openrouter.ai/api/v1", "https://"),
        ("ftp://openrouter.ai/api/v1", "https://"),
        ("not-a-url", "https://"),
        ("https://", "no host"),
        ("https://user:secret@openrouter.ai/api/v1", "credentials"),
    ],
)
def test_an_openrouter_url_that_would_expose_the_key_is_refused(
    monkeypatch: pytest.MonkeyPatch, url: str, reason: str
) -> None:
    _select_openrouter(monkeypatch, OPENROUTER_API_KEY=FAKE_OPENROUTER_KEY, OPENROUTER_BASE_URL=url)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert reason in str(excinfo.value)
    assert "secret" not in str(excinfo.value)


def test_plain_http_to_openrouter_is_allowed_to_this_machine_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_openrouter(
        monkeypatch,
        OPENROUTER_API_KEY=FAKE_OPENROUTER_KEY,
        OPENROUTER_BASE_URL="http://127.0.0.1:8123/api/v1",
    )

    assert load_settings(env_file=None).openrouter_base_url == "http://127.0.0.1:8123/api/v1"


@pytest.mark.parametrize(
    ("name", "value"),
    [("OPENROUTER_MODEL", "  "), ("OPENROUTER_TIMEOUT_SECONDS", "0")],
)
def test_a_blank_openrouter_model_or_a_non_positive_timeout_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _select_openrouter(monkeypatch, OPENROUTER_API_KEY=FAKE_OPENROUTER_KEY, **{name: value})

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert name in str(excinfo.value)


def test_no_openrouter_configuration_error_carries_the_key_even_in_its_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import traceback

    _select_openrouter(
        monkeypatch,
        OPENROUTER_API_KEY=FAKE_OPENROUTER_KEY,
        OPENROUTER_BASE_URL="http://example.invalid",
    )

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    printed = "".join(traceback.format_exception(excinfo.value))
    assert FAKE_OPENROUTER_KEY[:12] not in printed
    assert FAKE_OPENROUTER_KEY[-8:] not in printed
    assert excinfo.value.__cause__ is None


def test_demo_mode_needs_no_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")

    settings = load_settings(env_file=None)

    assert settings.llm_provider == "openrouter"
    assert settings.openrouter_api_key is None


@pytest.mark.parametrize("provider", ["ollama", "deepseek", "anthropic"])
def test_no_other_provider_needs_an_openrouter_key(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key-not-real-0123456789")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")

    settings = load_settings(env_file=None)

    assert settings.llm_provider == provider
    assert settings.openrouter_api_key is None


def test_each_provider_names_its_own_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DB_URL)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")

    for provider, expected in [
        ("ollama", "qwen2.5:7b-instruct"),
        ("deepseek", "deepseek-flash"),
        ("openrouter", "deepseek/deepseek-v4.1-flash"),
        ("anthropic", "claude-opus-5"),
    ]:
        monkeypatch.setenv("LLM_PROVIDER", provider)
        assert load_settings(env_file=None).provider_model == expected
