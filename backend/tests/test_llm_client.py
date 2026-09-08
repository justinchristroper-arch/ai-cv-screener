"""The LLM boundary: fixture replay, key derivation, and the no-fallback rules.

These tests run entirely offline. No API key, no network, no provider SDK call
— which is the property that lets the whole Phase 4 suite and the public demo
run for free (docs/architecture.md section 4.2).
"""

from __future__ import annotations

import json

import pytest

from app.core.enums import LlmPurpose, LlmSource
from app.core.errors import LlmUnavailableError
from app.core.hashing import sha256_text
from app.llm.client import FixtureKey, LlmRequest, ReplayLlmClient, build_llm_client
from app.llm.fixtures import fixture_jd_text, load_fixtures
from app.llm.prompts.jd_extraction import (
    PROMPT_VERSION,
    build_request,
    render_user_content,
)

MODEL = "claude-opus-5"


def test_every_bundled_fixture_loads() -> None:
    """Counted per purpose, so a new recording cannot be added unnoticed.

    A bare total would drift upward silently with every milestone; this says
    which stages have recordings and how many each has.
    """
    fixtures = load_fixtures()

    by_purpose: dict[LlmPurpose, int] = {}
    for key in fixtures:
        by_purpose[key.purpose] = by_purpose.get(key.purpose, 0) + 1

    assert by_purpose == {
        # Phase 4: happy path, a recoverable retry pair, an unrecoverable pair,
        # and a prompt-injection attempt.
        # Plus three sets of informal natural-language criteria — Indonesian,
        # mixed, and English — added when extraction stopped requiring a formal
        # job description.
        LlmPurpose.JD_EXTRACTION: 9,
        # Candidate intelligence: a full CV, an injected CV, and a retry pair.
        LlmPurpose.PROFILE_EXTRACTION: 4,
        # Candidate intelligence: the undecided pairs for each of those CVs.
        # One per (CV, criteria set) pair the demo can walk: two readable
        # sample CVs across four sets of criteria.
        LlmPurpose.SEMANTIC_MATCH: 8,
    }


def test_retry_fixtures_share_an_input_hash_and_differ_by_attempt() -> None:
    """A retry is the same input, tried again — the hash must not change.

    `input_sha256` identifies the *document*. If a retry produced a different
    hash, the audit log would show two calls about two different inputs, which
    would be a lie about what happened.
    """
    fixtures = load_fixtures()
    analyst = [
        key
        for key in fixtures
        if key.input_sha256
        == sha256_text(render_user_content(fixture_jd_text("jd_data_analyst_retry_attempt1")))
    ]

    assert {key.attempt for key in analyst} == {1, 2}
    assert len({key.input_sha256 for key in analyst}) == 1


def test_replay_returns_the_recorded_reply() -> None:
    jd_text = fixture_jd_text("jd_backend_engineer")
    client = ReplayLlmClient(model=MODEL)

    response = client.complete(build_request(jd_text))

    assert response.source is LlmSource.FIXTURE
    assert response.model == MODEL
    payload = json.loads(response.text)
    assert len(payload["requirements"]) == 13


def test_replay_is_deterministic() -> None:
    """Same input, same bytes. This is what makes the demo reproducible."""
    jd_text = fixture_jd_text("jd_backend_engineer")
    client = ReplayLlmClient(model=MODEL)

    first = client.complete(build_request(jd_text))
    second = client.complete(build_request(jd_text))

    assert first.text == second.text


def test_replay_reports_no_token_usage() -> None:
    """A recording has no live cost. Reporting zeros would misstate spend."""
    client = ReplayLlmClient(model=MODEL)

    response = client.complete(build_request(fixture_jd_text("jd_backend_engineer")))

    assert response.input_tokens is None
    assert response.output_tokens is None


def test_missing_fixture_raises_and_never_calls_the_provider() -> None:
    """Demo mode never silently falls back to a live call.

    The error also has to name the key a developer would need to record, or
    adding a fixture becomes guesswork.
    """
    client = ReplayLlmClient(model=MODEL)

    with pytest.raises(LlmUnavailableError) as excinfo:
        client.complete(build_request("A job description that was never recorded."))

    assert "fixture" in str(excinfo.value).lower()
    assert excinfo.value.details["attempt"] == 1
    assert excinfo.value.details["prompt_version"] == PROMPT_VERSION
    assert len(excinfo.value.details["input_sha256"]) == 64


def test_a_different_model_is_a_different_recording() -> None:
    """A reply recorded from one model is not a recording of another."""
    client = ReplayLlmClient(model="some-other-model")

    with pytest.raises(LlmUnavailableError):
        client.complete(build_request(fixture_jd_text("jd_backend_engineer")))


def test_changing_the_prompt_invalidates_the_fixture() -> None:
    """A fixture is a recording of a reply to specific instructions.

    Replaying it against a different prompt version would present output
    produced by other instructions as if it answered these ones.
    """
    client = ReplayLlmClient(model=MODEL)
    request = build_request(fixture_jd_text("jd_backend_engineer"))
    altered = LlmRequest(
        purpose=request.purpose,
        prompt_version="jd-extraction-v99",
        system_prompt=request.system_prompt,
        user_content=request.user_content,
        json_schema=request.json_schema,
        attempt=request.attempt,
        input_sha256=request.input_sha256,
    )

    with pytest.raises(LlmUnavailableError):
        client.complete(altered)


def test_demo_mode_selects_the_replay_client(settings_factory) -> None:
    client = build_llm_client(settings_factory(demo_mode=True))

    assert isinstance(client, ReplayLlmClient)


def test_live_mode_never_selects_the_replay_client(settings_factory, monkeypatch) -> None:
    """The other half of the rule: live mode must not serve a recording.

    The live client is constructed but never called, so no request is made.
    """
    from app.llm import client as client_module

    constructed: dict[str, str] = {}

    class _StubLive:
        def __init__(self, api_key: str, model: str, timeout_seconds: float = 120.0) -> None:
            constructed["api_key"] = api_key
            constructed["model"] = model

        def complete(self, request):  # pragma: no cover - never invoked here
            raise AssertionError("no live call should be made in this test")

    monkeypatch.setattr(client_module, "LiveLlmClient", _StubLive)

    built = build_llm_client(
        settings_factory(demo_mode=False, anthropic_api_key="sk-ant-not-a-real-key")
    )

    assert not isinstance(built, ReplayLlmClient)
    assert constructed["api_key"] == "sk-ant-not-a-real-key"
    assert constructed["model"] == MODEL


def test_fixture_key_is_hashable_and_value_based() -> None:
    """The key is a dict key; equality must be by value, not identity."""
    parts = {
        "purpose": LlmPurpose.JD_EXTRACTION,
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "input_sha256": "a" * 64,
        "attempt": 1,
    }

    assert FixtureKey(**parts) == FixtureKey(**parts)
    assert len({FixtureKey(**parts), FixtureKey(**parts)}) == 1
