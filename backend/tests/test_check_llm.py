"""`scripts/check_llm.py` for the hosted providers: the preflight the launcher
relies on.

The launcher runs `check-llm --preflight` before starting CvScreener in a
hosted-provider mode, so what it reports decides whether the app starts. For
DeepSeek it asks `GET /models` — a request that spends no tokens — whether the
key is accepted and the configured model exists. For OpenRouter it asks
`GET /key` first, then `GET /models` for the configured slug.

Offline, like every test here: the transport is replaced at
`urllib.request.urlopen`, the key is fake, and `.env` is never read (the
script's own settings loader is swapped for `settings_factory`).
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import typing
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from app.core.config import Settings
from app.llm.client import DeepSeekLlmClient, OpenRouterLlmClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_llm.py"
LAUNCHER = REPO_ROOT / "scripts" / "start-cvscreener.ps1"
FAKE_KEY = "test-deepseek-key-not-real-0123456789"
FAKE_OPENROUTER_KEY = "test-openrouter-key-not-real-0123456789"


@pytest.fixture(scope="module")
def check_llm():
    # Registered before it runs: the script defines a dataclass, and with
    # postponed annotations `dataclasses` looks its module up in sys.modules.
    spec = importlib.util.spec_from_file_location("check_llm_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture()
def deepseek_settings(settings_factory):
    return settings_factory(demo_mode=False, llm_provider="deepseek", deepseek_api_key=FAKE_KEY)


class _Models:
    """A stand-in for `urlopen` answering `GET /models`."""

    def __init__(self, ids=("deepseek-flash", "deepseek-v4-pro"), error=None) -> None:
        self.raw = json.dumps(
            {"object": "list", "data": [{"id": id_, "object": "model"} for id_ in ids]}
        ).encode()
        self.error = error
        self.url: str | None = None
        self.headers: dict[str, str] = {}

    def __call__(self, request, timeout=None):  # noqa: ANN001 - urlopen's shape
        self.url = request.full_url
        self.headers = {name.lower(): value for name, value in request.header_items()}
        if self.error is not None:
            raise self.error
        return _Response(self.raw)


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _install(monkeypatch: pytest.MonkeyPatch, transport: _Models) -> _Models:
    monkeypatch.setattr("urllib.request.urlopen", transport)
    return transport


def test_a_listed_model_and_an_accepted_key_pass(check_llm, deepseek_settings, monkeypatch) -> None:
    transport = _install(monkeypatch, _Models())

    assert check_llm._preflight(deepseek_settings) == []
    assert transport.url == "https://api.deepseek.com/models"
    assert transport.headers["authorization"] == f"Bearer {FAKE_KEY}"


def test_a_model_the_key_cannot_use_is_named_with_the_alternatives(
    check_llm, settings_factory, monkeypatch
) -> None:
    _install(monkeypatch, _Models())
    settings = settings_factory(
        demo_mode=False,
        llm_provider="deepseek",
        deepseek_api_key=FAKE_KEY,
        deepseek_model="deepseek-flsh",
    )

    problems = "\n".join(check_llm._preflight(settings))

    assert "'deepseek-flsh'" in problems
    assert "deepseek-flash, deepseek-v4-pro" in problems
    assert "DEEPSEEK_MODEL" in problems


def test_a_rejected_key_is_named_as_such(check_llm, deepseek_settings, monkeypatch) -> None:
    error = urllib.error.HTTPError(
        url="https://api.deepseek.com/models", code=401, msg="x", hdrs=None, fp=io.BytesIO(b"{}")
    )
    _install(monkeypatch, _Models(error=error))

    problems = "\n".join(check_llm._preflight(deepseek_settings))

    assert "rejected the API key (HTTP 401)" in problems
    assert "DEEPSEEK_API_KEY" in problems


def test_an_unreachable_api_names_the_url_to_check(
    check_llm, deepseek_settings, monkeypatch
) -> None:
    _install(monkeypatch, _Models(error=urllib.error.URLError(ConnectionRefusedError())))

    problems = "\n".join(check_llm._preflight(deepseek_settings))

    assert "Could not reach DeepSeek at https://api.deepseek.com" in problems
    assert "DEEPSEEK_BASE_URL" in problems


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError(
            url="u", code=401, msg="x", hdrs=None, fp=io.BytesIO(FAKE_KEY.encode())
        ),
        urllib.error.URLError(ConnectionRefusedError()),
        ValueError(f"Invalid header value b'Bearer {FAKE_KEY}\\r\\n'"),
        ConnectionResetError(f"reset while sending {FAKE_KEY}"),
    ],
    ids=["401", "unreachable", "invalid-header-quoting-the-key", "reset"],
)
def test_no_preflight_line_contains_the_key(
    check_llm, deepseek_settings, monkeypatch, error
) -> None:
    """These lines are printed to a console the launcher shows, and into its log."""
    _install(monkeypatch, _Models(error=error))

    problems = check_llm._preflight(deepseek_settings)

    assert problems
    assert FAKE_KEY not in "\n".join(problems)


def test_the_client_it_builds_is_the_deepseek_one(check_llm, deepseek_settings) -> None:
    """Constructed, not called: no request is made."""
    assert isinstance(check_llm._build_client(deepseek_settings), DeepSeekLlmClient)


def test_the_preflight_run_reports_the_hosted_model_and_endpoint(
    check_llm, deepseek_settings, monkeypatch, capsys
) -> None:
    """What the launcher's window shows, end to end — and never the key."""
    _install(monkeypatch, _Models())
    monkeypatch.setattr(check_llm, "_settings", lambda: deepseek_settings)

    exit_code = check_llm.main(["--preflight"])

    printed = capsys.readouterr().out
    assert exit_code == 0
    assert "Provider: deepseek   Model: deepseek-flash" in printed
    assert "Endpoint: https://api.deepseek.com" in printed
    assert "Preflight: OK" in printed
    assert FAKE_KEY not in printed


