# ADR-0007: FastAPI `BackgroundTasks` instead of Celery/Redis

**Status:** Accepted (Phase 1)

## Context

A CV upload triggers a per-candidate pipeline (parse → extract profile → match → score) that can take from seconds to tens of seconds per document once LLM calls are involved, and a batch can contain up to `MAX_FILES_PER_BATCH` (25) documents. Handling this fully synchronously inside the upload request would mean an HTTP request that runs for minutes, times out, and gives the UI nothing to show in the meantime.

The standard production answer to "long-running work triggered by a web request" is a durable task queue — Celery or RQ backed by Redis. That is real infrastructure: a broker to run and monitor, a separate worker process to deploy, and a second system to keep compatible across environments (local dev, CI, deployment).

## Decision

Use FastAPI's built-in `BackgroundTasks`, with concurrency across candidates bounded by an `asyncio.Semaphore` (so a 25-file batch does not open 25 simultaneous LLM provider connections and trip rate limits). The upload endpoint returns immediately once files are validated and stored; the frontend polls candidate status.

This choice comes with an explicitly acknowledged cost, not a hidden one: background tasks run in-process. A server restart mid-batch loses in-flight work, leaving affected candidates stuck in a transient status (`PARSING`, `EXTRACTING`, `SCORING`). This is mitigated, not eliminated:

- `candidate.stage_started_at` records when the current stage began, so a candidate stuck past a reasonable timeout is *detectable* rather than silently pending forever.
- A retry endpoint (`POST /api/candidates/{id}/retry`, planned for the phase that implements it) re-runs the pipeline from the last completed stage.
- Every stage is idempotent and cached by content hash (`LlmCallLog`, keyed by `input_sha256`), so a retry does not re-pay for LLM work already successfully completed.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Celery / RQ + Redis | Real durability against process restarts, but adds a broker, a worker process, and a second deployed component to build, test, and operate — for a workload of tens of documents per batch. The operational cost is not repaid at this project's actual scale. |
| Fully synchronous request handling | An HTTP request lasting minutes will hit client and proxy timeouts, and the UI has no way to show incremental progress during it. |
| A Postgres-backed job queue (e.g., a `job_queue` table with polling) | Cheaper than adding Redis, and a genuinely reasonable middle ground — but still requires building and testing a scheduler and a poller. The candidate status model (transient states + `stage_started_at` + a retry endpoint) is designed so this remains a straightforward drop-in replacement if and when the in-process limitation becomes a real problem, rather than something that has to be retrofitted. |

## Consequences

**Benefits:** no additional infrastructure to run locally, in CI, or in the initial deployment; the entire pipeline can be exercised in tests and in the demo with nothing beyond the application process and PostgreSQL itself.

**Costs:** the restart-loses-work limitation is real and is documented in [`docs/product-spec.md` §17](../product-spec.md#17-major-limitations) rather than discovered later during deployment hardening. This is not suitable for a production deployment handling real, unrecoverable candidate submissions without the queue upgrade described above — a decision explicitly deferred, not forgotten.

**See also:** [`docs/architecture.md` §7](../architecture.md#7-processing-model), [`docs/data-model.md` §4.4](../data-model.md#44-candidate) (`candidate_status`, `stage_started_at`).
