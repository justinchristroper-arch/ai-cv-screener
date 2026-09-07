"""Derived rows must never outlive the inputs they were derived from.

docs/data-model.md section 7 states exactly which change invalidates what:

===============================  ==========================================
Change                            Invalidates
===============================  ==========================================
Requirement `text` or `category`  `match_result` for it, then every `score`
Requirement added or deleted      the `match_result` set, then every `score`
Requirement `weight`/`must_have`  **`score` only** -- verdicts do not depend
                                  on weight, so re-weighting is free
Matching re-run for a candidate   that candidate's `score`
Job unconfirmed                   `match_result` and `score` for the job
===============================  ==========================================

This module exists as its own file for a boring but real reason: `requirements`
and `matching` both have to invalidate scores, and `scoring` has to read
confirmed requirements. Putting the invalidation helpers in `scoring` would
close that loop into an import cycle. They touch only ORM models and no other
service, so a lower module is where they belong anyway.

Nothing here is a soft delete. A stale derived row is not history worth
keeping -- it is a wrong answer that would be served as a right one.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.enums import CandidateStatus
from app.models.candidate import Candidate
from app.models.evaluation import MatchResult, Score

logger = logging.getLogger(__name__)


def invalidate_score_for_candidate(db: Session, candidate_id: uuid.UUID) -> int:
    """Drop one candidate's score. Returns how many rows went.

    Does not commit: the caller is mid-transaction doing the thing that caused
    the invalidation, and the two must land together or not at all.
    """
    removed = db.execute(delete(Score).where(Score.candidate_id == candidate_id)).rowcount or 0
    if removed:
        _demote_scored(db, [candidate_id])
        logger.info("Invalidated the stored score for candidate %s", candidate_id)
    return removed


def invalidate_scores_for_job(db: Session, job_id: uuid.UUID) -> int:
    """Drop every score in a job. Returns how many rows went.

    Used when a change alters the requirement set the whole job is scored
    against, so no candidate's stored number is still correct.
    """
    candidate_ids = list(db.scalars(select(Candidate.id).where(Candidate.job_id == job_id)))
    if not candidate_ids:
        return 0

    removed = db.execute(delete(Score).where(Score.candidate_id.in_(candidate_ids))).rowcount or 0
    if removed:
        _demote_scored(db, candidate_ids)
        logger.info("Invalidated %s stored score(s) for job %s", removed, job_id)
    return removed


def invalidate_match_results_for_job(db: Session, job_id: uuid.UUID) -> int:
    """Drop every verdict in a job, and every score that rested on one.

    This is what unconfirming a job costs (ADR-0004). The price is deliberate
    and visible: it is what makes changing a confirmed requirement a considered
    act rather than a silent corruption of results already shown to someone.
    """
    candidate_ids = list(db.scalars(select(Candidate.id).where(Candidate.job_id == job_id)))
    if not candidate_ids:
        return 0

    removed = (
        db.execute(delete(MatchResult).where(MatchResult.candidate_id.in_(candidate_ids))).rowcount
        or 0
    )
    invalidate_scores_for_job(db, job_id)
    if removed:
        logger.info("Invalidated %s match result(s) for job %s", removed, job_id)
    return removed


def _demote_scored(db: Session, candidate_ids: list[uuid.UUID]) -> None:
    """A candidate whose score was just deleted is no longer SCORED.

    Status is meant to describe how far a candidate actually got. Leaving it at
    SCORED with no `score` row would be a status that contradicts the data it
    claims to summarize.
    """
    for candidate in db.scalars(select(Candidate).where(Candidate.id.in_(candidate_ids))):
        if candidate.status is CandidateStatus.SCORED:
            candidate.status = CandidateStatus.EXTRACTED


__all__ = [
    "invalidate_match_results_for_job",
    "invalidate_score_for_candidate",
    "invalidate_scores_for_job",
]