def test_a_failed_preflight_exits_non_zero_so_the_launcher_stops(
    check_llm, deepseek_settings, monkeypatch, capsys
) -> None:
    error = urllib.error.HTTPError(
        url="https://api.deepseek.com/models", code=401, msg="x", hdrs=None, fp=io.BytesIO(b"{}")
    )
    _install(monkeypatch, _Models(error=error))
    monkeypatch.setattr(check_llm, "_settings", lambda: deepseek_settings)

    exit_code = check_llm.main(["--preflight"])

    assert exit_code == 1
    assert "Not ready" in capsys.readouterr().out


# --------------------------------------------------------------------------
# OpenRouter: GET /key, then GET /models?q=<slug>
# --------------------------------------------------------------------------

OPENROUTER = "https://openrouter.ai/api/v1"
SLUG = "deepseek/deepseek-v4.1-flash"

#: What `/key` describes about the account. The label is free text -- here
#: deliberately shaped like a key prefix, the worst case -- and the amounts are
#: money: none of it may be printed.
KEY_BODY = {
    "data": {
        "label": "sk-or-v1-abc...xyz",
        "limit": 25.0,
        "limit_remaining": 17.5,
        "usage": 7.5,
        "is_free_tier": False,
    }
}

MODEL_ENTRY = {
    "id": SLUG,
    "name": "DeepSeek V4.1 Flash",
    "supported_parameters": [
        "max_tokens",
        "temperature",
        "response_format",
        "reasoning",
        "include_reasoning",
    ],
    "reasoning": {"supported_efforts": ["high", "none"], "mandatory": False},
}


class _OpenRouter:
    """A stand-in for `urlopen` answering OpenRouter's two preflight requests."""

    def __init__(self, key_body=None, models=None, key_error=None, models_error=None) -> None:
        self.key_body = KEY_BODY if key_body is None else key_body
        self.models = [MODEL_ENTRY] if models is None else models
        self.key_error = key_error
        self.models_error = models_error
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, request, timeout=None):  # noqa: ANN001 - urlopen's shape
        url = request.full_url
        self.calls.append((url, {name.lower(): value for name, value in request.header_items()}))
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/key"):
            if self.key_error is not None:
                raise self.key_error
            return _Response(json.dumps(self.key_body).encode())
        if path.endswith("/models"):
            if self.models_error is not None:
                raise self.models_error
            return _Response(json.dumps({"data": self.models}).encode())
        raise AssertionError(f"the OpenRouter preflight asked for an unexpected URL: {url}")


