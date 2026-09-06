"""The LLM boundary: one protocol, two implementations.

This module and its siblings are the **only** place a provider SDK is imported
(docs/architecture.md section 2). Every service above it depends on the
`LlmClient` protocol and on `LlmRequest`/`LlmResponse`, never on Anthropic
types, so a provider change is contained here.

```
LlmClient (Protocol)
├── LiveLlmClient    — calls the provider; used when DEMO_MODE=false
└── ReplayLlmClient  — serves recorded fixtures; used when DEMO_MODE=true
```

Two rules from architecture section 4.2, both enforced below:

* **Demo mode never falls back to a live call.** A missing fixture raises.
  Silent fallback would let the "free, deterministic" demo quietly spend money.
* **Live mode never falls back to a fixture.** That would present a recording
  as a fresh result — fabricating an answer, which this project exists not to do.

The client returns the model's **raw text**. Parsing and validation happen in
the calling service, against `schemas/llm`, so that a malformed reply is
available verbatim for `LlmCallLog.raw_response_excerpt`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.enums import LlmPurpose, LlmSource
from app.core.errors import LlmUnavailableError
from app.core.hashing import sha256_text

logger = logging.getLogger(__name__)


class LlmProviderError(RuntimeError):
    """The provider could not be reached, or declined to answer.

    Deliberately distinct from a schema-validation failure: this means we have
    no reply to validate, so retrying against the same prompt is pointless.
    """


@dataclass(frozen=True)
class LlmRequest:
    """One model call, described without reference to any provider."""

    purpose: LlmPurpose
    prompt_version: str
    system_prompt: str

    #: The data channel. Everything untrusted goes here, never in the system
    #: prompt — docs/architecture.md section 5.
    user_content: str

    #: JSON Schema the reply must satisfy. Sent to the provider as an output
    #: constraint *and* re-checked locally by the caller.
    json_schema: dict[str, Any]

    max_tokens: int = 8000

    #: 1 or 2. The retry policy is exactly one retry on schema failure
    #: (architecture section 8), and the attempt participates in the fixture
    #: key so a retry can be replayed distinctly from the first try.
    attempt: int = 1

    #: Hash of the *stable* part of the input — the document being processed.
    #: Set explicitly so it stays identical across attempt 1 and attempt 2,
    #: whose rendered prompts differ by the validation feedback appended to
    #: the retry. This is the value written to `LlmCallLog.input_sha256`, and
    #: it is what architecture section 4 means by "cached by JD text hash".
    input_sha256: str = ""

    def with_computed_hash(self) -> LlmRequest:
        """Return a copy with `input_sha256` filled in from `user_content`."""
        if self.input_sha256:
            return self
        return LlmRequest(
            purpose=self.purpose,
            prompt_version=self.prompt_version,
            system_prompt=self.system_prompt,
            user_content=self.user_content,
            json_schema=self.json_schema,
            max_tokens=self.max_tokens,
            attempt=self.attempt,
            input_sha256=sha256_text(self.user_content),
        )


@dataclass(frozen=True)
class LlmResponse:
    """A raw reply plus the metadata `LlmCallLog` needs to make it auditable."""

    text: str
    model: str
    source: LlmSource
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None

    #: Present only when the provider declined to answer.
    refusal_category: str | None = None


@dataclass(frozen=True)
class FixtureKey:
    """What identifies a recorded reply.

    The four components named in architecture section 4.2 — purpose, model,
    prompt version, input hash — plus `attempt`, because a retry sends a
    different prompt (the same input with validation feedback appended) and
    therefore needs its own recording. Keying the retry on the feedback text
    itself would make fixtures unmaintainable, since that text is generated
    from live validator output.
    """

    purpose: LlmPurpose
    model: str
    prompt_version: str
    input_sha256: str
    attempt: int


class LlmClient(Protocol):
    """What every service above this layer is allowed to know about the model."""

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Run one call. Raises `LlmProviderError` if there is no reply at all."""
        ...


