# ADR-0011: A local model is the default provider

**Status:** Accepted

## Context

Every AI call in this project went to the Anthropic API. That worked, and it was
never the problem the design set out to solve — [ADR-0001](0001-ai-deterministic-boundary.md)
says the model's job is to read text and nothing else, which is a job any capable
model can do. But it made the project's central claim awkward in practice:

- **A fresh clone could not run the real thing.** Demo mode worked with no key,
  which is most of the value, but anyone wanting to screen their own CV against
  their own criteria needed an account, a payment method and a key. For a
  portfolio project whose readers are students and interviewers, that is a wall
  in front of the feature it most wants to show.
- **Candidate CVs left the machine.** A CV is personal data belonging to someone
  who did not choose this tool. Sending it to a third party is a real decision,
  and the product was making it by default with nothing but a line of copy to
  say so.
- **The abstraction was untested as an abstraction.** `LlmClient` had two
  implementations, but one of them served recordings. Nothing proved a second
  *provider* could be dropped in without the pipeline noticing.

## Decision

**Ollama is the default provider.** `LLM_PROVIDER=ollama` with
`OLLAMA_MODEL=qwen2.5:7b-instruct`, talking to a model on the machine running
the backend. No account, no key, no per-call cost.

The shape is unchanged. `OllamaLlmClient` is a third implementation of the same
`LlmClient` protocol, and `build_llm_client` is still the only place a provider
is chosen. Nothing above `app/llm/` knows or can ask which one answered.

```
LlmClient (Protocol)
├── OllamaLlmClient     — a model on this machine; the default
├── AnthropicLlmClient  — the cloud API; opt-in
└── ReplayLlmClient     — recordings; used when DEMO_MODE=true
```

Two levels of selection, in this order:

1. **`DEMO_MODE`** decides whether a model is contacted *at all*. It short-circuits,
   so demo mode needs no provider configured, no server running and no key.
2. **`LLM_PROVIDER`** decides *which* model, and is only consulted when demo mode
   is off.

**The Anthropic client was kept.** It is what makes `LlmClient` an abstraction
rather than a rename: a hosted API behind an SDK and an HTTP POST to a process on
localhost are genuinely different things, and both reach the pipeline through the
same six-line protocol. The cost of keeping it is one lazily-imported class and
one optional dependency; the alternative is claiming an abstraction with a single
implementation. Its API key is now required **only** when it is the selected
provider — running locally must not demand an account for a service nobody chose.

**`urllib.request`, not an HTTP library.** One POST with a JSON body and a
timeout. Adding `httpx` so the LLM boundary could make a single request would be
a dependency bought for nothing, and this project has removed dependencies for
less.

### The model

`qwen2.5:7b-instruct`, roughly 4.7 GB quantised (Q4_K_M), needing about 8 GB of
free RAM — or considerably less time on a GPU with 6 GB or more of VRAM.

| Why it, over the alternatives | |
|---|---|
| **Structured output** | The pipeline's hard requirement. Every reply is re-validated against a Pydantic schema and a malformed one costs a retry and then the whole stage. Qwen2.5 is explicitly trained for structured output and holds a JSON schema better than similarly sized alternatives. |
| **Indonesian and English** | [ADR-0009](0009-natural-language-screening-criteria.md) made criteria free text in the recruiter's own language. Qwen2.5's multilingual coverage includes Indonesian, which Llama 3.1 8B handles noticeably less well. |
| **Runs on a laptop** | 7B at Q4 is the largest size that is comfortable on an ordinary developer machine. A 14B or 32B model would likely read a CV better and would put this project out of reach of the people meant to run it. |

**Smaller alternative, documented and supported:** `qwen2.5:3b-instruct`, about
1.9 GB. It runs on far less, and it is measurably worse at returning a reply that
survives schema validation. That is the trade, and the honest way to offer it is
to name the cost rather than present it as equivalent.