@pytest.fixture()
def openrouter_settings(settings_factory):
    return settings_factory(
        demo_mode=False, llm_provider="openrouter", openrouter_api_key=FAKE_OPENROUTER_KEY
    )


def _http_401(url: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url=url, code=401, msg="x", hdrs=None, fp=io.BytesIO(b"{}"))


def test_openrouter_checks_the_key_first_then_the_model(
    check_llm, openrouter_settings, monkeypatch
) -> None:
    transport = _install(monkeypatch, _OpenRouter())

    assert check_llm._preflight(openrouter_settings) == []

    urls = [url for url, _ in transport.calls]
    assert urls == [
        f"{OPENROUTER}/key",
        f"{OPENROUTER}/models?q=deepseek%2Fdeepseek-v4.1-flash",
    ]
    for _, headers in transport.calls:
        assert headers["authorization"] == f"Bearer {FAKE_OPENROUTER_KEY}"


def test_openrouter_never_falls_through_to_ollama(
    check_llm, openrouter_settings, monkeypatch
) -> None:
    """Every request goes to OpenRouter; none to Ollama's /api/tags."""
    transport = _install(monkeypatch, _OpenRouter())

    check_llm._preflight(openrouter_settings)

    assert transport.calls
    assert all(url.startswith(OPENROUTER) for url, _ in transport.calls)


def test_a_rejected_openrouter_key_stops_before_the_model_is_asked_about(
    check_llm, openrouter_settings, monkeypatch
) -> None:
    transport = _install(monkeypatch, _OpenRouter(key_error=_http_401(f"{OPENROUTER}/key")))

    problems = "\n".join(check_llm._preflight(openrouter_settings))

    assert "rejected the API key (HTTP 401)" in problems
    assert "OPENROUTER_API_KEY" in problems
    assert len(transport.calls) == 1


def test_a_key_whose_credit_limit_is_used_up_is_reported_without_amounts(
    check_llm, openrouter_settings, monkeypatch
) -> None:
    body = {"data": {"label": "sk-or-v1-abc...xyz", "limit": 5.0, "limit_remaining": 0}}
    _install(monkeypatch, _OpenRouter(key_body=body))

    problems = "\n".join(check_llm._preflight(openrouter_settings))

    assert "credit limit is used up" in problems
    assert "5.0" not in problems
    assert "sk-or" not in problems


def test_a_model_openrouter_does_not_list_is_named_with_the_closest_matches(
    check_llm, settings_factory, monkeypatch
) -> None:
    _install(monkeypatch, _OpenRouter(models=[MODEL_ENTRY, {"id": "deepseek/deepseek-v4-pro"}]))
    settings = settings_factory(
        demo_mode=False,
        llm_provider="openrouter",
        openrouter_api_key=FAKE_OPENROUTER_KEY,
        openrouter_model="deepseek/deepseek-v4.1-flsh",
    )

    problems = "\n".join(check_llm._preflight(settings))

    assert "'deepseek/deepseek-v4.1-flsh'" in problems
    assert "deepseek/deepseek-v4.1-flash, deepseek/deepseek-v4-pro" in problems
    assert "OPENROUTER_MODEL" in problems


def test_a_model_without_a_parameter_the_client_requires_is_reported(
    check_llm, openrouter_settings, monkeypatch
) -> None:
    entry = {**MODEL_ENTRY, "supported_parameters": ["max_tokens", "temperature"]}
    _install(monkeypatch, _OpenRouter(models=[entry]))

    problems = "\n".join(check_llm._preflight(openrouter_settings))

    assert "response_format, reasoning" in problems


def test_a_model_whose_reasoning_is_mandatory_is_reported(
    check_llm, openrouter_settings, monkeypatch
) -> None:
    """The client asks for reasoning off; such a model would reject every call."""
    entry = {**MODEL_ENTRY, "reasoning": {"supported_efforts": ["high"], "mandatory": True}}
    _install(monkeypatch, _OpenRouter(models=[entry]))

    problems = "\n".join(check_llm._preflight(openrouter_settings))

    assert "reasoning as mandatory" in problems


