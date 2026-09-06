# ADR-0003: Sensitive-attribute exclusion from scoring

**Status:** Accepted (Phase 0–1)

## Context

A CV routinely contains, or implies, attributes irrelevant to job qualification: name, photo, age or date of birth, gender, nationality, ethnicity, religion, marital status, home address. A hiring-adjacent tool that lets these attributes reach a scoring function — even if only to be instructed to "ignore" them — has not actually removed the risk that they influence the outcome. An instruction inside a prompt is not a control a security or fairness review can verify; a field that a downstream function cannot read at all, is.

The product spec is explicit that this system does not, and cannot, guarantee unbiased outcomes (see [`docs/product-spec.md` §13](../product-spec.md#13-bias-and-fairness-principles)); the goal of this decision is narrower and achievable: make the specific, listed sensitive attributes structurally absent from the data the matching and scoring stages can see.

## Decision

Sensitive attributes are excluded from the scoring input **by construction**, not by prompt instruction:

- The `candidate_profile` schema and its child tables (`profile_skill`, `profile_experience`, `profile_education`, `profile_project`) — the only tables the matching and scoring services read from — have **no column** for name, date of birth, age, gender, photo, nationality, ethnicity, religion, marital status, home address, phone, or email.
- The candidate's display name lives on the separate `candidate` table (`display_name`), used only for the UI to show which document is which. The scoring and matching services never query `candidate`, only `candidate_profile`.
- This is enforced as an automated test (`test_no_sensitive_column_exists_anywhere` in `backend/tests/test_schema.py`), which inspects the live ORM metadata for a fixed list of forbidden column names and fails the build if one appears — turning the principle into a regression test rather than a promise.

Two attributes are a deliberate, documented exception: `profile_education.institution` and `profile_experience.organization` **are** in the scoring input, because education and experience requirements are sometimes legitimately about accreditation, field of study, or employer domain. Both are well-known proxies for protected characteristics; the project does not claim they are neutral. This is recorded as a residual risk, not resolved away.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Include the fields, instruct the LLM/scoring logic to ignore them | Not independently verifiable — a prompt instruction can be forgotten, overridden by a later change, or simply not followed by the model. |
| Include the fields but redact them only in the UI | The scoring function would still have read access to them, and nothing would prevent a future feature from using them. |
| Drop `institution` and `organization` too, for a stronger guarantee | Would make genuinely relevant requirements ("PhD from an accredited institution", "5 years in fintech") impossible to evaluate, trading an achievable, honest control for one that quietly breaks a legitimate use case while producing an unearned sense of complete neutrality. |

## Consequences

**Benefits:** the exclusion is checkable by a static schema inspection, not an audit of every prompt and every code path that touches candidate data. It survives future refactors, because a new sensitive column added to `candidate_profile` fails a test immediately.

**Costs:** does not, and cannot, remove proxy signals still present in legitimately relevant fields (name-adjacent cues in free text, career-gap patterns, employer and institution names). This is why the product spec's fairness section states plainly that this control narrows a specific, listed risk — it does not make the system unbiased, and no broader claim is made.

**See also:** [`docs/product-spec.md` §13](../product-spec.md#13-bias-and-fairness-principles), [`docs/data-model.md` §5](../data-model.md#5-the-fairness-boundary-expressed-structurally), `backend/tests/test_schema.py::test_no_sensitive_column_exists_anywhere`.
