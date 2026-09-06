# ADR-0005: pgvector deferred from the MVP

**Status:** Accepted (Phase 1)

## Context

The original stack brief listed PostgreSQL with pgvector as a planned dependency, on the assumption that semantic skill matching would need embedding similarity search. By the time the matching pipeline was actually designed (Phase 1), it became clear the MVP's matching strategy does not need it: exact and alias-based matching (`postgres` ↔ `postgresql`, `js` ↔ `javascript`) handles the common, easy cases deterministically, and the LLM's own semantic judgment — already required for evidence extraction and verdict reasoning — handles the harder cases that alias matching cannot (see [ADR-0001](0001-ai-deterministic-boundary.md)). Nothing in that pipeline calls for a vector similarity search.

## Decision

Do not add pgvector, an embedding model dependency, a vector index, or a similarity threshold in the MVP. Use plain PostgreSQL (`postgres:16-alpine`).

The schema is deliberately arranged so this is reversible without disruption: adding an `embedding vector(N)` column and index to `profile_skill` or `requirement` later would be a purely additive migration touching no existing column or query. Because `match_result.decided_by` already records which mechanism (exact, alias, LLM semantic) decided each pair, a future embedding-based pre-filter would appear as a new value in that enum, and its effect on cost, latency, and verdict agreement would be *measurable* against the Phase 13 evaluation baseline — rather than adopted on the assumption that it helps.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Include pgvector now, as originally planned | Adds a Postgres extension, an embedding-model dependency, an index, and an unvalidated similarity threshold to tune — for a capability nothing in the current pipeline design calls for. No evaluation set exists until Phase 13 to demonstrate it improves anything. |
| Include it now "for later," unused | Dead weight: an unused extension and dependency in every environment (local dev, CI, deployment) that has to be explained, maintained, and kept compatible, with no code exercising it. |

## Consequences

**Benefits:** one fewer moving part in local setup, CI, and deployment; no embedding-model cost or latency in the MVP; the deferral is cheap to reverse because the schema was designed for it, and reversing it would be backed by measurement rather than assumption once Phase 13's evaluation harness exists.

**Costs:** if a future need for semantic skill search or cross-job candidate matching (both explicitly out of MVP scope, see `docs/product-spec.md` §5, §18) materializes, it requires a follow-up migration and a new dependency — a known, accepted, and clearly cheaper-than-guessing-wrong cost.

**See also:** [`docs/data-model.md` §10](../data-model.md#10-pgvector-the-deferred-path), [`docs/architecture.md` §12 (decision log)](../architecture.md#12-decision-log), `backend/tests/test_database.py::test_pgvector_extension_is_not_installed`.