class LiveLlmClient:
    """Calls the Anthropic API. Used only when `DEMO_MODE=false`."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 120.0) -> None:
        self._model = model
        self._timeout = timeout_seconds
        # Imported lazily so that demo-mode deployments and the offline test
        # suite never need the SDK loaded, and so the import error (if the
        # dependency is missing) names the real cause.
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise LlmUnavailableError(
                "The anthropic package is not installed; live mode is unavailable."
            ) from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)

    def complete(self, request: LlmRequest) -> LlmResponse:
        started = time.monotonic()
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=request.system_prompt,
                messages=[{"role": "user", "content": request.user_content}],
                # Adaptive thinking: extraction is a judgement task, and the
                # current models take no sampling parameters, so determinism
                # comes from the fixture layer rather than from temperature.
                thinking={"type": "adaptive"},
                output_config={"format": {"type": "json_schema", "schema": request.json_schema}},
            )
        except self._anthropic.APIStatusError as exc:
            # Status, never the provider's message body: that text is not ours
            # to forward to a client and can echo request content back.
            raise LlmProviderError(f"provider returned HTTP {exc.status_code}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LlmProviderError("could not reach the provider") from exc

        latency_ms = int((time.monotonic() - started) * 1000)

        # A safety refusal is an HTTP 200 with no usable content. Check the
        # stop reason before reading content, and report it honestly rather
        # than silently retrying somewhere else.
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LlmProviderError(f"provider declined the request (category={category})")

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        usage = getattr(response, "usage", None)

        return LlmResponse(
            text=text,
            model=getattr(response, "model", self._model),
            source=LlmSource.LIVE,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
        )


class ReplayLlmClient:
    """Serves recorded replies. Used when `DEMO_MODE=true`.

    Deterministic by construction: the same input always yields the same bytes,
    which is what lets the demo and the whole test suite run with no API key
    and at no cost.
    """

    def __init__(self, model: str, fixtures: dict[FixtureKey, str] | None = None) -> None:
        self._model = model
        if fixtures is None:
            from app.llm.fixtures import load_fixtures

            fixtures = load_fixtures()
        self._fixtures = fixtures

    def complete(self, request: LlmRequest) -> LlmResponse:
        prepared = request.with_computed_hash()
        key = FixtureKey(
            purpose=prepared.purpose,
            model=self._model,
            prompt_version=prepared.prompt_version,
            input_sha256=prepared.input_sha256,
            attempt=prepared.attempt,
        )
        try:
            text = self._fixtures[key]
        except KeyError as exc:
            # Loud, specific failure. Never a live call: see the module
            # docstring. The hash is included so a developer recording a new
            # fixture knows exactly which key to record it under.
            raise LlmUnavailableError(
                "No recorded fixture for this input in demo mode.",
                details={
                    "purpose": prepared.purpose.value,
                    "model": self._model,
                    "prompt_version": prepared.prompt_version,
                    "input_sha256": prepared.input_sha256,
                    "attempt": prepared.attempt,
                },
            ) from exc

        return LlmResponse(
            text=text,
            model=self._model,
            source=LlmSource.FIXTURE,
            latency_ms=0,
            # A recording has no live token usage to report. Recording zeros
            # would misstate cost; None says "not applicable", which is true.
            input_tokens=None,
            output_tokens=None,
        )


@dataclass
class _ClientCache:
    client: LlmClient | None = None
    key: tuple[bool, str] | None = field(default=None)


_cache = _ClientCache()


def build_llm_client(settings: Any) -> LlmClient:
    """Pick the implementation from configuration, once per process.

    `DEMO_MODE` selects the implementation at composition time — one branch, at
    startup, in one place (architecture section 11). Settings already guarantee
    that live mode has an API key, so no key check is needed here.
    """
    cache_key = (settings.demo_mode, settings.llm_model)
    if _cache.client is not None and _cache.key == cache_key:
        return _cache.client

    if settings.demo_mode:
        client: LlmClient = ReplayLlmClient(model=settings.llm_model)
        logger.info("LLM client: replay (demo mode), model=%s", settings.llm_model)
    else:
        # Guaranteed non-None by Settings._live_mode_requires_api_key.
        client = LiveLlmClient(api_key=str(settings.anthropic_api_key), model=settings.llm_model)
        logger.info("LLM client: live, model=%s", settings.llm_model)

    _cache.client = client
    _cache.key = cache_key
    return client


def reset_llm_client_cache() -> None:
    """Drop the cached client. For tests that change configuration."""
    _cache.client = None
    _cache.key = None