def test_metadata_openrouter_does_not_publish_is_not_held_against_the_model(
    check_llm, openrouter_settings, monkeypatch
) -> None:
    """Inspected where available: an entry with no parameter or reasoning data passes."""
    _install(monkeypatch, _OpenRouter(models=[{"id": SLUG}]))

    assert check_llm._preflight(openrouter_settings) == []


def test_an_unreachable_openrouter_names_the_url_to_check(
    check_llm, openrouter_settings, monkeypatch
) -> None:
    error = urllib.error.URLError(ConnectionRefusedError())
    _install(monkeypatch, _OpenRouter(key_error=error))

    problems = "\n".join(check_llm._preflight(openrouter_settings))

    assert f"Could not reach OpenRouter at {OPENROUTER}" in problems
    assert "OPENROUTER_BASE_URL" in problems


@pytest.mark.parametrize(
    "failure",
    [
        {"key_error": _http_401("u")},
        {"key_error": urllib.error.URLError(ConnectionRefusedError())},
        {"key_error": ValueError(f"Invalid header value b'Bearer {FAKE_OPENROUTER_KEY}\\r\\n'")},
        {"key_error": ConnectionResetError(f"reset while sending {FAKE_OPENROUTER_KEY}")},
        {"models_error": ConnectionResetError(f"reset while sending {FAKE_OPENROUTER_KEY}")},
        {"models": []},
    ],
    ids=["401", "unreachable", "invalid-header", "reset-on-key", "reset-on-models", "no-model"],
)
def test_no_openrouter_preflight_line_contains_the_key_or_the_account(
    check_llm, openrouter_settings, monkeypatch, failure
) -> None:
    _install(monkeypatch, _OpenRouter(**failure))

    printed = "\n".join(check_llm._preflight(openrouter_settings))

    assert printed
    assert FAKE_OPENROUTER_KEY not in printed
    assert "sk-or" not in printed
    assert "17.5" not in printed


def test_the_client_it_builds_is_the_openrouter_one(check_llm, openrouter_settings) -> None:
    """Constructed, not called: no request is made."""
    built = check_llm._build_client(openrouter_settings)

    assert isinstance(built, OpenRouterLlmClient)
    assert not isinstance(built, DeepSeekLlmClient)


def test_the_openrouter_preflight_run_shows_the_provider_and_nothing_secret(
    check_llm, openrouter_settings, monkeypatch, capsys
) -> None:
    """What the launcher's window shows, end to end."""
    _install(monkeypatch, _OpenRouter())
    monkeypatch.setattr(check_llm, "_settings", lambda: openrouter_settings)

    exit_code = check_llm.main(["--preflight"])

    printed = capsys.readouterr().out
    assert exit_code == 0
    assert f"Provider: openrouter   Model: {SLUG}" in printed
    assert f"Endpoint: {OPENROUTER}" in printed
    assert "Preflight: OK" in printed
    assert FAKE_OPENROUTER_KEY not in printed
    assert "sk-or" not in printed
    assert "17.5" not in printed


def test_a_failed_openrouter_preflight_exits_non_zero_so_the_launcher_stops(
    check_llm, openrouter_settings, monkeypatch, capsys
) -> None:
    _install(monkeypatch, _OpenRouter(key_error=_http_401(f"{OPENROUTER}/key")))
    monkeypatch.setattr(check_llm, "_settings", lambda: openrouter_settings)

    exit_code = check_llm.main(["--preflight"])

    assert exit_code == 1
    assert "Not ready" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The launcher's list of hosted providers
# --------------------------------------------------------------------------


def _launcher_cloud_providers() -> set[str]:
    source = LAUNCHER.read_text(encoding="utf-8")
    match = re.search(r"\$settings\.Provider -in @\(([^)]*)\)", source)
    assert match, "the launcher's hosted-provider list was not found"
    return set(re.findall(r'"([a-z]+)"', match.group(1)))


def test_the_launcher_preflights_openrouter_as_a_hosted_provider() -> None:
    assert "openrouter" in _launcher_cloud_providers()


def test_the_launcher_knows_every_hosted_provider_the_settings_accept() -> None:
    """Anything but Ollama is hosted; the launcher fails loudly on an unknown one,
    so a provider added to the settings but not to the launcher could not start."""
    providers = set(typing.get_args(Settings.model_fields["llm_provider"].annotation))

    assert _launcher_cloud_providers() == providers - {"ollama"}
