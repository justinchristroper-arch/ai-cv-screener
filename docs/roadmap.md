# AI CV Screener — Development Roadmap

**Status:** every phase delivered. Phases 0–7, 9–12 and 15–17 are complete. Five carry an outstanding item, and in every case the item needs something this repository cannot do for itself rather than more code: **8 and 13** need one run against a live model (`scripts/check_llm.py`); **3, 14 and 18** need the first push to GitHub, so CI can be observed and the repository's own metadata set; **19 and 20** need a hosting decision that belongs to the repository owner. Nothing is deployed and nothing has been pushed.
**Last updated:** 2026-09-07
**Product definition:** [product-spec.md](product-spec.md)

---

## How this roadmap is used

Development is strictly phase-by-phase. One phase is planned, implemented, tested, verified, committed, and reviewed before the next begins.

For every phase:

1. State the objective.
2. Inspect the current state of the project.
3. Plan.
4. Implement.
5. Run the verification commands.
6. Fix failures, re-run, and only continue once green.
7. Summarize what was built and why.
8. Commit.
9. Stop for review.

Rules that hold across every phase:

- **Nothing is reported as working unless it was actually executed and observed to pass.** Test output is never fabricated or paraphrased optimistically.
- **A failing test stops the phase.** Diagnose the root cause, fix it, re-run. Do not skip, weaken, or delete a test to make a phase pass.
- **Verification criteria are written before implementation** so a phase cannot be quietly redefined to match whatever got built.
- **No secret enters the repository, in any phase.**
- **Later phases may revise earlier decisions**, but the change is recorded in the relevant document rather than applied silently.

Phase status legend: ✅ complete · 🚧 in progress · ⬜ not started

### Milestones

From Phase 6 onward the remaining phases are grouped into larger **milestones**
that are planned, built, verified and committed as one unit. The phases below
are unchanged — they remain the definition of what has to be delivered and how
it is verified — but several of them now land in a single commit rather than one
each.

| Milestone | Phases | Status |
|---|---|---|
| **Candidate Intelligence** | 6, 7, and the semantic-evaluation half of 8 | ✅ |
| **AI Evaluation Engine** | 9 | ✅ |
| **Deterministic Ranking** | 10 | ✅ |
| **Product UI + Demo** | 11, 12 | ✅ |

Phase 8's semantic evaluation was pulled into this milestone rather than
deferred, because Phase 7's routing layer has nowhere to route to without it:
the deterministic matchers settle five of the thirteen pairs on the sample CV,
and the remaining eight would have had no verdict at all. What is *not* in the
milestone is Phase 8's measurement work — cost per CV, and semantic-equivalence
rates over a sample set — which needs the evaluation harness from Phase 13.

---

## Progress at a glance

| Phase | Title | Status |
|---|---|---|
| 0 | Product definition and project specification | ✅ |
| 1 | Architecture and data model | ✅ |
| 2 | Local development environment | ✅ |
| 3 | Git repository setup | ✅ (see note) |
| 4 | Job description processing | ✅ |
| 5 | CV upload and PDF parsing | ✅ |
| 6 | Candidate profile extraction | ✅ |
| 7 | Requirement matching engine | ✅ |
| 8 | LLM semantic evaluation | 🚧 |
| 9 | Transparent scoring engine | ✅ |
| 10 | Candidate ranking | ✅ |
| 11 | Frontend application | ✅ |
| 12 | Demo mode with synthetic candidates | ✅ |
| 13 | Evaluation and benchmark | ⬜ |
| 14 | Automated testing | ⬜ |
| 15 | Security and reliability review | ⬜ |
| 16 | UI/UX polish | ⬜ |
| 17 | Documentation | ⬜ |
| 18 | GitHub repository cleanup | ⬜ |
| 19 | Deployment | ⬜ |
| 20 | Final end-to-end verification | ⬜ |

---

## Phase 0 — Product definition and project specification ✅

**Objective.** Define what is being built, for whom, and under which constraints — before any code exists — so that later phases have a fixed reference for scope, the AI/deterministic boundary, scoring, fairness, and security.

**Deliverables.**
- `docs/product-spec.md` — product overview, problem, users, workflow, MVP scope, feature list, AI vs deterministic responsibilities, requirement taxonomy, evidence-first principle, scoring philosophy, recommendation heuristic, bias and fairness, security, demo and evaluation requirements, limitations, future work.
- `docs/roadmap.md` — this file.
- `README.md` — honest project status, planned architecture, planned stack.
- `.gitignore` and `.env.example` — present before the first commit so no secret can ever enter history.
- Git repository initialized with a clean initial commit.

**Verification criteria.**
- All four documents exist and are non-empty.
- Every section required by the brief is present in the specification.
- Every relative link between documents resolves to an existing file.
- No secret-shaped value appears in any tracked file; `.env` is ignored.
- The README describes unimplemented features as planned, never as existing.
- `git log` shows exactly one clean initial commit; `git status` is clean.

---

## Phase 1 — Architecture and data model ✅

**Objective.** Decide the system's shape and its persistent data model, and write both down, before writing code that would silently fix these decisions in place.

**Deliverables.**
- `docs/architecture.md` — modular monolith layering (API → services → models), module boundaries, the pipeline stage by stage, where each LLM call sits, the trust boundary for untrusted CV content, error and retry policy, and the rationale for each significant choice with its rejected alternatives.
- `docs/data-model.md` — entities and relationships: `Job`, `JobDescription`, `Requirement`, `Candidate`, `CandidateDocument`, `ParsedDocument`, `CandidateProfile`, `EvidenceSpan`, `MatchResult`, `Score`, `LlmCallLog`. Field-level definitions, enumerations, constraints, indexes, and an ER diagram.
- Documented decision on whether pgvector is needed for the MVP, and if so, for what exactly.
- Resolution of the open questions listed in `product-spec.md` section 19.

**Verification criteria.**
- Every entity in the pipeline described in the specification has a home in the data model.
- The complete flow from JD upload to ranked candidates can be traced through the documented modules with no gaps.
- Every score in the model is reconstructible from stored rows — no derived-only-in-memory values.
- Model output storage retains model id and prompt version for auditability.
- The ER diagram renders.
- No code is written in this phase.

---

## Phase 2 — Local development environment ✅

