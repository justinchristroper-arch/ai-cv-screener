"""The LLM boundary: one protocol, five implementations.

This module and its siblings are the **only** place a provider is spoken to
(docs/architecture.md section 2). Every service above it depends on the
`LlmClient` protocol and on `LlmRequest`/`LlmResponse`, never on a provider's
types, so changing provider is contained here — which is what this file is for,
and what made swapping a cloud API for a local model a one-file change.

```
LlmClient (Protocol)
├── OllamaLlmClient     — a model running on this machine; the default
├── DeepSeekLlmClient   — the hosted API for a deployment; LLM_PROVIDER=deepseek
├── OpenRouterLlmClient — a gateway to hosted models; LLM_PROVIDER=openrouter
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

import http.client
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


class DeepSeekLlmClient:
    """Calls the DeepSeek API. Used only when `LLM_PROVIDER=deepseek`.

    The hosted provider, for a deployment where no machine can run a local
    model. It speaks DeepSeek's Chat Completions endpoint with `urllib`, for the
    reason the Ollama client does: one POST with a JSON body does not justify a
    dependency, and a vendor SDK would be a second one inside this package.

    ## Structured output: JSON mode, with the schema in the prompt

    DeepSeek's JSON output (`response_format={"type": "json_object"}`)
    guarantees a JSON object but, unlike Ollama's `format` or Anthropic's
    `output_config`, accepts no schema to constrain generation with. So the same
    `request.json_schema` is written into the system prompt instead, which is
    what DeepSeek asks for: the word "json" and the shape of the answer in the
    prompt. The calling service re-validates the reply against `schemas/llm`
    either way, and that was always the real check (architecture section 4.3).
    What changes is that an invalid reply becomes likelier, and the single
    retry with validation feedback is what absorbs it.

    The schema is trusted text from this codebase, so it may join the system
    prompt. The untrusted document still travels only in the user turn.

    ## Thinking off, temperature 0

    `deepseek-flash` thinks by default, and thinking mode ignores `temperature`.
    Extraction wants a reply that reads the same document the same way twice,
    so thinking is turned off explicitly and the temperature pinned to 0 — the
    reason the Ollama client pins it.

    ## One deadline for the whole call

    While a request waits to be scheduled, DeepSeek keeps the connection alive
    by sending empty lines, and gives up only after ten minutes. A socket
    timeout never fires while bytes keep arriving, so the reply is read in
    pieces against a single deadline. The empty lines are whitespace in front of
    the JSON body, and parse away.

    ## The key

    Sent in the `Authorization` header and nowhere else: not in the URL, not in
    the body, and never in a log line or an error message. No exception from
    the transport is chained onto the errors raised here, because Python's HTTP
    client quotes an invalid header value in full — and that value is the key.
    """

    #: Far above any reply this application asks for (at most 16,000 tokens);
    #: a body larger than this is not a model's answer.
    MAX_REPLY_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._endpoint = self._base_url + "/chat/completions"
        self._model = model
        self._timeout = timeout_seconds

    def complete(self, request: LlmRequest) -> LlmResponse:
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": _with_json_schema(request.system_prompt, request.json_schema),
                },
                # The data channel, exactly as every other provider receives it.
                {"role": "user", "content": request.user_content},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": request.max_tokens,
            "stream": False,
        }

        started = time.monotonic()
        body = self._post(json.dumps(payload).encode("utf-8"))
        latency_ms = int((time.monotonic() - started) * 1000)

        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(choice, dict) or not isinstance(message, dict):
            # A 200 with the wrong shape is a provider problem, not a schema
            # problem: there is no model output to validate or to record.
            raise LlmProviderError("DeepSeek returned a reply with no message")

        finish_reason = choice.get("finish_reason")
        if finish_reason == "content_filter":
            raise LlmProviderError(
                "DeepSeek declined to answer: its content filter stopped the reply"
            )
        if finish_reason in ("insufficient_system_resource", "aborted"):
            raise LlmProviderError(
                f"DeepSeek stopped before finishing the reply ({finish_reason}). Try again shortly."
            )

        content = message.get("content")
        if content is None:
            # DeepSeek documents that JSON output "may occasionally return empty
            # content". That is an answer the model failed to write, not a
            # transport fault: as empty text it fails validation in the calling
            # service and gets the one permitted retry.
            content = ""
        if not isinstance(content, str):
            raise LlmProviderError("DeepSeek returned message content that is not text")

        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return LlmResponse(
            text=content,
            # What actually answered, which DeepSeek reports itself.
            model=str(body.get("model") or self._model),
            source=LlmSource.LIVE,
            latency_ms=latency_ms,
            input_tokens=_as_optional_int(usage.get("prompt_tokens")),
            output_tokens=_as_optional_int(usage.get("completion_tokens")),
        )

    def _post(self, data: bytes) -> dict[str, Any]:
        """One request, every failure turned into advice that holds no secret."""
        http_request = urllib.request.Request(
            self._endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        deadline = time.monotonic() + self._timeout

        # `from None` throughout: see "The key" in the class docstring.
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                raw = self._read_until(deadline, response)
        except urllib.error.HTTPError as exc:
            # The status, never the body: it is not ours to relay, and it can
            # echo request content — here, CV text — back to a caller.
            raise _deepseek_http_error(exc.code) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise self._timeout_error() from None
            raise LlmProviderError(
                f"could not reach DeepSeek at {self._base_url}{_reason_name(exc.reason)}. "
                "Check the network connection and DEEPSEEK_BASE_URL."
            ) from None
        except TimeoutError:
            raise self._timeout_error() from None
        except (OSError, ValueError, http.client.HTTPException):
            raise LlmProviderError(
                "the connection to DeepSeek failed before a complete reply arrived"
            ) from None

        try:
            body = json.loads(raw)
        except ValueError:
            raise LlmProviderError("DeepSeek returned a body that is not JSON") from None
        if not isinstance(body, dict):
            raise LlmProviderError("DeepSeek returned JSON that is not an object")
        return body

    def _read_until(self, deadline: float, response: Any) -> bytes:
        """The whole body, or a timeout once `deadline` has passed.

        `read1` returns as soon as anything has arrived, so the deadline is
        checked between the keep-alive lines as well as at the end.
        """
        chunks: list[bytes] = []
        size = 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError
            chunk = response.read1(65536)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > self.MAX_REPLY_BYTES:
                raise LlmProviderError("DeepSeek returned a body far larger than any reply")
            chunks.append(chunk)

    def _timeout_error(self) -> LlmProviderError:
        return LlmProviderError(
            f"DeepSeek did not answer within {self._timeout:.0f}s. Under load it queues "
            "requests; try again, or raise DEEPSEEK_TIMEOUT_SECONDS."
        )


#: What each status DeepSeek documents means to whoever has to act on it
#: (api-docs.deepseek.com, "Error Codes"). The response body is never read.
_DEEPSEEK_HTTP_ADVICE = {
    400: (
        "DeepSeek refused the request as invalid (HTTP 400). A wrong DEEPSEEK_MODEL is "
        "the likeliest cause; `python scripts/check_llm.py --preflight` lists the "
        "models this key can use."
    ),
    401: "DeepSeek rejected the API key (HTTP 401). Check DEEPSEEK_API_KEY.",
    402: (
        "The DeepSeek account has run out of balance (HTTP 402). Top it up on the "
        "DeepSeek platform, then try again."
    ),
    422: (
        "DeepSeek refused a request parameter (HTTP 422). A wrong DEEPSEEK_MODEL is the "
        "likeliest cause; `python scripts/check_llm.py --preflight` lists the models "
        "this key can use."
    ),
    429: (
        "DeepSeek is limiting this account (HTTP 429: too many requests at once). "
        "Try again shortly."
    ),
    500: "DeepSeek had a server error (HTTP 500). Try again shortly.",
    503: "DeepSeek is overloaded (HTTP 503). Try again shortly.",
}


def _deepseek_http_error(code: int) -> LlmProviderError:
    return LlmProviderError(_DEEPSEEK_HTTP_ADVICE.get(code, f"DeepSeek returned HTTP {code}"))


def _reason_name(reason: object) -> str:
    """` (ConnectionRefusedError)`, say: the kind of failure, never its text."""
    return f" ({type(reason).__name__})" if isinstance(reason, BaseException) else ""


def _with_json_schema(system_prompt: str, schema: dict[str, Any]) -> str:
    """The system prompt, plus the reply's shape, for a provider that cannot take it apart.

    Fixed wording and sorted keys, so one prompt version always sends the same
    bytes and what was sent can be rebuilt from the prompt version alone.
    """
    rendered = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        f"{system_prompt}\n\n"
        "Reply with one JSON object and nothing else: no prose, no code fence. "
        f"It must be valid against this JSON Schema:\n{rendered}"
    )


class OpenRouterLlmClient:
    """Calls a model through OpenRouter. Used only when `LLM_PROVIDER=openrouter`.

    OpenRouter is a gateway: one OpenAI-compatible Chat Completions endpoint in
    front of many upstream providers, each hosting the models they host. The
    model is named by an OpenRouter slug in `OPENROUTER_MODEL`
    (`deepseek/deepseek-v4.1-flash` by default), and OpenRouter picks which
    upstream provider serves each call.

    It is a separate client from `DeepSeekLlmClient` even when the model is a
    DeepSeek one, because the two differ exactly where it matters: the key is a
    different key for a different host, thinking is switched off through a
    different parameter, errors arrive in a different shape, and routing to a
    third party needs settings DeepSeek's own API has no notion of.

    ## Structured output: JSON mode, with the schema in the prompt

    The same approach as `DeepSeekLlmClient`, for the same reason. OpenRouter
    documents `response_format: {"type": "json_object"}` as JSON mode; support
    for a `json_schema` constraint is decided per upstream endpoint, so it is
    not relied on. The schema goes into the system prompt with
    `_with_json_schema`, and the calling service re-validates the reply against
    `schemas/llm` exactly as for every provider (architecture section 4.3).

    ## Thinking off, temperature 0

    DeepSeek's API documents thinking as the default for this model family, and
    `DeepSeekLlmClient` switches it off with DeepSeek's own `thinking` field. That
    field is not an OpenRouter parameter, so it is not sent here. OpenRouter's
    equivalent is `reasoning`, and its documentation describes
    `{"effort": "none"}` as disabling reasoning entirely -- unless a model marks
    reasoning as mandatory, which `scripts/check_llm.py --preflight` reports.
    With reasoning off, `temperature: 0` applies, as it does for DeepSeek.

    ## Routing

    `provider.require_parameters` restricts routing to upstream endpoints that
    support every parameter in the request, so JSON mode and the reasoning
    switch are not silently dropped by one that does not. `data_collection:
    "deny"` excludes providers that may store the data. Neither decides which
    provider serves a call; both narrow the set it is chosen from. No
    attribution headers are sent: they exist to list an app publicly.

    ## Errors arrive two ways

    Either as an HTTP status, or -- when an upstream provider fails after
    OpenRouter has already answered 200 -- as a 200 whose body is an `error`
    object with no `choices`. Both are mapped by their numeric code alone. The
    error's message and metadata are never read into anything this client
    raises: a moderation refusal carries a fragment of the flagged input, which
    here is CV text.

    ## The key

    Sent in the `Authorization` header and nowhere else, with the same
    `from None` discipline as `DeepSeekLlmClient`, and for the same reason.
    """

    #: Far above any reply this application asks for; a body larger than this
    #: is not a model's answer.
    MAX_REPLY_BYTES = 8 * 1024 * 1024

    #: See "Thinking off" above.
    REASONING: dict[str, Any] = {"effort": "none"}

    #: See "Routing" above.
    PROVIDER_ROUTING: dict[str, Any] = {"require_parameters": True, "data_collection": "deny"}

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._endpoint = self._base_url + "/chat/completions"
        self._model = model
        self._timeout = timeout_seconds

    def complete(self, request: LlmRequest) -> LlmResponse:
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": _with_json_schema(request.system_prompt, request.json_schema),
                },
                # The data channel, exactly as every other provider receives it.
                {"role": "user", "content": request.user_content},
            ],
            "response_format": {"type": "json_object"},
            "reasoning": dict(self.REASONING),
            "temperature": 0,
            "max_tokens": request.max_tokens,
            "stream": False,
            "provider": dict(self.PROVIDER_ROUTING),
        }

        started = time.monotonic()
        body = self._post(json.dumps(payload).encode("utf-8"))
        latency_ms = int((time.monotonic() - started) * 1000)

        error = body.get("error")
        if error:
            # A 200 carrying an error instead of a reply: see "Errors arrive two
            # ways" in the class docstring. Only a non-empty value counts, so a
            # reply that merely carries `"error": null` is read as a reply.
            raise _openrouter_body_error(error)

        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(choice, dict) or not isinstance(message, dict):
            # A 200 with the wrong shape is a provider problem, not a schema
            # problem: there is no model output to validate or to record.
            raise LlmProviderError("OpenRouter returned a reply with no message")

        finish_reason = choice.get("finish_reason")
        if finish_reason == "content_filter":
            raise LlmProviderError(
                "OpenRouter's upstream provider declined to answer: a content filter "
                "stopped the reply"
            )
        if finish_reason == "error":
            raise LlmProviderError(
                "OpenRouter's upstream provider failed while writing the reply. Try again shortly."
            )

        content = message.get("content")
        if content is None:
            # No text is an answer the model failed to write, not a transport
            # fault: as empty text it fails validation in the calling service
            # and gets the one permitted retry.
            content = ""
        if not isinstance(content, str):
            raise LlmProviderError("OpenRouter returned message content that is not text")

        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return LlmResponse(
            text=content,
            # The slug OpenRouter reports as having answered.
            model=str(body.get("model") or self._model),
            source=LlmSource.LIVE,
            latency_ms=latency_ms,
            input_tokens=_as_optional_int(usage.get("prompt_tokens")),
            output_tokens=_as_optional_int(usage.get("completion_tokens")),
        )

    def _post(self, data: bytes) -> dict[str, Any]:
        """One request, every failure turned into advice that holds no secret."""
        http_request = urllib.request.Request(
            self._endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        deadline = time.monotonic() + self._timeout

        # `from None` throughout: see "The key" in the class docstring.
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                raw = self._read_within_deadline(deadline, response)
        except urllib.error.HTTPError as exc:
            # The status, never the body: it can quote the request back.
            raise _openrouter_http_error(exc.code) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise self._timeout_error() from None
            raise LlmProviderError(
                f"could not reach OpenRouter at {self._base_url}{_reason_name(exc.reason)}. "
                "Check the network connection and OPENROUTER_BASE_URL."
            ) from None
        except TimeoutError:
            raise self._timeout_error() from None
        except (OSError, ValueError, http.client.HTTPException):
            raise LlmProviderError(
                "the connection to OpenRouter failed before a complete reply arrived"
            ) from None

        try:
            body = json.loads(raw)
        except ValueError:
            raise LlmProviderError("OpenRouter returned a body that is not JSON") from None
        if not isinstance(body, dict):
            raise LlmProviderError("OpenRouter returned JSON that is not an object")
        return body

    def _read_within_deadline(self, deadline: float, response: Any) -> bytes:
        """The whole body, or a timeout once `deadline` has passed.

        One deadline over the whole call, read in bounded chunks: a socket
        timeout only bounds the gap between two reads, and an unbounded read
        has no ceiling on size.
        """
        chunks: list[bytes] = []
        size = 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError
            chunk = response.read1(65536)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > self.MAX_REPLY_BYTES:
                raise LlmProviderError("OpenRouter returned a body far larger than any reply")
            chunks.append(chunk)

    def _timeout_error(self) -> LlmProviderError:
        return LlmProviderError(
            f"OpenRouter did not answer within {self._timeout:.0f}s. Try again, or raise "
            "OPENROUTER_TIMEOUT_SECONDS."
        )


#: What each code OpenRouter documents means to whoever has to act on it
#: (openrouter.ai/docs, "Errors and Debugging"). Used for an HTTP status and for
#: the code inside a 200's `error` body alike; the error's own message is never
#: read.
_OPENROUTER_ERROR_ADVICE = {
    400: (
        "the request was refused as invalid. A wrong OPENROUTER_MODEL is the likeliest "
        "cause; `python scripts/check_llm.py --preflight` checks it."
    ),
    401: "the API key was rejected. Check OPENROUTER_API_KEY.",
    402: (
        "the account or this key has run out of credit. Add credit, or raise the key's "
        "credit limit, on OpenRouter."
    ),
    403: (
        "the request was refused by a moderation flag, a guardrail on the account, or a "
        "permission. The refusal itself is not shown, because it can quote the request."
    ),
    408: "the request timed out at OpenRouter. Try again shortly.",
    429: "the account is being rate limited. Try again shortly.",
    502: "the model is down or returned an invalid response. Try again shortly.",
    503: (
        "no upstream provider meets this request's routing requirements (every "
        "parameter supported, data_collection=deny). Try again later, or choose "
        "another OPENROUTER_MODEL."
    ),
}


def _openrouter_http_error(code: int) -> LlmProviderError:
    advice = _OPENROUTER_ERROR_ADVICE.get(code)
    message = f"OpenRouter returned HTTP {code}"
    return LlmProviderError(f"{message}: {advice}" if advice else message)


def _openrouter_body_error(error: object) -> LlmProviderError:
    """A 200 whose body is an error. The code is read; nothing else is."""
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, bool) or not isinstance(code, int):
        return LlmProviderError("OpenRouter reported an error in place of a reply")
    advice = _OPENROUTER_ERROR_ADVICE.get(code)
    message = f"OpenRouter reported error {code} in place of a reply"
    return LlmProviderError(f"{message}: {advice}" if advice else message)


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
        settings.deepseek_base_url,
        settings.deepseek_model,
        settings.openrouter_base_url,
        settings.openrouter_model,
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
    elif settings.llm_provider == "deepseek":
        # The key is guaranteed present by Settings._selected_provider_is_configured,
        # and leaves its SecretStr only here, on its way into the client.
        client = DeepSeekLlmClient(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout_seconds=settings.deepseek_timeout_seconds,
        )
        # The URL is logged for the reason the Ollama one is; settings refuse a
        # URL that carries credentials. The key is not.
        logger.info(
            "LLM client: cloud (deepseek), model=%s, base_url=%s",
            settings.deepseek_model,
            settings.deepseek_base_url,
        )
    elif settings.llm_provider == "openrouter":
        # The key is guaranteed present by Settings._selected_provider_is_configured,
        # and leaves its SecretStr only here, on its way into the client.
        client = OpenRouterLlmClient(
            api_key=settings.openrouter_api_key.get_secret_value(),
            base_url=settings.openrouter_base_url,
            model=settings.openrouter_model,
            timeout_seconds=settings.openrouter_timeout_seconds,
        )
        # The model and URL are logged, as for DeepSeek. The key is not.
        logger.info(
            "LLM client: cloud (openrouter), model=%s, base_url=%s",
            settings.openrouter_model,
            settings.openrouter_base_url,
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
