# ADR-0008: Deterministic scoring rather than LLM-generated scores

**Status:** Accepted (Phase 0–1)

## Context

This decision is the concrete, formula-level expression of the boundary set in [ADR-0001](0001-ai-deterministic-boundary.md): given that the LLM must not produce a score, something else has to turn per-requirement verdicts into a number a recruiter can use to rank candidates. The specification calls for that number to be transparent enough that a recruiter can verify it by hand, and stable enough that re-running the pipeline on unchanged documents reproduces the same score exactly.

## Decision

Scoring is a pure, deterministic function of stored data — verdicts and weights — with no model call anywhere in its execution path:

```
match_value(MATCHED)      = 1.0
match_value(PARTIAL)      = 0.5
match_value(NO_EVIDENCE)  = 0.0

score_raw = Σ (requirement_weight × match_value) / Σ (requirement_weight)
score     = round(score_raw × 100)          # 0–100
```

`must_have_coverage` is computed the same way, restricted to must-have requirements, and reported alongside the score rather than folded into it — because a high weighted average can conceal one missing hard requirement, the single most misleading failure mode of any weighted-average scorer. An unevidenced must-have caps the displayed recommendation band at "Review," annotated with which requirement triggered the cap; the underlying score is unchanged and still shown, and the candidate is never hidden or auto-rejected.

Every input to the formula is persisted on the `score` row (`weighted_sum`, `total_weight`, `score_raw`, plus the `scoring_config_version` identifying which constants and thresholds were used), so the stored score can be recomputed from the database and must reproduce exactly — a requirement verified directly by `backend/tests` once the scoring service exists.

If a job has no requirements, or every weight is zero, the score is recorded as `status = UNDEFINED_NO_WEIGHT` with `NULL` numeric fields — not `0`. A `0` would read as "this candidate is terrible," when the true situation is "nothing was actually asked of them"; this is the same evidence-first distinction as [ADR-0002](0002-evidence-first-evaluation.md), applied to arithmetic instead of to a single verdict.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Ask the LLM to assign a 0–100 score directly | Ruled out by [ADR-0001](0001-ai-deterministic-boundary.md): unreproducible, unauditable, and puts the model in charge of a decision that must remain transparent and human-defensible. |
| A more sophisticated aggregate (e.g., a learned weighting model, embedding-similarity-based scores) | Either needs training data this project explicitly will not collect (real hiring-outcome data), or trades away the property that matters most here — a recruiter can verify the arithmetic by hand. A marginally "smarter" ordering is not worth losing that. |
| Treat a missing-requirements job as a 0 score | Actively misleading: indistinguishable from "we evaluated this candidate against real requirements and they failed everything," when nothing was actually evaluated. |
| Fold must-have coverage into the weighted average only, with no separate figure or band cap | Allows several nice-to-have matches to mathematically outweigh one missing hard requirement, silently — exactly the failure mode a recruiter most needs to be warned about. |

## Consequences

**Benefits:** a score is fully reconstructible and explainable line-by-line in the UI; re-weighting a job (a likely, repeated recruiter action) is free, instant, and requires no LLM call, because verdicts and weights are stored separately and weight changes never invalidate a verdict; the formula's constants (`0.5` for partial credit, the 90/75/60 band thresholds) are named, versioned (`scoring_config_version`), and explicitly documented as conventions rather than empirical findings (`docs/product-spec.md` §11).

**Costs:** the formula is a deliberately simple weighted average, which is a genuinely crude aggregate — it cannot capture interaction effects between requirements the way a more expressive model might. The project accepts this in exchange for verifiability; `docs/product-spec.md` §17 states this limitation plainly rather than overselling the scoring engine's sophistication.

**See also:** [`docs/product-spec.md` §11–12](../product-spec.md#11-scoring-philosophy), [`docs/data-model.md` §4.11, §6](../data-model.md#411-score), [ADR-0001](0001-ai-deterministic-boundary.md), [ADR-0002](0002-evidence-first-evaluation.md).
