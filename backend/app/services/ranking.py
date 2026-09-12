"""Stage 12: order one job's candidates, stably and explicably.

The order is fixed by docs/data-model.md section 9 and implemented here rather
than invented:

1. ``score.score`` **descending**. A candidate whose score is
   ``UNDEFINED_NO_WEIGHT`` sorts **last**, never as a 0 -- the same distinction
   the scorer makes, carried through to the list.
2. ``score.must_have_coverage`` **descending**, nulls last.
3. Count of ``MATCHED`` verdicts **descending** -- at the same score, prefer the
   candidate with more concrete evidence over one with accumulated partials.
4. ``candidate.created_at`` ascending, then ``candidate.id``.

Rule 4 exists to make the order **total**. Without it, two candidates equal on
every earlier rule would come back in whatever order the database felt like,
and a recruiter refreshing the page would see them swap. With it, repeated runs
on identical data produce an identical list.

Nothing here filters. There is no limit, no offset, no top-K and no threshold:
every candidate in the job comes back, including the lowest-scoring one
(product-spec section 12 -- the system never hides a candidate). Candidates who
have not been scored yet, and candidates whose processing failed, are returned
in their own groups rather than being dropped or silently mixed into the order.

**The ranking key cannot read a name.** It is a pure function of a score, a
coverage ratio, a verdict count and two identifiers. The candidate's display
name is carried in the response so a recruiter can tell rows apart, and it takes
no part in the ordering -- structurally, not by convention.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import CandidateFailureReason, CandidateStatus, MatchVerdict, ScoreStatus
from app.models.candidate import Candidate, CandidateDocument, ParsedDocument
from app.models.evaluation import MatchResult, Score
from app.models.job import Job, Requirement
from app.services import jobs as jobs_service

#: Stable codes a client can branch on, in the order they are emitted.
WARNING_MUST_HAVE_NOT_EVIDENCED = "MUST_HAVE_NOT_EVIDENCED"
#: A must-have the engine could not resolve. Kept separate from the line above
#: because the two mean opposite things about the document: one says the CV
#: shows nothing, the other says the CV shows something we could not read.
WARNING_MUST_HAVE_UNRESOLVED = "MUST_HAVE_NEEDS_REVIEW"
WARNING_SCORE_UNDEFINED = "SCORE_UNDEFINED"
#: Every criterion came back unresolved, so there was no average to take.
WARNING_NOTHING_DECIDABLE = "NO_DECIDABLE_CRITERIA"
#: Some did. Those are out of the score entirely -- not counted as zeros.
WARNING_CRITERIA_UNRESOLVED = "CRITERIA_NEED_REVIEW"
WARNING_EVIDENCE_DOWNGRADED = "EVIDENCE_DOWNGRADED"
WARNING_INSTRUCTION_LIKE_TEXT = "INSTRUCTION_LIKE_TEXT_IN_CV"


@dataclass(frozen=True)
class RankingInputs:
    """Everything the order depends on, and nothing else.

    Deliberately a plain value object rather than an ORM row: it is what makes
    the tie-break testable without a database, and it makes the absence of a
    name from the ordering visible at a glance.
    """

    candidate_id: uuid.UUID
    created_at: datetime
    score: int | None
    must_have_coverage: Decimal | None
    matched_count: int


def ranking_key(inputs: RankingInputs) -> tuple:
    """The sort key from docs/data-model.md section 9.

    Descending fields are negated rather than sorted separately, so the whole
    order is one comparison and there is no multi-pass sort to get wrong.
    """
    return (
        # An undefined score is not a low score. It sorts last, on its own flag,
        # so it can never be compared numerically against a real one.
        0 if inputs.score is not None else 1,
        -inputs.score if inputs.score is not None else 0,
        0 if inputs.must_have_coverage is not None else 1,
        -inputs.must_have_coverage if inputs.must_have_coverage is not None else Decimal(0),
        -inputs.matched_count,
        inputs.created_at,
        str(inputs.candidate_id),
    )


def order_candidates(entries: Sequence[RankingInputs]) -> list[RankingInputs]:
    """Sort by `ranking_key`. Pure, and total: no two entries ever tie."""
    return sorted(entries, key=ranking_key)


@dataclass(frozen=True)
class RankedEntry:
    """One row of the ranked list, with everything that placed it there."""

    position: int
    candidate: Candidate
    score: Score
    matched_count: int
    #: Criteria the engine left unresolved. Excluded from the score on both
    #: sides of the average, so this is the count the recruiter needs in order
    #: to know how much of the list the number actually covers.
    needs_review_count: int
    original_filename: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UnrankedEntry:
    """A candidate with no score, or one whose processing failed.

    Both groups exist so that neither can be quietly dropped. A recruiter who
    uploaded twenty files must be able to account for twenty files.
    """

    candidate: Candidate
    original_filename: str | None


@dataclass(frozen=True)
class JobRanking:
    """A job's candidates, in three groups that are never merged."""

    job: Job
    ranked: list[RankedEntry]
    not_yet_scored: list[UnrankedEntry]
    failed: list[UnrankedEntry]

    @property
    def total(self) -> int:
        return len(self.ranked) + len(self.not_yet_scored) + len(self.failed)


