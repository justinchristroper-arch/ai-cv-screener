# ADR-0006: Synchronous SQLAlchemy over async

**Status:** Accepted (Phase 1–2)

## Context

FastAPI supports both synchronous and asynchronous request handlers and database access. Async SQLAlchemy (with an async driver such as `asyncpg` or `psycopg`'s async mode, async sessions, and `async def` endpoints throughout) is the more commonly recommended default for a new FastAPI project, on the premise that it improves throughput under concurrent load.

This project's actual latency profile is dominated by outbound LLM API calls (hundreds of milliseconds to seconds each, per document, per pipeline stage) — not by database round-trips, which are local, indexed, single-row-or-small-batch operations. FastAPI already runs synchronous endpoint functions in a threadpool, so a synchronous database layer does not block the event loop for other requests.

## Decision

Use synchronous SQLAlchemy (`Engine`, `Session`, `sessionmaker`) with the `psycopg` (v3) driver in its synchronous mode, and plain `def` route handlers for anything that touches the database directly.

Concurrency for the part of the system that actually needs it — issuing multiple LLM calls without serializing a whole batch — is handled separately, at the LLM client layer, with an `asyncio.Semaphore`-bounded worker (see [ADR-0007](0007-backgroundtasks-not-celery.md)), not by making the ORM async.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Async SQLAlchemy + async driver throughout | Adds an async driver dependency, async session lifecycle management, and async-aware test fixtures (`pytest-asyncio` or equivalent) — real complexity — to buy throughput headroom this workload's database access pattern does not need. The bottleneck is the LLM, not Postgres. |
| Async only for the LLM-calling routes, sync for the rest | Mixing async and sync database access in the same codebase is a known source of subtle bugs (a sync call inside an async context blocking the event loop) for a benefit that, per the above, does not apply here. |

## Consequences

**Benefits:** simpler test setup (no async fixtures, no event-loop management in `pytest`), simpler mental model (one calling convention for all database code), and no risk of accidentally blocking the event loop with a sync call made from async code.

**Costs:** if a future phase's actual load testing shows the database genuinely becomes a throughput bottleneck (not currently expected, and not measured, since no load testing exists yet — see `docs/product-spec.md` §17), migrating to async SQLAlchemy later is a real, non-trivial rewrite of the data-access layer, not a flag flip. This is an accepted, revisitable trade, not a permanent architectural commitment.

**See also:** [`backend/app/db/session.py`](../../backend/app/db/session.py), [`docs/architecture.md` §7](../architecture.md#7-processing-model).