**Objective.** Make the project runnable on a clean machine with a short, documented sequence of commands.

**Deliverables.**
- `backend/` — FastAPI application skeleton, settings loaded from the environment, health endpoint, dependency pinning.
- `frontend/` — Vite + React skeleton that builds and starts.
- PostgreSQL via Docker Compose, with pgvector available if Phase 1 concluded it is needed.
- Database migrations wired up (Alembic).
- Test runner configured for both backend and frontend.
- Linting and formatting configured.
- `docs/development.md` — setup instructions.

**Verification criteria.**
- From a clean checkout, the documented commands bring up the database, backend, and frontend.
- `GET /health` returns a success response — observed, not assumed.
- The migration command runs against an empty database and succeeds.
- The frontend dev server starts and serves a page.
- The test command runs and passes, even with a near-empty suite.
- The linter passes.
- Backend starts and reports a clear, actionable error when a required environment variable is missing.

---

## Phase 3 — Git repository setup ✅ (one criterion pending push)

**Objective.** Establish repository hygiene and the conventions that keep the history reviewable.

**Deliverables.**
- Branch and commit-message conventions recorded in `CONTRIBUTING.md`. ✅
- `.gitignore` extended to cover the real toolchain now that it exists (Python, Node, Docker, IDE, uploads). ✅ (already covered as of Phase 2; reverified)
- CI workflow running lint and tests on push. ✅ [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — three jobs (backend, frontend, docs); every step re-verified locally, including against a fresh, isolated PostgreSQL container standing in for the CI service.
- `docs/decisions/` for lightweight architecture decision records, seeded with the Phase 1 decisions. ✅ 8 ADRs + index, see [`docs/decisions/README.md`](decisions/README.md).
- Repository metadata: license, description, topics. ✅ MIT [`LICENSE`](../LICENSE); description and GitHub topics are set at push time, not before (they're a GitHub repository setting, not a file — see the note below).

Also delivered, beyond the original list, because they turned out to be needed to actually meet this phase's objective:
- Language version pinning (`backend/.python-version`, `frontend/.nvmrc`, `engines` + `engine-strict` in the frontend) — closes a reproducibility gap Phase 2 explicitly flagged.
- The `httpx` → `httpx2` dependency fix, removing the deprecation warning Phase 2 reported (one unrelated, unfixable-by-us upstream warning remains — documented in [`docs/development.md` §5](development.md#5-install-backend-dependencies)).
- [`scripts/check_docs.py`](../scripts/check_docs.py) — the ad-hoc link-verification logic used by hand in Phases 0–2, promoted into a real, tested, reusable tool, wired into CI as its own job.
- A one-time secret-oriented audit of the working tree and the full git history (detect-secrets pattern scan + a manual history grep for common secret shapes) — see the Phase 3 completion report for findings.
- `.github/ISSUE_TEMPLATE/bug_report.md` and `.github/pull_request_template.md`.

**Verification criteria.**
- A fresh `git clone` into a new directory contains no secrets, no virtualenv, no `node_modules`, no uploaded files, and no database data. ✅ Verified: full git-history scan (not just the working tree) found no `.env`, no real-secret-shaped string, and no accidentally committed dependency directory in any commit.
- CI runs and passes on the current commit — observed in the CI output. **⬜ NOT YET MET.** Every job's steps were run locally and pass, including the backend job's full sequence against a freshly created, isolated PostgreSQL container — but this repository has not yet been pushed to GitHub, so no workflow run has actually executed on GitHub Actions. "The workflow file is correct and its steps pass locally" and "CI passed on GitHub" are different claims; only the first is true as of this phase. This will be updated to ✅ with a link to the passing run once the repository is pushed.
- `git status` is clean after a full local build and test run, proving the ignore rules cover every generated artifact. ✅

---

## Phase 4 — Job description processing ✅

**Objective.** Turn a job description into a structured, human-confirmed requirement set — the first LLM feature, and the input on which every later score depends.

**Deliverables.**
- Endpoints to create a job, attach a JD, and retrieve it. ✅
- LLM requirement-extraction service with a versioned prompt and a strict output schema. ✅ `app/services/jd_extraction.py`, prompt `jd-extraction-v1`.
- Schema validation, one retry, and an explicit failure path. ✅ Exactly one retry, carrying the specific validation errors so the second attempt can correct the first.
- Requirement CRUD so HR can edit text, category, must-have flag, and weight. ✅
- Confirmation endpoint that freezes the requirement set. ✅
- A stored record of every LLM call: model, prompt version, token usage. ✅ Every attempt, success or failure, written to `llm_call_log`.

Also delivered:
- The `LlmClient` abstraction (`LiveLlmClient` / `ReplayLlmClient`) with the two no-fallback rules from [architecture §4.2](architecture.md#42-the-client-abstraction-demo-mode-and-provider-selection), and six recorded fixtures covering the happy path, a recoverable retry, an unrecoverable double failure, and a prompt-injection attempt.
- `app/core/errors.py` — domain errors with a structured, non-leaking HTTP mapping.

**Verification criteria.**
- A realistic JD yields atomic requirements — one testable claim each, verified by inspection against a sample JD. ✅ The bundled backend-engineer JD's "Python, FastAPI, PostgreSQL and Docker" line becomes four separate requirements; asserted directly.
- Categories and must-have flags are populated and are valid enumeration values. ✅ All five categories exercised, with both flag values.
- Malformed model output is rejected by validation rather than propagated; covered by a test with a fabricated bad response. ✅ Eight malformed shapes, each asserted to persist nothing.
- Editing and confirming a requirement set persists correctly. ✅
- Scoring against an unconfirmed requirement set is refused by the API. ✅ Enforced in the service (`get_confirmed_requirements` raises), which is what every future caller must go through — not only in a route.
- Tests pass without a live API key, using recorded responses. ✅ The whole suite runs offline; no network call is made.

**Not done in this phase, deliberately:** no CV upload, parsing, matching, scoring, ranking, or frontend work — those are Phases 5-11. No migration was created: the Phase 1 data model already had every column this phase needed, and `alembic check` confirms no drift.

---

## Phase 5 — CV upload and PDF parsing ✅

**Objective.** Accept CV files safely and convert them to text with location information, handling failure honestly.

**Deliverables.**
- Batch upload endpoint with size, count, page, and content-type validation by magic bytes. ✅ The `Content-Type` header is never consulted; the file's own signature decides.
- Safe storage using generated identifiers, never client-supplied filenames. ✅ `app/core/storage.py` — the path comes from the document UUID and nothing else.
- PDF text extraction with page and character offsets retained. ✅ `app/services/document_parsing.py`, using pypdf (BSD-3; PyMuPDF was rejected as AGPL-incompatible with this project's MIT licence).
- Text normalization, applied identically here and in evidence verification later. ✅ `app/core/text.py`, versioned `text-normalize-v1`.
- Detection of empty or image-only text layers, surfaced as a candidate-level `failed` status with a reason. ✅ `NO_TEXT_LAYER`.
- Per-file status tracking through the pipeline. ✅ `UPLOADED → PARSING → PARSED`, or `FAILED` with a reason.

**Verification criteria.**
- A multi-file upload of sample PDFs parses, with the extracted text inspected against the source. ✅ Verified against a live uvicorn server over real multipart HTTP, not only TestClient.
- A scanned / image-only PDF is reported as unparseable — it does not silently produce an empty profile. ✅ Fails as `NO_TEXT_LAYER`, and no `parsed_document` row is written.
- A corrupt or non-PDF file is rejected with a clear error and fails only its own candidate, not the batch. ✅ A six-file batch with two bad files yielded four candidates and two per-file rejections.
- An oversized file is rejected before parsing. ✅ Size is checked before anything opens the file.
- A filename containing path-traversal characters cannot influence the storage path; covered by a test. ✅ `../../../../etc/passwd.pdf` stored under a UUID path; the name survives only as sanitized display metadata.
- Offsets round-trip: a substring taken at a stored offset returns the expected text. ✅ `full_text[start:end]` returns exactly one page, asserted in unit tests and against the live server.

**Scope notes.** No migration was needed — the Phase 1 schema already had every column, and `alembic check` reports no drift. **There is no OCR**, so scanned CVs are unsupported by design and fail honestly. Language detection is not implemented, so `language_detected` is always NULL and the `UNSUPPORTED_LANGUAGE` reason is reserved but unused. Parsing runs inline rather than in a background task: architecture section 7 requires background processing for the minutes of LLM work in later stages, none of which exists yet.

---

## Phase 6 — Candidate profile extraction ✅

*Delivered as part of the **Candidate Intelligence** milestone.*

**Objective.** Turn CV text into a typed candidate profile whose every claim is traceable to the source document.

**Deliverables.**
- Versioned extraction prompt with a strict schema: education, skills, roles with dates, projects. ✅ `app/llm/prompts/profile_extraction.py`, prompt `profile-extraction-v1`; schema in `app/schemas/llm/profile_extraction.py`.
- Every extracted item carries an evidence span with offsets. ✅ The **model supplies only the quote**; the offsets are found by our verifier, which is what makes them checkable at all.
- Deterministic evidence verification: each span must occur in the normalized source text; unverifiable items are flagged. ✅ `app/services/evidence.py` — exact match, then a folded match (case, whitespace, quote and dash variants) whose offsets map back to the real characters.
- Sensitive attributes are absent from the schema by construction. ✅ No field exists for one, and `extra="forbid"` rejects a whole reply that invents one.
- Prompt-injection handling: CV text is confined to a delimited data channel; suspicious patterns are flagged, not stripped. ✅
- Caching by document content hash. ✅ A candidate that already has a profile is returned as-is; a document whose `text_sha256` matches one already extracted is copied, with every quote re-verified against the new document rather than having offsets copied across.

**Verification criteria.**
- Sample CVs produce profiles that match the documents on manual inspection. ✅ Verified against a live uvicorn server over real multipart HTTP, not only TestClient.
- Evidence validity rate is measured and reported on the sample set, with its denominator. ✅ 10 of 10 items verified on the bundled CV, and the API returns that figure with its denominator on every profile (`evidence_summary`). This is one document, not a benchmark — a measured rate over a labelled set is Phase 13.
- A CV containing an injected instruction does not alter the extraction behaviour, and the attempt is flagged; covered by an explicit test. ✅
- A fabricated evidence span in a recorded response is caught by the verifier; covered by a test. ✅ The item is stored and flagged `UNVERIFIED` rather than deleted, and it cannot decide a pair.
- No sensitive attribute appears anywhere in a stored profile; covered by a test asserting the absence of such fields. ✅ The bundled CV deliberately prints a full personal-details block, and a test asserts that none of it reaches the profile, its free-text fields, or any evidence quote.
- Re-extracting an identical document hits the cache and issues no second LLM call. ✅ Asserted with a client that raises if it is called at all.

---

## Phase 7 — Requirement matching engine ✅

*Delivered as part of the **Candidate Intelligence** milestone.*

**Objective.** Produce a verdict for every (requirement, candidate) pair, using deterministic rules wherever they suffice.

**Deliverables.**
- Deterministic matchers: exact and alias-based skill matching, normalized comparison, date arithmetic for duration requirements. ✅ `app/services/matching.py`.
- A skill alias table (`Postgres` / `PostgreSQL`, `JS` / `JavaScript`, and so on). ✅ Seeded by a data-only migration, `c1a7f3b90e42`, with 22 unambiguous pairs. Genuinely ambiguous abbreviations are left out on purpose: `tf` means both Terraform and TensorFlow, and a wrong alias produces a confident, wrong `MATCHED`.
- A routing layer that decides which pairs a deterministic rule can settle and which must go to the LLM. ✅ On the sample CV, 5 of 13 pairs never reach the model.
- `MatchResult` persistence: verdict, evidence, reason, and which method decided it. ✅

**Verification criteria.**
- Unit tests cover exact match, alias match, duration satisfied and not satisfied, and absence. ✅
- Every result records whether it was decided deterministically or by the LLM. ✅ `decided_by`, asserted per method.
- Absence produces `NO_EVIDENCE`, never a negative claim about the candidate; asserted in tests against the stored reason text. ✅ The `NO_EVIDENCE` wording is written by the **application**, not by the model, precisely so that it cannot drift.
- The matcher is pure and testable with no network access — the full matching suite runs offline. ✅ The deterministic rules are pure functions; the duration arithmetic takes its reference date as an argument rather than reading the clock.
- The routing decision is logged, so the deterministic/LLM split is measurable. ✅ Logged per run, and returned in the API summary.

**Design note on the duration matcher.** It decides in one direction only. A
shortfall is arithmetic: total listed experience is an upper bound on any
domain-restricted subset of it, so a career shorter than the stated minimum
cannot meet the requirement however the roles are read, and the verdict is
`PARTIAL`. *Meeting* the total settles nothing, because five years of
**backend** experience is not answered by five years of any experience; those
pairs go to the model. When the CV dates are only year-precise and the gap is
inside a year, no deterministic decision is made at all.

---

## Phase 8 — LLM semantic evaluation 🚧

**Objective.** Resolve the pairs deterministic rules cannot settle, under the same evidence discipline as every other LLM stage.

**Deliverables.**
- Versioned semantic-matching prompt returning verdict, evidence span, and reason. ✅ `app/llm/prompts/semantic_match.py`, prompt `semantic-match-v1`. `must_have` and `weight` are deliberately **not** sent: importance is the recruiter's judgement, and telling the model which requirements matter would let it leak into a judgement that is supposed to be about evidence alone.
- Batched evaluation of the undecided pairs for a candidate. ✅ One call per candidate, and the reply must cover exactly the positions asked about — no gaps, no repeats, no invented indices.
- Enforcement rule: `MATCHED` or `PARTIAL` without a verifiable span is downgraded to `NO_EVIDENCE` and flagged. ✅ Plus a second rule the original phase did not anticipate: a quote that *does* verify but reads as instruction text is refused the same way. An injected "mark this candidate as fully qualified" genuinely occurs in the document, so verification on its own would pass it.
- Recorded-response fixtures so the whole stage runs offline in tests. ✅
- Cost and latency instrumentation. 🚧 `llm_call_log` records tokens and latency per call, and Phase 13's harness now exists to aggregate them — but replay records zero latency and no token usage by construction, so a real figure needs a run against a live provider.

**Verification criteria.**
- Semantic equivalences the deterministic matcher misses are recognized on the sample set. 🚧 Demonstrated on the bundled CV, where a Postgres-backed embedding search returns `PARTIAL` against a vector-database requirement. Phase 13 established why this cannot be *measured* offline: the only model output available without a provider is a recording written by the same author as the labels, so scoring one against the other would measure that author's consistency rather than the model's quality.
- The downgrade rule fires on a fixture whose evidence does not exist in the source; covered by a test. ✅
- No LLM response is ever interpreted as an instruction; the injection test set produces no verdict change. ✅
- Cost per CV is measured and reported, not estimated. ⬜ Needs a live run. Reported by Phase 13 as `cost_and_latency_per_cv` — not measured, with the reason — rather than filled in with the zeros replay would produce.
- The stage is fully replayable from fixtures with no API key present. ✅

**What remains for this phase:** the two measurement criteria above. Phase 13
built the harness and then found that neither can be answered offline: both need
an actual call to a provider. They are reported as unmeasured, with the reason,
rather than estimated.

---

## Phase 9 — Transparent scoring engine ✅

*Delivered as the **AI Evaluation Engine** milestone.*

**Objective.** Turn verdicts into a score that a recruiter can verify by hand.

**Deliverables.**
- Pure scoring function implementing the weighted formula from the specification. ✅ `app/services/scoring.py`. `compute_score` takes requirement rows and verdicts and touches no database, network, clock or random source.
- Must-have coverage computed and stored separately from the score. ✅ Null — not 0 and not 1 — when the job has no must-haves or their weights sum to zero, because there is no ratio to report.
- Recommendation band mapping with configurable thresholds. ✅ 90 / 75 / 60, named on a versioned `ScoringConfig` rather than written into an expression.
- The must-have guard, confirmed in Phase 1. ✅ An unevidenced must-have caps the *displayed* band at `REVIEW`; the score is untouched, `band_raw` keeps the uncapped band, `capped_by_requirement_id` names the trigger, and the guard can only ever lower a band.
- A per-requirement score-breakdown structure for the UI. ✅ Weight, verdict, verdict value and points per line, in display order.
- Defined behaviour for edge cases: no requirements, all weights zero, single requirement. ✅

**Verification criteria.**
- Unit tests assert exact expected scores for hand-computed cases. ✅ Including the specification's own worked example (5/3/2 against MATCHED/PARTIAL/NO_EVIDENCE = 65) and the bundled fixture end to end (25.5 / 31 = 82).
- Recomputing a score from stored rows reproduces the stored value exactly. ✅ `load_breakdown` rebuilds the arithmetic from the requirements and verdicts still in the database; asserted field by field against the stored row.
- Scoring is a pure function: no I/O, no network, no clock, no randomness — enforced by the tests running fully offline. ✅
- The band boundaries are tested at their exact edges (59/60, 74/75, 89/90). ✅
- Division-by-zero and empty-requirement cases are handled explicitly, not by exception. ✅ Both produce `status = UNDEFINED_NO_WEIGHT` with NULL numbers, which removes the division by construction.
- The breakdown sums to the total. ✅ Asserted in the service tests and again over HTTP. A property test over generated inputs was **not** written: the sum is an invariant of one expression over a list, and a generator would restate it rather than probe it.

**Two decisions this phase had to make that the specification left open.**

*Rounding is half-up.* `round(score_raw × 100)` is ambiguous in Python, whose
built-in `round` is banker's rounding and would turn 62.5 into 62. A recruiter
checking the arithmetic on paper expects 63, so `Decimal` with `ROUND_HALF_UP`
is used and the 0–100 figure is scaled from the *stored* `score_raw` rather than
from an unrounded intermediate nobody kept.

*Staleness is enforced, not detected.* The data model has no matching-run
identifier, by design — `match_result` is unique per (requirement, candidate)
and a re-run replaces the whole set. Rather than invent one, this phase
implements the invalidation table in [data-model.md §7](data-model.md#7-staleness-and-invalidation)
in `app/services/invalidation.py`: re-running matching drops that candidate's
score, editing a weight or the must-have flag drops the job's scores, and
unconfirming a job drops its match results and scores. A stale score is deleted
rather than served, so a stored number always belongs to the verdicts it was
computed from.

**Not done in this phase, deliberately:** no ranking, no top-K, no shortlisting,
and no automatic accept or reject. No migration was needed — the Phase 1 schema
already had every column, and `alembic check` confirms no drift.

---

## Phase 10 — Candidate ranking ✅

*Delivered as the **Deterministic Ranking** milestone.*

**Objective.** Order candidates within a job, stably and explicably.

**Deliverables.**
- Ranking service ordering by score, with a documented deterministic tie-break. ✅ `app/services/ranking.py`, implementing the order fixed in [data-model.md §9](data-model.md#9-ranking-and-tie-breaking) rather than inventing one. The sort key is a pure function of five fields, so the tie-break is testable with no database.
- Candidates in a failed state are surfaced separately, never silently dropped. ✅ Three groups — `ranked`, `not_yet_scored`, `failed` — and a summary whose counts let a recruiter reconcile the list against what they uploaded. Status is checked before the score, so a failed candidate is reported as failed even if an earlier run left a score behind.
- Ranked-list endpoint returning score, band, must-have coverage, and warning flags. ✅ `GET /api/jobs/{job_id}/ranking`. The warning codes are `MUST_HAVE_NOT_EVIDENCED`, `SCORE_UNDEFINED`, `EVIDENCE_DOWNGRADED` and `INSTRUCTION_LIKE_TEXT_IN_CV` — all drawn from data earlier stages already stored, and none of them changes a score or a position. The last of these is where the injection flag raised at parse time finally reaches a human.

**Verification criteria.**
- Ranking is deterministic across repeated runs on identical input, including ties. ✅ Asserted on the pure ordering (same input, and reversed input, both give the same list), through the service, and over HTTP.
- A failed candidate appears in the response with its failure reason rather than disappearing. ✅
- No candidate is filtered out by score at any point in the API; asserted by a test with a very low-scoring candidate. ✅ The endpoint also takes **no** parameter that could hide anyone — a test asserts its only parameter is `job_id`, and that unrecognised query parameters change nothing.
- Ordering is tested against a hand-constructed expected order. ✅ Eight candidates exercising every rule at once, fed in shuffled.

**Two decisions this phase had to make.**

*A separate endpoint from the candidate list.* Section 9 below originally named
`GET /api/jobs/{job_id}/candidates` as the ranked list. That endpoint already
existed, returning upload and parse state for candidates in *any* state, and the
ranked list needs a **grouped** shape — failed candidates must never be merged
into the order — which a flat array cannot carry without inventing null
positions. The two answer different questions: "what happened to my uploads"
and "the ranked shortlist". They are now two endpoints, and
[architecture.md §9](architecture.md#9-planned-api-surface) records the split.

*No confirmation gate on ranking.* Every other stage that reads confirmed
requirements goes through the gate, but ranking produces nothing — it reads
stored scores. Unconfirming a job already discards every score in it, so its
candidates simply appear under `not_yet_scored`, which is the honest thing to
show. Refusing the request instead would hide correctly-labelled data from the
recruiter.

**Not done in this phase, deliberately:** no top-K, no shortlisting, no
pagination, no cross-job comparison, and no frontend. No migration was needed —
ranking joins `candidate` to `score` and adds no column, and
[data-model.md §8](data-model.md#8-indexes) had already concluded that the
existing indexes suffice at MVP scale, so no speculative ranking index was added.

---

## Phase 11 — Frontend application ✅

*Delivered as the **Product UI + Demo** milestone, together with Phase 12.*

**Objective.** Build the recruiter-facing interface for the full workflow.

**Deliverables.**
- Job list and job creation. ✅
- JD entry, extraction trigger, requirement review and edit table, and the confirmation gate. ✅ Weight and must-have are editable inline; text, category, add and delete are only offered while the job is unconfirmed, which is what the API allows.
- Batch CV upload with per-file progress and status. ✅ Each file's outcome is reported against its own name — accepted, rejected with the reason, or failed — and screening runs profile → matching → scoring per candidate with a progress bar and a per-candidate failure list.
- Ranked candidate list. ✅ With the not-yet-screened and failed groups beside it, so every uploaded file is accounted for.
- Candidate detail: matched / partial / no-evidence groups, evidence quotes with source location, score breakdown, must-have coverage, warnings. ✅
- Error, empty, and loading states throughout. ✅
- Evidence-first wording enforced in the UI copy. ✅ The vocabulary lives in one module, `src/display.ts`, so "No evidence found in CV" cannot drift into a claim about a person in one component while staying correct in another — and a test asserts none of the shared strings ever says a candidate lacks anything.

**Verification criteria.**
- The complete workflow is exercised in the browser end to end and observed to work. ✅ Both the one-click demo seed and a hand-driven run: create a job, load the sample description, extract 13 requirements, confirm, and watch the gate lift.
- The requirement confirmation gate cannot be bypassed from the UI. ✅ The screening action is not rendered at all until the job is confirmed, and the backend refuses it independently regardless.
- All CV-derived text renders escaped; a candidate whose CV contains HTML or script markup renders it inertly. ✅ There is no `dangerouslySetInnerHTML` anywhere in the application; a test feeds `<img src=x onerror=…>` through an evidence quote and asserts no element is created.
- Low-scoring candidates are visible and openable. ✅
- Heuristic thresholds are labeled as heuristic where scores are shown. ✅ The band never appears without its score, its must-have coverage and the caveat.
- The frontend builds with no errors and no type errors. ✅

**Two deviations from [architecture.md §10](architecture.md#10-frontend-architecture), both deliberate.**

*No query library.* Section 10 planned one. This application has three screens
and a dozen endpoints, each screen loads its data once and refetches after an
action it triggered itself, and there is no cache shared between screens to
coordinate. A 90-line `useResource`/`useAction` pair does the two things that
actually matter — abort on unmount, and never write state after unmount — and
configuring a query library would have been more code than replacing it.

*No routing library.* Hash routing in thirty lines, which also means the
production build is a static bundle that works from any path with no server
rewrite rule. Three routes did not justify a dependency.

Both keep the frontend at **zero runtime dependencies beyond React**.

---

## Phase 12 — Demo mode with synthetic candidates ✅

*Delivered as the **Product UI + Demo** milestone, together with Phase 11.*

**Objective.** Make the project runnable and convincing without an API key, real data, or cost.

**Deliverables.**
- `data/sample/` — synthetic CVs. ✅ Three, covering a strong match, an adversarial CV carrying injected instructions, and an image-only PDF. Generated from the page content in `backend/tests/pdf_fixtures.py`, so the text inside the binaries is readable in the repository. The sample **job description is not duplicated** — it is read from the recorded extraction fixture, so there is one copy and it cannot drift.
- Recorded LLM response fixtures covering the sample set. ✅ Already bundled from earlier milestones; this phase makes them reachable from the UI.
- A demo flag that routes all LLM calls to fixtures. ✅ `DEMO_MODE`, unchanged since Phase 4. The UI reads it from `/health` and says which mode it is in, including the consequence: only the sample documents can be analysed. A job description that is **not** the sample is flagged as such on the job screen, above the extract action rather than after it, and the failure — if the action is taken anyway — explains the limitation in ordinary words and states plainly that nothing was sent to a model. Internal terms such as recorded fixtures, prompt versions and input hashes never appear in the interface.
- A seed command producing a ready-to-browse demo state. ✅ `POST /api/demo/jobs`, one click from the job list. **Refused unless the server is in demo mode** — an endpoint that manufactures candidate records has no place in a deployment handling real applications, and the guard is in the service rather than only the route.
- In-app labeling of demo data as synthetic. ✅ The seeded job is titled `[Demo] …` and the job page carries a "Synthetic demo data" tag.

**Verification criteria.**
- With no API key set, the seed command runs and the full workflow completes. ✅ Verified in a process with `ANTHROPIC_API_KEY` removed from the environment: 3 uploaded, 2 screened, 1 failed, ranking 82 then 15.
- Two consecutive seeded runs produce identical scores and identical ranking. ✅ Guaranteed by construction — no model call, fixture replay, and a total ordering — and asserted in the ranking suite.
- The sample set includes the high-score / missing-must-have case required by the specification. ✅ Promoting the unevidenced Kubernetes requirement to must-have caps the band at Review while the score stays 82; covered by tests at the service, API and UI layers.
- The adversarial CV is visibly flagged in the UI. ✅ A warning on the ranked row and a callout on the candidate page, both stating that the text cannot be used as evidence.
- The image-only CV shows an honest parse failure rather than a zero score. ✅ It appears under "Could not be processed" with the reason, never in the ranking.
- No real personal data exists anywhere in `data/`. ✅ Every name, employer and institution is invented, and the only email domain is the reserved `example.invalid`.

**Not done in this phase, deliberately:** the sample set is three CVs, not the
seven-case set Phase 13's evaluation work will need. Broadening it means
recording new fixtures, which belongs with the evaluation harness that will
measure against them.

---

## Phase 13 — Evaluation and benchmark 🚧

*Delivered as the **Evaluation + Hardening** milestone.*

**Objective.** Measure the pipeline instead of asserting that it works.

**Deliverables.**
- `evaluation/` — labeled gold dataset, runner script, and results. ✅ Eight synthetic candidates across two jobs, 89 labelled (candidate, requirement) pairs, `python -m evaluation.runner`.
- Metrics from `product-spec.md` section 16. 🚧 Eleven are measured; six are reported as **not measurable in this configuration**, each with its reason. See below.
- `evaluation/RESULTS.md` — measured numbers with sample sizes and the reproduction command. ✅ Generated by the runner, so it cannot drift from the code.

**Verification criteria.**
- The evaluation runs end to end and emits a results file. ✅ `results.json` and `RESULTS.md`.
- Every reported number carries its denominator. ✅ Every metric records numerator, denominator, definition, what it actually measures, whether it is deterministic or LLM-dependent, and its limitations.
- Results are reproducible: a second run on the same fixtures gives the same numbers. ✅
- Weak results are reported as they are. No metric is dropped because it is unflattering. ✅ The harness found two real defects on its first run and both are documented in `RESULTS.md`, including the recall the fixes cost.
- The counterfactual probe is labeled as a sensitivity test, not a bias audit. ✅ And explicitly as a test of *deterministic* code only — model sensitivity to a name is one of the five unmeasurable metrics.

**Why five metrics are not measured.** A replay fixture is keyed by a hash of
its rendered input, so any CV or job description outside the recorded set has no
recording. Producing one means hand-writing the model's answer — and the same
person would then be writing both the answer and the label it is scored against.
Extraction precision/recall, semantic verdict agreement, run-to-run stability,
counterfactual *model* sensitivity, ranking correlation against a human reference
and cost per CV therefore need a live provider. They are listed with that reason rather than filled in with a number
that would measure nothing. This is the boundary the whole project is built on
(ADR-0001): the deterministic half is measurable now, the model half is not.

**What this milestone also delivered, beyond the phase as written.** Hardening
that belongs with evaluation because the evaluation is what motivated it: the
job description is now treated as untrusted text the way a CV already was
(scanned for instruction-like passages, flagged and surfaced to the recruiter,
never removed and never obeyed), bounds and control-character handling on pasted
description input, mode-boundary tests, an adversarial model-output corpus at
every call site, and the matching-failure lifecycle. 42 tests in
`backend/tests/test_hardening.py`.

**What remains for this phase:** the six LLM-dependent metrics. Each needs one
run against a configured provider; none needs more code here. Running them is
the natural companion to enabling Live AI Mode, which has not been done yet.

---

## Phase 14 — Automated testing 🚧

*Delivered as part of the **Final completion** milestone.*

**Objective.** Bring the suite to the level where a regression is caught by tests rather than by a demo failing.

**Deliverables.**
- Unit tests for parsing, normalization, evidence verification, deterministic matching, scoring, band mapping, and ranking. ✅
- Integration tests for the API endpoints against a test database. ✅
- One end-to-end test covering criteria → requirements → upload → score → ranking in demo mode. ✅ One per sample brief, in fact: `test_demo.py::test_every_sample_brief_can_be_walked_end_to_end`.
- Security regression tests: injection set, path traversal, oversized upload, XSS payload in CV text. ✅ `test_hardening.py` (43), `test_limits.py` (14), `test_cv_prompt_injection.py`, `test_prompt_injection.py`, `test_storage.py`, plus an ESLint rule that makes `dangerouslySetInnerHTML` a build failure.
- Coverage reporting. ✅ `.	asks.ps1 coverage` — 97% of `backend/app` by statement.
- CI running the full suite. 🚧 The workflow runs it; it has still never been observed on GitHub, because the repository has not been pushed.

**Verification criteria.**
- The entire suite runs and passes, with the output shown. ✅ 662 backend, 93 frontend.
- The suite runs offline with no API key. ✅
- Coverage is measured and reported honestly, including any weak areas. ✅ The weakest module is named rather than averaged away: `app/llm/client.py` at 78%, all of it inside `LiveLlmClient`, which cannot run offline.
- Every test asserts a specific behaviour; no test passes trivially. ✅
- CI is green on the current commit. ⬜ Unobservable until the first push.

**What remains for this phase:** watching CI actually run. That is the same
outstanding item Phase 3 carries, and it needs a push, not more code.

---

## Phase 15 — Security and reliability review ✅

*Delivered as part of the **Final completion** milestone.*

**Objective.** Deliberately attack the system, then fix what the attack finds.

**Deliverables.**
- A written review covering the three-channel trust boundary, upload validation, secret handling, dependency vulnerabilities, error-message leakage, and CORS. ✅ [`docs/security.md`](security.md), 15 sections.
- Adversarial testing against the injection corpus. ✅ An injected CV is screened by the demo on every run; a quote that verifies but reads as an instruction is refused separately, because verification alone cannot catch it.
- Rate limiting and request size limits. ✅ `app/api/limits.py`, with both limits' ceilings documented and one of them pinned by a test named after the limitation it records.
- Structured logging that never logs secrets or full CV content. ✅ Verified by running a full screening at DEBUG and asserting the candidate's name, email, phone, address and CV text are absent from every record.
- Graceful degradation when the LLM provider is unavailable. ✅ 503/502 with a plain-language message, nothing partial written, and the candidate recorded as FAILED with a reason rather than vanishing.
- Fixes for every issue found. ✅

**Verification criteria.**
- Each control is tested by attempting to break it, and the attempt is shown to fail. ✅
- A dependency vulnerability scan runs, and its findings are triaged in writing. ✅ `pip-audit` + `npm audit` via `.	asks.ps1 audit`. The first run found 8 advisories across 2 development-only packages; both were fixed, and the triage is in [`docs/security.md` §13](security.md#13-dependency-vulnerabilities). Both scans now report nothing.
- No secret appears in logs; verified by inspecting real log output. ✅
- The API returns a sane error, not a stack trace, when the provider is unreachable — verified by simulating the failure. ✅ And by an unsimulated one: `scripts/check_llm.py` against a placeholder key reports `provider returned HTTP 401` without printing the key.
- Findings are recorded even where they are accepted rather than fixed, with the reason. ✅ Six of them, including the two that matter most — no authentication at all, and a rate limiter that is a brake rather than a wall.

---

## Phase 16 — UI/UX polish ✅

*Delivered as part of the **Final completion** milestone.*

**Objective.** Make the interface clear enough that the evidence, not the score, is what a recruiter reads first.

**Deliverables.**
- Visual hierarchy that puts evidence above the number. ✅ The score badge stays in the candidate header for orientation; the full arithmetic moved *below* the verdicts, because a reader who meets the number first reads the evidence as a justification for it rather than as the thing it came from.
- Consistent verdict styling, with a text label on every state rather than colour alone. ✅
- Responsive layout. ✅ All three routes checked at 375px: no horizontal page scroll, and the requirement table scrolls inside its own container rather than pushing the page wide.
- Keyboard accessibility, focus states, and sufficient contrast. ✅ See below — this found two real defects.
- Copy review across the app for evidence-first wording and heuristic labeling. ✅ Including one removal: an ordinary `NO_EVIDENCE` was printing the same evidence-first sentence twice, which is how a reader learns to skip it.
- Empty, loading, and error states finished. ✅

**Verification criteria.**
- Every workflow screen is reviewed in the browser at desktop and narrow widths. ✅ Against a real server, not a mock.
- Verdict states are distinguishable without colour. ✅ Every verdict, band and flag carries a word.
- Keyboard-only navigation reaches every interactive control. ✅ Verified by enumerating the live accessibility tree: 15 controls, all with `tabIndex` 0, none hidden, all with an accessible name.
- Contrast meets WCAG AA on text; checked, not assumed. ✅ `python scripts/check_contrast.py` reads the palette out of the stylesheet and checks 34 pairs across both themes.
- No UI string implies a candidate lacks a skill where the system only lacks evidence. ✅

**Two defects this phase found, both only visible by looking:**

- **The must-have checkboxes announced as "on".** They were named by a
  visually-hidden span inside a wrapping `<label>`, which jsdom resolves and the
  test suite was happy with — but Chrome's accessibility tree did not. Now an
  explicit `aria-label`, matching the weight input beside it.
- **Control borders failed WCAG 1.4.11 at 1.63:1.** `--border-strong` is the
  border of every input, textarea, select and quiet button, and non-text
  contrast wants 3:1. Darkened in light mode and lightened in dark until both
  pass, and `scripts/check_contrast.py` exists so it cannot silently regress.

A third, smaller one: a ranked row's accessible name was every text node in the
card run together — "must-have coverage 50%38", with the score fused to the
percentage. It now has a written-out label.

---

## Phase 17 — Documentation 🚧

*Delivered as part of the **Final completion** milestone.*

**Objective.** Make the project understandable to a reader who has three minutes and no context.

**Deliverables.**
- README rewritten for a working system. ✅ Opens with what a recruiter types and what comes back, then the product, the boundary, the two modes, and everything a reader might want to check. No screenshots — see below.
- `docs/architecture.md` updated to describe what was actually built. ✅ Including §5.1, the job description as an untrusted channel.
- API documentation. ✅ Generated from the code at `/docs`, with a prose description on every route — which is the version that cannot drift from the implementation.
- `docs/development.md`, evaluation results, `docs/security.md`, and a limitations page. ✅ Plus [`docs/deployment.md`](deployment.md). Limitations live in the README and in product-spec §17 rather than in a page of their own, so a reader meets them without going looking.
- Decision records for the significant choices. ✅ Ten ADRs; two written this milestone.

**Verification criteria.**
- Every command in the README is executed from a clean checkout and observed to work. 🚧 Every command was executed, and the Docker build was run from a clean context — but not from a fresh `git clone`, which is a step for the repository owner after the first push.
- No documented feature is absent from the code, and no significant feature is undocumented. ✅
- Screenshots reflect the current UI. ⬜ There are none. A screenshot in a README goes stale silently and this UI has changed under every milestone; the demo runs locally in two commands with no API key instead. Adding them is a good idea *after* the UI stops moving.
- Limitations and evaluation results are linked from the README, not buried. ✅ Both have their own top-level section and appear in the table of contents.

---

## Phase 18 — GitHub repository cleanup 🚧

*Delivered as part of the **Final completion** milestone.*

**Objective.** Make the repository itself part of the portfolio.

**Deliverables.**
- History reviewed for accidentally committed secrets or data. ✅ `git log --all --full-history -- .env` returns nothing; a pattern scan over the tracked tree finds only the `.env.example` placeholder and a `sk-ant-not-a-real-key` literal in two test files.
- Dead code, unused dependencies, and stale TODOs removed. ✅ No `TODO`, `FIXME`, `XXX` or `HACK` anywhere in `backend/app`, `frontend/src`, `evaluation` or `scripts`. One genuinely unused devDependency found and removed (`eslint-plugin-react-refresh`, referenced by no config). Runtime dependencies are two: React and ReactDOM.
- Consistent formatting and linting across the codebase. ✅ Ruff now covers `scripts/` and `evaluation/` as well as `backend/`, which is how four pre-existing lint errors in `scripts/check_docs.py` were found and fixed.
- License, description, topics, and a clean issue/PR template set. 🚧 MIT licence and templates are in the repository; the GitHub-side description and topics can only be set after the first push.
- Final `.gitignore` review. ✅ `.env`, `var/`, `coverage.json` and the build outputs are all covered; a `.dockerignore` now keeps `.env` and uploaded CVs out of the image context too.

**Verification criteria.**
- A secret scan over the full history reports nothing. 🚧 A pattern scan is clean, and nothing that needed removing was ever committed. Running a dedicated history scanner (`gitleaks`, `trufflehog`) is listed in the README as a pre-publication step for the repository owner.
- A fresh clone builds, tests, and runs using only the README. 🚧 Every command in the README was executed, and the Docker image builds from a clean context — but not from an actual fresh `git clone`, which needs the push.
- No real candidate data exists in any commit. ✅ Every CV, brief and profile in the repository is invented.
- Lint and format checks pass across the whole repository. ✅
- The repository root is legible: no stray scratch files. ✅

---

## Phase 19 — Deployment 🚧

**Objective.** Put a working demo online.

**Nothing is deployed.** There is no public URL and no hosting account. What
this milestone delivered is everything a deployment needs *before* one exists,
written after building and running the image rather than from memory:
[`docs/deployment.md`](deployment.md) and `backend/Dockerfile`.

**Deliverables.**
- Backend deployed with a managed PostgreSQL instance. ⬜ The image exists, builds from the lock file, runs as a non-root user, carries a health check, and was verified locally answering `/health`, `/health/db` and `/api/demo/samples`.
- Frontend deployed and pointed at the backend. ⬜ `npm run build` produces a static bundle; `VITE_API_BASE_URL` is baked in at build time, which is documented because it surprises people.
- Environment and secret configuration handled by the platform, never committed. ✅ Documented; `.dockerignore` keeps `.env` out of the build context.
- Demo mode enabled in production so the public demo costs nothing and stays deterministic. ✅ Documented as the default and the recommendation, with the reason: on a public URL with no authentication, live mode is an invitation to spend the operator's money.
- Production migrations and seeding. ✅ Documented as a separate step — the image deliberately does *not* run `alembic upgrade` at start-up, because applying schema changes from every replica is a race.
- Basic uptime and error monitoring. ⬜ Not wired to any vendor. The two health endpoints exist and are documented for liveness and readiness respectively.

**Verification criteria.**
- The public URL loads and the full workflow completes. ⬜ No URL exists.
- The deployed app runs on demo fixtures; no live key is exposed to the browser. ✅ True of the design: the key is server-side only and the frontend has exactly one variable, which is an API base URL.
- CORS restricts the API to the deployed frontend origin. ✅ An explicit allowlist, never `*`, with the deployment note saying so.
- A cold start is measured and reported honestly. ⬜ Not measured. Reporting one would mean inventing it.
- Rolling back to the previous deployment is possible and documented. 🚧 Documented, including the two caveats that belong to this codebase rather than to a platform: migrations are forward-only in practice, and a stored score names the config version it was computed under so a rollback cannot silently reinterpret old rows.

**What remains for this phase:** an actual deployment, which is a decision for
the repository owner and not something this milestone should make for them.

---

## Phase 20 — Final end-to-end verification 🚧

*Delivered as part of the **Final completion** milestone, minus the parts that need a deployment.*

**Objective.** Verify the whole system against this roadmap and the specification, and state plainly what is and is not true of it.

**Deliverables.**
- Full workflow executed and recorded. ✅ Locally, against a real uvicorn server and a real browser, not a mock: all four sample briefs seeded to a ranked list, the Indonesian one walked screen by screen, and the protected-attribute guard driven to its refusal through the UI.
- Every phase's verification criteria re-checked against the final build. ✅ Every phase above now carries a per-criterion mark, including the ones that are still ⬜.
- Final evaluation run with published results. ✅ A fresh run reproduces `evaluation/RESULTS.md` byte for byte.
- A closing summary: what was built, what was measured, what is limited, what would come next. ✅ [`README.md`](../README.md), whose "Current status" and "Known limitations" sections are that summary.

**Verification criteria.**
- The complete workflow succeeds on the deployed URL. ⬜ No deployment exists. It succeeds locally.
- Every claim in the README is checked against observed behaviour, and any that no longer holds is corrected. ✅ Two corrections came out of it: the security review's claim that uploads check content type (they check magic bytes; the filename and declared type are untrusted labels), and the "Live AI mode works" framing, which is now stated as implemented-and-structurally-verified rather than measured.
- The full test suite passes on the final commit. ✅
- Published evaluation numbers match a fresh run. ✅
- Every unimplemented item is listed as unimplemented, in the README rather than only here. ✅

**What remains for this phase:** the deployed half, which is Phase 19's
remaining half too. Both need a hosting decision that belongs to the repository
owner.