def _warnings_for(
    score: Score,
    *,
    downgraded_count: int,
    injection_flag_count: int,
    needs_review_count: int,
    must_have_no_evidence: int,
) -> list[str]:
    """Things a recruiter should see next to a number, in a fixed order.

    Every one of these is drawn from data already stored by an earlier stage.
    None of them changes the score or the position; they exist so that a figure
    is never shown without the caveats that belong to it.
    """
    warnings: list[str] = []
    if score.capped:
        # The guard prefers genuine absence over uncertainty, so a cap with any
        # unevidenced must-have present is that one; otherwise it was an
        # unresolved must-have (services/scoring._apply_must_have_guard).
        warnings.append(
            WARNING_MUST_HAVE_NOT_EVIDENCED
            if must_have_no_evidence
            else WARNING_MUST_HAVE_UNRESOLVED
        )
    if score.status is ScoreStatus.UNDEFINED_NO_WEIGHT:
        warnings.append(WARNING_SCORE_UNDEFINED)
    elif score.status is ScoreStatus.UNDEFINED_NO_DECIDABLE:
        warnings.append(WARNING_NOTHING_DECIDABLE)
    elif needs_review_count:
        # Only when a score was actually produced: when nothing was decidable
        # the line above already says it, and more precisely.
        warnings.append(WARNING_CRITERIA_UNRESOLVED)
    if downgraded_count:
        warnings.append(WARNING_EVIDENCE_DOWNGRADED)
    if injection_flag_count:
        warnings.append(WARNING_INSTRUCTION_LIKE_TEXT)
    return warnings


