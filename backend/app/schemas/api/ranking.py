"""HTTP contract for the ranked candidate list.

Three groups, never merged, because they mean different things and collapsing
them would misrepresent all three:

* ``ranked`` — candidates with a score, in the documented order.
* ``not_yet_scored`` — uploaded and processed, but not scored yet. Present so a
  recruiter who uploaded twenty files can account for twenty files.
* ``failed`` — processing failed, with the reason. Never merged into the order
  and never silently dropped.

Everything that decided a position is returned alongside it — score, must-have
coverage, and the count of matched requirements — so a recruiter can see why one
candidate sits above another rather than having to trust the arrangement.

The list is never truncated. There is no limit, no offset and no threshold
parameter, because a screening tool that can hide a candidate is a screening
tool that will (product-spec section 12).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.enums import (
    CandidateFailureReason,
    CandidateStatus,
    RecommendationBand,
    ScoreStatus,
)


class RankedCandidateResponse(BaseModel):
    """One row of the ranked list."""

    position: int = Field(
        description=(
            "1-based position in this job's order. Positions are unique: where "
            "scores tie, the documented tie-break settles it, so the order is "
            "stable across repeated requests on unchanged data."
        )
    )
    candidate_id: uuid.UUID
    display_name: str | None = Field(
        default=None,
        description=(
            "Shown so a recruiter can tell rows apart. It takes no part in the "
            "ordering: the ranking key is computed from the score, the coverage "
            "and a verdict count, and never reads this field."
        ),
    )
    original_filename: str | None = None
    status: CandidateStatus

    score_status: ScoreStatus
    score: int | None = Field(
        description=(
            "0-100, or null when undefined. Only comparable within this job — a "
            "78 here and a 78 on another job mean nothing to each other."
        )
    )
    must_have_coverage: Decimal | None = None
    band: RecommendationBand | None = Field(
        description=(
            "A heuristic reading aid, not a hiring decision and not a "
            "probability. No candidate is filtered, hidden or rejected by band."
        )
    )
    band_raw: RecommendationBand | None = Field(
        default=None, description="The band the score alone implied, before the must-have guard."
    )
    capped: bool = False

    matched_count: int = Field(
        description="How many requirements this CV evidenced outright. The third tie-break."
    )
    needs_review_count: int = Field(
        default=0,
        description=(
            "How many criteria the engine left unresolved. They are excluded "
            "from the score on both sides of the average rather than counted as "
            "zeros, so this says how much of the criteria list the number "
            "actually covers."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Stable codes a recruiter should read next to the number: "
            "MUST_HAVE_NOT_EVIDENCED, MUST_HAVE_NEEDS_REVIEW, SCORE_UNDEFINED, "
            "NO_DECIDABLE_CRITERIA, CRITERIA_NEED_REVIEW, EVIDENCE_DOWNGRADED, "
            "INSTRUCTION_LIKE_TEXT_IN_CV, MULTI_COLUMN_LAYOUT. None of them changes "
            "the score or the position."
        ),
    )

    scoring_config_version: str
    computed_at: datetime


class UnscoredCandidateResponse(BaseModel):
    """A candidate that has no score yet. Listed, never dropped."""

    candidate_id: uuid.UUID
    display_name: str | None = None
    original_filename: str | None = None
    status: CandidateStatus = Field(
        description="How far this candidate got. Scoring needs matching, which needs a profile."
    )


class FailedCandidateResponse(BaseModel):
    """A candidate whose processing failed, with an honest reason."""

    candidate_id: uuid.UUID
    display_name: str | None = None
    original_filename: str | None = None
    status: CandidateStatus
    failure_reason: CandidateFailureReason | None = None
    failure_detail: str | None = Field(
        default=None, description="Human-readable detail. Never a stack trace."
    )


class RankingSummary(BaseModel):
    """Counts, so a recruiter can reconcile the list against what they uploaded."""

    total: int
    ranked: int
    not_yet_scored: int
    failed: int


class JobRankingResponse(BaseModel):
    """A job's candidates, ordered and grouped."""

    job_id: uuid.UUID
    job_title: str
    requirements_confirmed_at: datetime | None = Field(
        default=None,
        description=(
            "Null when the requirement set is not confirmed. Unconfirming discards "
            "every score in the job, so its candidates appear as not yet scored."
        ),
    )

    ranked: list[RankedCandidateResponse]
    not_yet_scored: list[UnscoredCandidateResponse]
    failed: list[FailedCandidateResponse]
    summary: RankingSummary
