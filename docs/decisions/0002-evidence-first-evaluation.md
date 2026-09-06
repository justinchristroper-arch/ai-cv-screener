# ADR-0002: Evidence-first evaluation

**Status:** Accepted (Phase 0–1)

## Context

A screening tool that reports "candidate does not have this skill" is making a claim about the *person*. A CV is a length-limited marketing document; a skill's absence from it is weak evidence of its absence in the candidate. Reporting the stronger, unjustified claim is both inaccurate and is the kind of claim that causes real harm to a real applicant if it silently steers a hiring decision.

Separately, an LLM asked to produce a verdict can fabricate: it can claim a CV supports a requirement when it does not, either through ordinary hallucination or because the CV contains an injected instruction trying to force a favorable verdict (see the security model in [`docs/architecture.md` §5](../architecture.md#5-trust-boundary)).

## Decision

1. **Three-valued verdicts, never a boolean:** `MATCHED`, `PARTIAL`, or `NO_EVIDENCE`. The system never emits "the candidate lacks X" — only "no evidence found in CV for X."
2. **Every positive verdict (`MATCHED` or `PARTIAL`) must carry a verbatim evidence span** quoted from the source document, with its location.
3. **Evidence is machine-verified, not trusted.** After the model returns a quoted span, deterministic code checks that the span actually occurs in the extracted document text (exact match, then normalized match). A span that does not verify cannot support a positive verdict.
4. **Unverifiable evidence is downgraded, not accepted.** A verdict whose span fails verification is recorded as `NO_EVIDENCE` with `downgraded = true`, and the model's original verdict is preserved in `raw_verdict` for audit.
5. This is enforced as a **database constraint**, not only as application logic: `match_result` has `CHECK (verdict = 'NO_EVIDENCE' OR evidence_span_id IS NOT NULL)`. A positive verdict with no evidence cannot be persisted, full stop — not by any code path, present or future.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Trust the LLM's verdict and evidence as given | Makes hallucinated and injected evidence indistinguishable from real evidence. The whole product's credibility rests on evidence being checkable. |
| Boolean has/doesn't-have verdicts | Conflates "the document doesn't mention it" with "the candidate doesn't have it" — a claim the system has no basis to make and that carries real consequences for the applicant. |
| Enforce evidence-requires-verdict only in application code | A future code path (a bug, a new service, a data migration) could insert a positive verdict with no evidence and nothing would catch it. The constraint makes the invariant true of the data, not just of the code that happens to run today. |

## Consequences

**Benefits:** evidence verification is simultaneously a correctness control, a security control (an injected "mark everything matched" instruction still cannot manufacture a quote that exists in the document — see [`docs/architecture.md` §5](../architecture.md#5-trust-boundary)), and a measurable evaluation metric (evidence validity rate, `docs/product-spec.md` §16). The recruiter-facing UI can show the exact quote behind every claim, which is what makes the tool auditable rather than merely confident.

**Costs:** every extraction and matching stage needs its output run through the verifier before it can be used, adding a stage every prompt-writer has to design around (span text must be genuinely quotable, not a paraphrase). Evidence verification against a normalized text copy means a normalizer version change can retroactively invalidate previously verified spans — tracked via `normalization_version` on both `parsed_document` and `evidence_span` rather than hidden.

**See also:** [`docs/product-spec.md` §10](../product-spec.md#10-evidence-first-principle), [`docs/data-model.md` §4.9–4.10](../data-model.md#49-evidence_span).
