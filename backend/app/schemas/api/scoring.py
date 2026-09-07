"""HTTP contract for candidate scoring.

The response is built so a recruiter can check the arithmetic by hand. Every
requirement contributes a visible line -- its weight, the verdict the evidence
supported, what that verdict is worth, and the product of the two -- and those
lines sum to the stored `weighted_sum`. That is the whole claim the score makes.

What the score is **not**, stated in the field descriptions because this is the
surface a UI reads from:

* not a hiring decision, and not an input to one that this system takes;
* not a probability of anything -- a 90 does not mean a 90% chance of success;
* not comparable across jobs, because the requirement sets and weights differ.

Evidence quotes are deliberately absent here. They belong to the verdicts and
are served by `GET /api/candidates/{id}/matches`; duplicating them into the
score response would make two places to keep in step and imply that evidence is
part of the arithmetic, which it is not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.enums import (
    MatchVerdict,
    RecommendationBand,
    RequirementCategory,
    ScoreStatus,
)


class ContributionResponse(BaseModel):
    """One requirement's line in the arithmetic."""

    requirement_id: uuid.UUID
    requirement_text: str
    category: RequirementCategory
    must_have: bool = Field(
        description=(
            "The recruiter's hard-requirement flag. It affects the weight they "
            "chose and the separately reported must-have coverage; it never "
            "affects how evidence was read."
        )
    )
    display_order: int

    weight: Decimal = Field(description="The recruiter-controlled weight for this requirement.")
    verdict: MatchVerdict = Field(
        description=(
            "NO_EVIDENCE means this CV contains no verified evidence for the "
            "requirement. It is never a claim that the candidate lacks the skill."
        )
    )
    verdict_value: Decimal = Field(
        description="What this verdict is worth: MATCHED 1.0, PARTIAL 0.5, NO_EVIDENCE 0.0."
    )
    points: Decimal = Field(description="weight x verdict_value.")


class ScoreResponse(BaseModel):
    """A candidate's score, with every input needed to reproduce it."""

    candidate_id: uuid.UUID
    job_id: uuid.UUID

    status: ScoreStatus = Field(
        description=(
            "COMPUTED, or UNDEFINED_NO_WEIGHT when the job has no requirements or "
            "every weight is zero. An undefined score is not zero: nothing was "
            "asked of the candidate, so there is nothing to report."
        )
    )

    score: int | None = Field(
        description="0-100, or null when undefined. Only comparable within this job."
    )
    score_raw: Decimal | None = Field(
        description="weighted_sum / total_weight, before scaling to 0-100."
    )
    weighted_sum: Decimal | None = Field(description="The sum of every line's points.")
    total_weight: Decimal | None = Field(description="The sum of every requirement's weight.")

    must_have_coverage: Decimal | None = Field(
        description=(
            "The same weighted average restricted to must-have requirements, "
            "reported separately because a high overall score can conceal one "
            "missing hard requirement. Null when the job has no must-haves."
        )
    )

    band: RecommendationBand | None = Field(
        description=(
            "A heuristic reading aid: 90+ Strong Match, 75-89 Good Match, 60-74 "
            "Review, below 60 Low Match. These thresholds have no empirical "
            "backing and are not a hiring decision, a rejection, or a probability "
            "of job performance. No candidate is ever hidden or filtered by band."
        )
    )
    band_raw: RecommendationBand | None = Field(
        description="The band the score alone implied, before the must-have guard."
    )
    capped: bool = Field(
        description=(
            "True when the displayed band was lowered to Review because a "
            "must-have requirement has no evidence. The score itself is unchanged."
        )
    )
    capped_by_requirement_id: uuid.UUID | None = None
    capped_by_requirement_text: str | None = Field(
        default=None, description="Which unevidenced must-have triggered the cap."
    )

    scoring_config_version: str = Field(
        description="Names the verdict values and thresholds this score was computed under."
    )
    computed_at: datetime

    contributions: list[ContributionResponse] = Field(
        description="One line per requirement, in the recruiter's display order."
    )