The model is **never downloaded by the application**. It is one documented
`ollama pull`, run once, by a person who knows what a 4.7 GB download is.
`scripts/check_llm.py --preflight` tells them if they have not.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Keep Anthropic as the default, add Ollama as an option | Leaves the wall exactly where it was. The default is what a fresh clone gets, and that is the case this decision exists to fix. |
| Delete the Anthropic client entirely | Tempting — it has never been exercised against a real key here, and this project removes unexercised code on principle. But deleting it would leave `LlmClient` with one real implementation and one replay stub, which is a weaker claim than the code currently earns. Kept, and its untested status stated wherever it might be read as more. |
| An OpenAI-compatible client, pointed at Ollama's compatibility endpoint | Would work, and would look more general. It is not: it trades Ollama's JSON-Schema `format` parameter — the thing that makes structured output reliable on a small model — for a looser `response_format`, to support providers this project does not have. |
| Run the model in-process (llama.cpp bindings, transformers) | Puts model loading, GPU detection and multi-gigabyte weights inside a FastAPI worker. Ollama already solves all three, is a single install, and keeps the failure modes on the other side of an HTTP boundary where they are easy to report. |
| Ship Ollama as a Docker Compose service | A `docker compose up` that pulls 4.7 GB is a hostile default, and GPU passthrough differs on every platform. Compose keeps one service — PostgreSQL — and Ollama is a documented host install, which is also how its own project recommends running it. |

## Consequences

**Benefits:** a fresh clone can screen a real CV against real criteria with no
account and no spending. Candidate documents stay on the machine running the
backend. The provider seam is now demonstrably a seam. The dependency footprint
went down rather than up.

### What one real run showed

Run against `qwen2.5:7b-instruct` on an ordinary Windows laptop (CPU), driving
the API over HTTP exactly as the UI does. Recorded because a decision like this
should not rest on expectations.

| | |
|---|---|
| Requirement extraction, informal Indonesian | 6.2 s, valid on the **first** attempt |
| Full screening per CV (profile + semantic match + score) | 22–27 s |
| Sample briefs surviving schema validation | 4 / 4, first attempt |

The pipeline behaved as designed, including in the way that matters most. On the
CV carrying injected instructions, the model returned `MATCHED` for
*"bisa bahasa Inggris"* and cited, as its evidence, the string
`"bisa bahasa Inggris"` — the **requirement's own text**, which appears nowhere
in that document. The verifier could not find it, the verdict was downgraded to
`NO_EVIDENCE`, and the refusal was recorded as `DOWNGRADED_UNVERIFIED`.

That is a real hallucination, from a real small model, caught by ordinary code
rather than by a prompt. It is the clearest evidence this project has produced
that the evidence-first rule ([ADR-0002](0002-evidence-first-evaluation.md)) is
load-bearing rather than decorative — and it is exactly the failure mode a
smaller model makes more likely.

The same run also showed the quality cost honestly. On the *strong* CV the model
matched *"bisa bahasa Inggris"* to the sentence *"Backend engineer working on
document-processing services."* — a quote that verifies, because it really is in
the document, supporting an inference that is thin at best. The system's answer
to that is not a better prompt: it is that the recruiter sees the quote next to
the verdict and can disagree with it.

**Costs, stated plainly:**

- **Model quality is different, and still unmeasured.** A 7B local model is not
  a frontier hosted model, and this repository makes no claim about how the two
  compare. The run above is a *sample*, not a measurement: it says the provider
  works and shows two examples of how the model behaves, and it produces no
  metric. The distinction the evaluation harness draws — deterministic
  correctness measured, model quality not measurable offline — applies unchanged.
  A concrete example of the gap, from the same run: on the mixed-language brief
  the model marked *"minimal 2 tahun pengalaman"* as **not** a must-have, though
  `minimal` is listed in the prompt as a hard-requirement word and the
  hand-written expectation has it as one. The confirmation gate exists partly
  for errors of exactly this size.
- **It is slower.** Seconds to tens of seconds per call on CPU, against under a
  second for a hosted API. Screening a batch of CVs is now a wait.
- **It needs software this repository cannot install.** Demo mode is unaffected —
  that is the point of keeping it the outer switch — but Local AI Mode needs
  Ollama running and a model pulled, and no amount of documentation removes that
  step.
- **`OLLAMA_BASE_URL` names a host this application will post CV text to.** It is
  operator configuration and no request path can influence it, but it is
  validated at startup and its residual risk is recorded in
  [security.md](../security.md).
- **"Local" describes the server, not the browser.** The UI says "running on the
  server this app is talking to" rather than "never leaves your computer",
  because in a deployed setup those are different machines.

**See also:** [ADR-0001](0001-ai-deterministic-boundary.md),
[ADR-0009](0009-natural-language-screening-criteria.md),
[`docs/architecture.md` §4](../architecture.md#4-where-the-llm-is-called),
[`docs/security.md`](../security.md), [`README.md`](../../README.md#demo-mode-and-local-ai-mode).
