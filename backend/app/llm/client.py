"""The LLM boundary: one protocol, three implementations.

This module and its siblings are the **only** place a provider is spoken to
(docs/architecture.md section 2). Every service above it depends on the
`LlmClient` protocol and on `LlmRequest`/`LlmResponse`, never on a provider's
types, so changing provider is contained here — which is what this file is for,
and what made swapping a cloud API for a local model a one-file change.

```
LlmClient (Protocol)
├── OllamaLlmClient     — a model running on this machine; the default
├── AnthropicLlmClient  — the cloud API; opt-in via LLM_PROVIDER=anthropic
└── ReplayLlmClient     — recorded fixtures; used when DEMO_MODE=true
```

Two rules from architecture section 4.2, both enforced below:

* **Demo mode never falls back to a real call.** A missing fixture raises.
  Silent fallback would let the "free, deterministic" demo quietly start
  spending money or CPU.
* **A real call never falls back to a fixture.** That would present a recording
  as a fresh result — fabricating an answer, which this project exists not to do.

The client returns the model's **raw text**. Parsing and validation happen in
the calling service, against `schemas/llm`, so that a malformed reply is
available verbatim for `LlmCallLog.raw_response_excerpt`. A local model is
measurably worse at holding a schema than a large hosted one, which makes that
validation boundary more load-bearing here, not less — so nothing about it was
relaxed to accommodate one.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
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


class OllamaLlmClient:
    """Calls a model running on this machine, through Ollama's HTTP API.

    The default provider. A fresh clone of this repository can screen a CV with
    no account, no API key and no per-call cost, which is the point: the pipeline
    needs a model that can read text, not a particular company's model.

    ## Why `urllib` and not an HTTP library

    One POST with a JSON body and a timeout. `urllib.request` does that, and this
    project has no other runtime HTTP client — adding one so the LLM boundary
    could make a single request would be a dependency bought for nothing.

    ## Structured output

    Ollama accepts a JSON Schema in `format`, and constrains generation to it.
    The schema passed is the *same* `request.json_schema` the Anthropic client
    sends and the same one `schemas/llm` re-validates the reply against
    afterwards — a provider-side constraint is a convenience, never the check
    (architecture section 4.3).

    `temperature` is 0 and the context window is set explicitly. Neither is
    tuning for quality: a long CV silently truncated by a 2048-token default
    window is a wrong answer that looks like a right one, and a temperature
    above 0 makes two runs of the same document disagree for no reason a
    recruiter could act on.
    """

    #: Ollama's default context window is small enough to silently drop the tail
    #: of a real CV. This is sized for a long document plus its prompt; a model
    #: whose own window is smaller uses its own, and one whose window is larger
    #: is not forced to allocate more than this.
    DEFAULT_CONTEXT_TOKENS = 8192

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 300.0,
        context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    ) -> None:
        self._endpoint = base_url.rstrip("/") + "/api/chat"
        self._model = model
        self._timeout = timeout_seconds
        self._context_tokens = context_tokens

    def complete(self, request: LlmRequest) -> LlmResponse:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                # The data channel, exactly as every other provider receives it.
                # Untrusted document text stays in the user turn and is never
                # concatenated into the system prompt (architecture section 5).
                {"role": "user", "content": request.user_content},
            ],
            "stream": False,
            "format": request.json_schema,
            "options": {
                "temperature": 0,
                "num_ctx": self._context_tokens,
                "num_predict": request.max_tokens,
            },
        }

        started = time.monotonic()
        body = self._post(json.dumps(payload).encode("utf-8"))
        latency_ms = int((time.monotonic() - started) * 1000)

        message = body.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            # A 200 with the wrong shape is a provider problem, not a schema
            # problem: there is no model output to validate or to record.
            raise LlmProviderError(
                "the local model server returned a reply with no message content"
            )

        return LlmResponse(
            text=message["content"],
            # What actually answered, which is not necessarily what was asked
            # for: Ollama resolves a tag to a specific build.
            model=str(body.get("model") or self._model),
            source=LlmSource.LIVE,
            latency_ms=latency_ms,
            input_tokens=_as_optional_int(body.get("prompt_eval_count")),
            output_tokens=_as_optional_int(body.get("eval_count")),
        )

    def _post(self, data: bytes) -> dict[str, Any]:
        """One request, with every failure turned into something actionable.

        A developer running this repository for the first time will hit at least
        two of these, so each says what to do rather than what went wrong.
        """
        http_request = urllib.request.Request(
            self._endpoint,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except TimeoutError as exc:
            raise LlmProviderError(
                f"the local model did not answer within {self._timeout:.0f}s. A larger "
                "model on a slower machine can exceed this; raise "
                "OLLAMA_TIMEOUT_SECONDS or choose a smaller model."
            ) from exc
        except urllib.error.URLError as exc:
            # The single most common first-run failure: Ollama is not running.
            raise LlmProviderError(
                f"could not reach the local model server at {self._safe_endpoint()}. "
                "Is Ollama running? Start it with `ollama serve`, and check "
                "OLLAMA_BASE_URL."
            ) from exc

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LlmProviderError(
                "the local model server returned a body that is not JSON"
            ) from exc

        if not isinstance(body, dict):
            raise LlmProviderError("the local model server returned JSON that is not an object")
        return body

    def _http_error(self, exc: urllib.error.HTTPError) -> LlmProviderError:
        """Map a status onto advice, using the body only to recognise one case.

        Ollama answers a request for an absent model with a 404 and a short
        `error` string. That one is worth reading, because "pull the model" is
        the whole fix and a bare 404 sends someone looking in the wrong place.
        The body is otherwise never forwarded: it is not ours to relay, and it
        can echo request content — which here would be CV text — back to a
        caller.
        """
        detail = ""
        try:
            detail = json.loads(exc.read()).get("error", "")
        except Exception:  # noqa: BLE001 - a body we cannot read is simply absent
            detail = ""

        if exc.code == 404 and "not found" in detail.lower():
            return LlmProviderError(
                f"the local model {self._model!r} is not installed. Pull it once with "
                f"`ollama pull {self._model}`, then try again."
            )
        return LlmProviderError(f"the local model server returned HTTP {exc.code}")

    def _safe_endpoint(self) -> str:
        """The endpoint, for an error message. No credentials by construction."""
        return self._endpoint


def _as_optional_int(value: Any) -> int | None:
    """Ollama omits counts on some paths; absent is not zero."""
    return value if isinstance(value, int) else None


class AnthropicLlmClient:
    """Calls the Anthropic API. Used only when `LLM_PROVIDER=anthropic`.

    Kept rather than deleted when the project moved to a local default, because
    it is what makes `LlmClient` an abstraction rather than a rename: two
    genuinely different providers — a hosted API with an SDK, and an HTTP call to
    a process on localhost — reach the pipeline through the same six-line
    protocol, and nothing above this file can tell which answered.

    It has never been exercised against a real key in this repository, and that
    is stated wherever a claim about it might otherwise be read (ADR-0011).
    """

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
    key: tuple[Any, ...] | None = field(default=None)


_cache = _ClientCache()


def build_llm_client(settings: Any) -> LlmClient:
    """Pick the implementation from configuration, once per process.

    The **only** place a provider is chosen — one branch, at composition time,
    in one file (architecture section 11). Everything above depends on the
    protocol, so nothing else in the application knows or can ask which provider
    is in use.

    Two levels, in this order:

    1. `DEMO_MODE` decides whether a model is contacted **at all**. It is checked
       first and short-circuits, so demo mode needs no provider configured, no
       server running and no key — that is what makes a fresh clone work.
    2. `LLM_PROVIDER` decides *which* model, and is consulted only when demo
       mode is off.

    Settings have already validated whatever the selected provider needs, so
    there is no configuration check here.
    """
    cache_key = (
        settings.demo_mode,
        settings.llm_model,
        settings.llm_provider,
        settings.ollama_base_url,
        settings.ollama_model,
    )
    if _cache.client is not None and _cache.key == cache_key:
        return _cache.client

    if settings.demo_mode:
        client: LlmClient = ReplayLlmClient(model=settings.llm_model)
        logger.info("LLM client: replay (demo mode), model=%s", settings.llm_model)
    elif settings.llm_provider == "ollama":
        client = OllamaLlmClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
        # The URL is logged: it is operator configuration, it carries no
        # credentials (settings refuse a URL that does), and "which machine is
        # answering" is the first thing anyone debugging this needs.
        logger.info(
            "LLM client: local (ollama), model=%s, base_url=%s",
            settings.ollama_model,
            settings.ollama_base_url,
        )
    else:
        # Guaranteed non-None by Settings._selected_provider_is_configured.
        client = AnthropicLlmClient(
            api_key=str(settings.anthropic_api_key), model=settings.llm_model
        )
        logger.info("LLM client: cloud (anthropic), model=%s", settings.llm_model)

    _cache.client = client
    _cache.key = cache_key
    return client


def reset_llm_client_cache() -> None:
    """Drop the cached client. For tests that change configuration."""
    _cache.client = None
    _cache.key = None
