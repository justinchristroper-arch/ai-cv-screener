# AI CV Screener — Development Roadmap

**Status:** Phases 0–7 complete, Phase 8 partially delivered (Phase 3's CI-workflow-observed-passing criterion is still pending the first push to GitHub — see the note under Phase 3). Phases 9–20 not started.
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
| 9 | Transparent scoring engine | ⬜ |
| 10 | Candidate ranking | ⬜ |
| 11 | Frontend application | ⬜ |
| 12 | Demo mode with synthetic candidates | ⬜ |
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
- The `LlmClient` abstraction (`LiveLlmClient` / `ReplayLlmClient`) with the two no-fallback rules from [architecture §4.2](architecture.md#42-the-client-abstraction-and-demo-mode), and six recorded fixtures covering the happy path, a recoverable retry, an unrecoverable double failure, and a prompt-injection attempt.
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
- Cost and latency instrumentation. ⬜ `llm_call_log` records tokens and latency per call, but nothing aggregates them into a cost-per-CV figure.

**Verification criteria.**
- Semantic equivalences the deterministic matcher misses are recognized on the sample set. 🚧 Demonstrated on the bundled CV, where a Postgres-backed embedding search returns `PARTIAL` against a vector-database requirement. Not measured over a labelled set — that is Phase 13.
- The downgrade rule fires on a fixture whose evidence does not exist in the source; covered by a test. ✅
- No LLM response is ever interpreted as an instruction; the injection test set produces no verdict change. ✅
- Cost per CV is measured and reported, not estimated. ⬜ Needs the Phase 13 harness.
- The stage is fully replayable from fixtures with no API key present. ✅

**What remains for this phase:** the two measurement criteria above. They are
deferred rather than skipped, and are picked up in Phase 13.

---

## Phase 9 — Transparent scoring engine ⬜

**Objective.** Turn verdicts into a score that a recruiter can verify by hand.

**Deliverables.**
- Pure scoring function implementing the weighted formula from the specification.
- Must-have coverage computed and stored separately from the score.
- Recommendation band mapping with configurable thresholds.
- The must-have guard, if confirmed in Phase 1.
- A per-requirement score-breakdown structure for the UI.
- Defined behaviour for edge cases: no requirements, all weights zero, single requirement.

**Verification criteria.**
- Unit tests assert exact expected scores for hand-computed cases.
- Recomputing a score from stored rows reproduces the stored value exactly.
- Scoring is a pure function: no I/O, no network, no clock, no randomness — enforced by the tests running fully offline.
- The band boundaries are tested at their exact edges (59/60, 74/75, 89/90).
- Division-by-zero and empty-requirement cases are handled explicitly, not by exception.
- The breakdown sums to the total; asserted by a property test over generated inputs.

---

## Phase 10 — Candidate ranking ⬜

**Objective.** Order candidates within a job, stably and explicably.

**Deliverables.**
- Ranking service ordering by score, with a documented deterministic tie-break.
- Candidates in a failed state are surfaced separately, never silently dropped.
- Ranked-list endpoint returning score, band, must-have coverage, and warning flags.

**Verification criteria.**
- Ranking is deterministic across repeated runs on identical input, including ties.
- A failed candidate appears in the response with its failure reason rather than disappearing.
- No candidate is filtered out by score at any point in the API; asserted by a test with a very low-scoring candidate.
- Ordering is tested against a hand-constructed expected order.

---

## Phase 11 — Frontend application ⬜

**Objective.** Build the recruiter-facing interface for the full workflow.

**Deliverables.**
- Job list and job creation.
- JD entry, extraction trigger, requirement review and edit table, and the confirmation gate.
- Batch CV upload with per-file progress and status.
- Ranked candidate list.
- Candidate detail: matched / partial / no-evidence groups, evidence quotes with source location, score breakdown, must-have coverage, warnings.
- Error, empty, and loading states throughout.
- Evidence-first wording enforced in the UI copy.

**Verification criteria.**
- The complete workflow is exercised in the browser end to end and observed to work.
- The requirement confirmation gate cannot be bypassed from the UI.
- All CV-derived text renders escaped; a candidate whose CV contains HTML or script markup renders it inertly — verified against a crafted sample.
- Low-scoring candidates are visible and openable.
- Heuristic thresholds are labeled as heuristic where scores are shown.
- The frontend builds with no errors and no type errors.

---

## Phase 12 — Demo mode with synthetic candidates ⬜

**Objective.** Make the project runnable and convincing without an API key, real data, or cost.

**Deliverables.**
- `data/sample/` — synthetic JDs and CVs covering strong, borderline, weak, off-target, adversarial (injection), badly formatted, and image-only cases.
- Recorded LLM response fixtures covering the sample set.
- A demo flag that routes all LLM calls to fixtures.
- A seed command producing a ready-to-browse demo state.
- In-app labeling of demo data as synthetic.

**Verification criteria.**
- With no API key set, the seed command runs and the full workflow completes.
- Two consecutive seeded runs produce identical scores and identical ranking.
- The sample set includes the high-score / missing-must-have case required by the specification.
- The adversarial CV is visibly flagged in the UI.
- The image-only CV shows an honest parse failure rather than a zero score presented as a judgement.
- No real personal data exists anywhere in `data/`.

---

## Phase 13 — Evaluation and benchmark ⬜

**Objective.** Measure the pipeline instead of asserting that it works.

**Deliverables.**
- `evaluation/` — labeled gold dataset, runner script, and results.
- Metrics from `product-spec.md` section 16: extraction precision/recall, verdict agreement with a confusion matrix, evidence validity rate, ranking correlation, injection resistance, run-to-run stability, counterfactual name probe, cost and latency.
- `evaluation/RESULTS.md` — measured numbers with sample sizes and the reproduction command.

**Verification criteria.**
- The evaluation runs end to end and emits a results file.
- Every reported number carries its denominator.
- Results are reproducible: a second run on the same fixtures gives the same numbers.
- Weak results are reported as they are. No metric is dropped because it is unflattering.
- The counterfactual probe is labeled as a sensitivity test, not a bias audit.

---

## Phase 14 — Automated testing ⬜

**Objective.** Bring the suite to the level where a regression is caught by tests rather than by a demo failing.

**Deliverables.**
- Unit tests for parsing, normalization, evidence verification, deterministic matching, scoring, band mapping, and ranking.
- Integration tests for the API endpoints against a test database.
- One end-to-end test covering JD → requirements → upload → score → ranking in demo mode.
- Security regression tests: injection set, path traversal, oversized upload, XSS payload in CV text.
- Coverage reporting.
- CI running the full suite.

**Verification criteria.**
- The entire suite runs and passes, with the output shown.
- The suite runs offline with no API key.
- Coverage is measured and reported honestly, including any weak areas.
- Every test asserts a specific behaviour; no test passes trivially.
- CI is green on the current commit.

---

## Phase 15 — Security and reliability review ⬜

**Objective.** Deliberately attack the system, then fix what the attack finds.

**Deliverables.**
- A written review covering the three-channel trust boundary, upload validation, secret handling, dependency vulnerabilities, error-message leakage, and CORS.
- Adversarial testing against the injection corpus.
- Rate limiting and request size limits.
- Structured logging that never logs secrets or full CV content.
- Graceful degradation when the LLM provider is unavailable.
- Fixes for every issue found.

**Verification criteria.**
- Each control is tested by attempting to break it, and the attempt is shown to fail.
- A dependency vulnerability scan runs, and its findings are triaged in writing.
- No secret appears in logs; verified by inspecting real log output.
- The API returns a sane error, not a stack trace, when the provider is unreachable — verified by simulating the failure.
- Findings are recorded even where they are accepted rather than fixed, with the reason.

---

## Phase 16 — UI/UX polish ⬜

**Objective.** Make the interface clear enough that the evidence, not the score, is what a recruiter reads first.

**Deliverables.**
- Visual hierarchy that puts evidence above the number.
- Consistent verdict styling, with a text label on every state rather than colour alone.
- Responsive layout.
- Keyboard accessibility, focus states, and sufficient contrast.
- Copy review across the app for evidence-first wording and heuristic labeling.
- Empty, loading, and error states finished.

**Verification criteria.**
- Every workflow screen is reviewed in the browser at desktop and narrow widths.
- Verdict states are distinguishable without colour.
- Keyboard-only navigation reaches every interactive control.
- Contrast meets WCAG AA on text; checked, not assumed.
- No UI string implies a candidate lacks a skill where the system only lacks evidence.

---

## Phase 17 — Documentation ⬜

**Objective.** Make the project understandable to a reader who has three minutes and no context.

**Deliverables.**
- README rewritten for a working system: what it does, screenshots or a recording, quickstart, demo link.
- `docs/architecture.md` updated to describe what was actually built.
- API documentation.
- `docs/development.md`, `docs/evaluation.md`, `docs/security.md`, and a limitations page.
- Decision records for the significant choices.

**Verification criteria.**
- Every command in the README is executed from a clean checkout and observed to work.
- No documented feature is absent from the code, and no significant feature is undocumented.
- Screenshots reflect the current UI.
- Limitations and evaluation results are linked from the README, not buried.

---

## Phase 18 — GitHub repository cleanup ⬜

**Objective.** Make the repository itself part of the portfolio.

**Deliverables.**
- History reviewed for accidentally committed secrets or data.
- Dead code, unused dependencies, and stale TODOs removed.
- Consistent formatting and linting across the codebase.
- License, description, topics, and a clean issue/PR template set.
- Final `.gitignore` review.

**Verification criteria.**
- A secret scan over the full history reports nothing.
- A fresh clone builds, tests, and runs using only the README.
- No real candidate data exists in any commit.
- Lint and format checks pass across the whole repository.
- The repository root is legible: no stray scratch files.

---

## Phase 19 — Deployment ⬜

**Objective.** Put a working demo online.

**Deliverables.**
- Backend deployed with a managed PostgreSQL instance.
- Frontend deployed and pointed at the backend.
- Environment and secret configuration handled by the platform, never committed.
- Demo mode enabled in production so the public demo costs nothing and stays deterministic.
- Production migrations and seeding.
- Basic uptime and error monitoring.

**Verification criteria.**
- The public URL loads and the full workflow completes in the deployed environment.
- The deployed app runs on demo fixtures; no live key is exposed to the browser.
- CORS restricts the API to the deployed frontend origin.
- A cold start is measured and reported honestly.
- Rolling back to the previous deployment is possible and documented.

---

## Phase 20 — Final end-to-end verification ⬜

**Objective.** Verify the whole system against this roadmap and the specification, and state plainly what is and is not true of it.

**Deliverables.**
- Full workflow executed on the deployed demo and recorded.
- Every phase's verification criteria re-checked against the final build.
- Final evaluation run with published results.
- A closing summary: what was built, what was measured, what is limited, what would come next.

**Verification criteria.**
- The complete workflow succeeds on the deployed URL.
- Every claim in the README is checked against observed behaviour, and any that no longer holds is corrected.
- The full test suite passes on the final commit.
- Published evaluation numbers match a fresh run.
- Every unimplemented item is listed as unimplemented, in the README rather than only here.
