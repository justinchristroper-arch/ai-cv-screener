"""`scripts/check_llm.py` with DeepSeek: the preflight the launcher relies on.

The launcher runs `check-llm --preflight` before starting CvScreener in a
hosted-provider mode, so what it reports decides whether the app starts. For
DeepSeek it asks `GET /models` — a request that spends no tokens — whether the
key is accepted and the configured model exists.

Offline, like every test here: the transport is replaced at
`urllib.request.urlopen`, the key is fake, and `.env` is never read (the
script's own settings loader is swapped for `settings_factory`).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from app.llm.client import DeepSeekLlmClient

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_llm.py"
FAKE_KEY = "test-deepseek-key-not-real-0123456789"


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