def rank_job_candidates(db: Session, job_id: uuid.UUID) -> JobRanking:
    """Rank every candidate in one job.

    The job filter is the whole of job isolation: a candidate belongs to exactly
    one job (docs/data-model.md section 12), so a query filtered by ``job_id``
    cannot reach another job's rows. Scores from other jobs are not merely
    excluded from the order -- they are never loaded.

    Reads only. Nothing is computed, stored or invalidated here, which is why
    there is no confirmation gate: unconfirming a job already discards its
    scores, so its candidates simply appear as not-yet-scored, which is the
    honest thing to show.
    """
    job = jobs_service.get_job(db, job_id)

    matched = (
        select(MatchResult.candidate_id, func.count().label("count"))
        .where(MatchResult.verdict == MatchVerdict.MATCHED)
        .group_by(MatchResult.candidate_id)
        .subquery()
    )
    downgraded = (
        select(MatchResult.candidate_id, func.count().label("count"))
        .where(MatchResult.downgraded.is_(True))
        .group_by(MatchResult.candidate_id)
        .subquery()
    )
    unresolved = (
        select(MatchResult.candidate_id, func.count().label("count"))
        .where(MatchResult.verdict == MatchVerdict.NEEDS_REVIEW)
        .group_by(MatchResult.candidate_id)
        .subquery()
    )
    # Which of the two reasons capped the band. Counted here rather than
    # re-derived in the UI, because the distinction -- the CV shows nothing
    # versus the CV shows something we could not read -- is the whole point of
    # having two verdicts (ADR-0012).
    must_have_gaps = (
        select(
            MatchResult.candidate_id,
            func.count()
            .filter(MatchResult.verdict == MatchVerdict.NO_EVIDENCE)
            .label("no_evidence"),
        )
        .join(Requirement, Requirement.id == MatchResult.requirement_id)
        .where(Requirement.must_have.is_(True))
        .group_by(MatchResult.candidate_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Candidate,
            Score,
            func.coalesce(matched.c.count, 0),
            func.coalesce(downgraded.c.count, 0),
            func.coalesce(unresolved.c.count, 0),
            func.coalesce(must_have_gaps.c.no_evidence, 0),
            CandidateDocument.original_filename,
            ParsedDocument.injection_flags,
        )
        .outerjoin(Score, Score.candidate_id == Candidate.id)
        .outerjoin(matched, matched.c.candidate_id == Candidate.id)
        .outerjoin(downgraded, downgraded.c.candidate_id == Candidate.id)
        .outerjoin(unresolved, unresolved.c.candidate_id == Candidate.id)
        .outerjoin(must_have_gaps, must_have_gaps.c.candidate_id == Candidate.id)
        .outerjoin(CandidateDocument, CandidateDocument.candidate_id == Candidate.id)
        .outerjoin(ParsedDocument, ParsedDocument.document_id == CandidateDocument.id)
        .where(Candidate.job_id == job_id)
    ).all()

    scored: dict[uuid.UUID, tuple] = {}
    inputs: list[RankingInputs] = []
    not_yet_scored: list[UnrankedEntry] = []
    failed: list[UnrankedEntry] = []

    for (
        candidate,
        score,
        matched_count,
        downgraded_count,
        unresolved_count,
        must_have_no_evidence,
        filename,
        flags,
    ) in rows:
        # Status is checked before the score: a failed candidate is reported as
        # failed even if an earlier run left a score behind, because a number
        # attached to a broken pipeline would be worse than no number at all.
        if candidate.status is CandidateStatus.FAILED:
            failed.append(UnrankedEntry(candidate=candidate, original_filename=filename))
            continue
        if score is None:
            not_yet_scored.append(UnrankedEntry(candidate=candidate, original_filename=filename))
            continue

        scored[candidate.id] = (
            candidate,
            score,
            matched_count,
            downgraded_count,
            unresolved_count,
            must_have_no_evidence,
            filename,
            flags,
        )
        inputs.append(
            RankingInputs(
                candidate_id=candidate.id,
                created_at=candidate.created_at,
                score=score.score,
                must_have_coverage=score.must_have_coverage,
                matched_count=matched_count,
            )
        )

    ranked: list[RankedEntry] = []
    for position, entry in enumerate(order_candidates(inputs), start=1):
        (
            candidate,
            score,
            matched_count,
            downgraded_count,
            unresolved_count,
            must_have_no_evidence,
            filename,
            flags,
        ) = scored[entry.candidate_id]
        ranked.append(
            RankedEntry(
                position=position,
                candidate=candidate,
                score=score,
                matched_count=matched_count,
                needs_review_count=unresolved_count,
                original_filename=filename,
                warnings=_warnings_for(
                    score,
                    downgraded_count=downgraded_count,
                    injection_flag_count=len(flags or []),
                    needs_review_count=unresolved_count,
                    must_have_no_evidence=must_have_no_evidence,
                ),
            )
        )

    return JobRanking(
        job=job,
        ranked=ranked,
        not_yet_scored=sorted(not_yet_scored, key=_arrival_key),
        failed=sorted(failed, key=_arrival_key),
    )


def _arrival_key(entry: UnrankedEntry) -> tuple:
    """Order the unranked groups by arrival, so they are stable too."""
    return (entry.candidate.created_at, str(entry.candidate.id))


def failure_reason_of(entry: UnrankedEntry) -> CandidateFailureReason | None:
    return entry.candidate.failure_reason


__all__ = [
    "WARNING_CRITERIA_UNRESOLVED",
    "WARNING_EVIDENCE_DOWNGRADED",
    "WARNING_INSTRUCTION_LIKE_TEXT",
    "WARNING_MUST_HAVE_NOT_EVIDENCED",
    "WARNING_MUST_HAVE_UNRESOLVED",
    "WARNING_NOTHING_DECIDABLE",
    "WARNING_SCORE_UNDEFINED",
    "JobRanking",
    "RankedEntry",
    "RankingInputs",
    "UnrankedEntry",
    "failure_reason_of",
    "order_candidates",
    "rank_job_candidates",
    "ranking_key",
]
