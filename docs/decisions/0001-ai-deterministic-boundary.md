# ADR-0001: AI vs. deterministic responsibility boundary

**Status:** Accepted (Phase 0–1)

## Context

An AI CV screener can be built as `CV → LLM → score`: hand the model a CV and a job description and ask it to return a number. This is the fastest thing to build, and it is also unverifiable, irreproducible, and impossible to audit — the score can change between two runs on identical input with no code change and no way to explain why, because the arithmetic exists only inside the model's forward pass.

The product is a decision-support tool for a domain (hiring) where a recruiter must be able to explain and defend a shortlist. An opaque number cannot be defended.

## Decision

Draw a hard line between what the language model does and what deterministic code does:

- **The LLM does language work only:** extracting structured requirements from a job description, extracting a structured candidate profile from a CV, judging semantic equivalence between a requirement and CV content, and identifying the evidence span that supports a claim.
- **Deterministic code does everything that must be reproducible, testable without a network call, and explainable:** validating LLM output against a schema, verifying evidence spans against source text, exact and alias-based matching, the scoring formula and its weights, thresholds, ranking, and every business rule.

The LLM never returns a score, a rank, or a hiring recommendation — only categorical verdicts (`MATCHED` / `PARTIAL` / `NO_EVIDENCE`) and supporting evidence. All arithmetic happens downstream, in code, from stored verdicts.

The one-sentence test used throughout the project: *the model decides what the text says; the code decides what that is worth. If a number can change without any document changing, that is a bug.*

## Alternatives considered

| Alternative | Why not |
|---|---|
| `CV → LLM → score` directly | Unreproducible, unauditable, and gives the model authority over a decision (scoring) that the product's own principles say a human must own. |
| LLM assigns partial-credit weights per requirement | Still opaque, and mixes the model's uncertainty about *evidence* with the product's judgment about *importance*, which should be an HR-owned, editable weight, not a model guess. |
| Hybrid where the LLM proposes a score and code "sanity-checks" it | The sanity check either trusts the LLM number (no different from the first alternative) or recomputes it independently (in which case the LLM's number was never used, and asking for it was wasted cost and a false signal of authority). |

## Consequences

**Benefits:** every score is a pure function of stored per-requirement verdicts and weights (see [ADR-0008](0008-deterministic-scoring.md)); re-weighting a job is free and instant because it never touches the LLM; the deterministic/LLM split is itself measurable (`match_result.decided_by`), which surfaces where the alias table needs work; the whole scoring and ranking test suite runs offline, with no API key.

**Costs:** more moving parts than a single prompt — a validation layer, an evidence verifier, a deterministic matcher, and a scoring engine all have to be built and tested independently of the LLM integration. The LLM's judgment on genuinely ambiguous semantic matches ("is a Flask side project evidence of backend experience?") is still ultimately a categorical verdict a human might disagree with; the boundary makes that disagreement visible and inspectable, not eliminates it.

**See also:** [`docs/product-spec.md` §7–8](../product-spec.md#7-ai-responsibilities), [`docs/architecture.md` §4](../architecture.md#4-where-the-llm-is-called).
