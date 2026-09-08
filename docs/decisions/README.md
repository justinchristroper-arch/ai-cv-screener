# Architecture Decision Records

This directory records the significant architectural decisions made for the AI CV Screener, in a lightweight ADR format. It exists so that a decision can be reviewed, questioned, or revisited without re-deriving the reasoning from scratch or from memory.

## What belongs here

A decision earns an ADR when it is **significant** (hard to reverse, or shapes how multiple later phases are built), **non-obvious** (a reasonable engineer could have chosen differently), or **already the subject of explicit reasoning** in [`docs/architecture.md`](../architecture.md), [`docs/data-model.md`](../data-model.md), or [`docs/product-spec.md`](../product-spec.md). ADRs here summarize and cite that reasoning — they do not introduce new justification the source documents don't already contain.

Routine implementation choices (a variable name, a function signature) do not need an ADR.

## Format

Each ADR uses the same five sections:

- **Status** — `Proposed`, `Accepted`, `Superseded by ADR-000X`, or `Deprecated`.
- **Context** — the problem or constraint that forced a choice.
- **Decision** — what was chosen, stated plainly.
- **Alternatives considered** — what else was on the table, and why it lost.
- **Consequences** — what the decision costs, not only what it buys. A decision with no acknowledged cost has not been examined carefully enough.

## Numbering

ADRs are numbered sequentially (`0001`, `0002`, …) in the order they were written, not in the order the decisions were made. A number is never reused, even if the ADR it belonged to is later superseded — the superseding ADR gets a new number and says so explicitly.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-ai-deterministic-boundary.md) | AI vs. deterministic responsibility boundary | Accepted |
| [0002](0002-evidence-first-evaluation.md) | Evidence-first evaluation | Accepted |
| [0003](0003-sensitive-attribute-exclusion.md) | Sensitive-attribute exclusion from scoring | Accepted |
| [0004](0004-human-confirmation-gate.md) | Human confirmation gate for extracted requirements | Accepted |
| [0005](0005-pgvector-deferred.md) | pgvector deferred from the MVP | Accepted |
| [0006](0006-synchronous-sqlalchemy.md) | Synchronous SQLAlchemy over async | Accepted |
| [0007](0007-backgroundtasks-not-celery.md) | FastAPI `BackgroundTasks` instead of Celery/Redis | Accepted |
| [0008](0008-deterministic-scoring.md) | Deterministic scoring rather than LLM-generated scores | Accepted |
| [0009](0009-natural-language-screening-criteria.md) | Screening criteria are free text, not a job description | Accepted |
| [0010](0010-protected-attribute-guard.md) | A requirement naming a protected characteristic cannot be confirmed | Accepted |

## Proposing a change

To propose changing a decision recorded here, open an issue or pull request describing the new context and the proposed decision (see [`CONTRIBUTING.md`](../../CONTRIBUTING.md#proposing-architectural-changes)). Do not silently edit an `Accepted` ADR to reflect a different decision — write a new ADR that supersedes it, and update the superseded one's status and this index.
