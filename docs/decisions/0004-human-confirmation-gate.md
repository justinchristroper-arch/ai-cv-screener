# ADR-0004: Human confirmation gate for extracted requirements

**Status:** Accepted (Phase 0–1)

## Context

Requirement extraction (job description → structured, weighted, categorized requirements) is the first stage of the pipeline and every later stage depends on its output. If the LLM extracts a wrong, missing, or miscategorized requirement, that error is silent — it does not look like an error, it looks like a slightly different set of criteria — and it corrupts every candidate's score in the job without anyone noticing until a recruiter wonders why a strong candidate scored oddly.

This is the single highest-leverage failure point in the whole pipeline, because it happens once per job but its effect multiplies across every candidate scored against that job.

## Decision

Requirement extraction output is never used for scoring until a human has reviewed and explicitly confirmed it:

- `job.requirements_confirmed_at` is `NULL` until HR confirms the requirement set. This is the gate.
- HR can edit requirement text, category, must-have flag, and weight, and can add or remove requirements, before confirming.
- The service layer — not only the UI — refuses to score any candidate against a job whose `requirements_confirmed_at` is `NULL`. No API path or background task can bypass this.
- While confirmed, requirement **text** and **category** are immutable; changing them requires explicitly unconfirming the job first, which invalidates every existing match result for that job. This makes the invalidation a deliberate, visible act rather than something that happens silently underneath a routine edit.
- Weight and the must-have flag remain editable at any time, confirmed or not, because they do not affect verdicts — only the arithmetic applied to already-computed verdicts (see [ADR-0008](0008-deterministic-scoring.md)) — so changing them costs nothing and requires no re-extraction.

The three `proposed_*` columns on `requirement` (`proposed_text`, `proposed_category`, `proposed_must_have`) record what the LLM originally proposed, written once and never updated. The gap between what was proposed and what HR confirmed is a labeled correction signal that accumulates from ordinary use — the seed of a future evaluation dataset — at essentially no cost to collect.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Score immediately on extraction, let HR fix mistakes after the fact | By the time a mistake is visible in a strange-looking ranking, every candidate has already been scored against the wrong criteria; the fix requires noticing something is wrong, not merely reviewing what was proposed. |
| Confirmation as a UI-only speed bump (a button), no server-side enforcement | A UI gate is not a gate — any other client, script, or future code path could call the scoring endpoint directly and bypass it entirely. |
| Auto-confirm after some idle period if HR does not act | Defeats the purpose: the point is an explicit human decision, not a default that fires regardless of whether anyone looked. |

## Consequences

**Benefits:** converts a hidden, expensive-to-diagnose error into a visible, cheap-to-fix one, at the one point in the pipeline where a human is already expected to be paying attention. Produces a standing, free evaluation signal (the proposed-vs-confirmed diff) without a dedicated data-collection effort.

**Costs:** adds a mandatory manual step to every job before any candidate can be scored — there is no "fully automatic" mode for the MVP, which is a deliberate trade against speed in favor of correctness and accountability. Unconfirming a job to fix a requirement invalidates match results for that job, so a correction found late in a batch's processing has a real, visible re-computation cost — which is the honest price of "text and category are load-bearing and must not silently drift."

**See also:** [`docs/product-spec.md` §4 (step 5)](../product-spec.md#4-user-workflow), [`docs/architecture.md` §3.1](../architecture.md#31-the-confirmation-gate), [`docs/data-model.md` §4.1, §4.3, §7](../data-model.md#41-job).
