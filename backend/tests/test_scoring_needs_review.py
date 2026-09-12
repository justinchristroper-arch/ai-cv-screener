"""What happens to a score when the engine could not read something.

`NEEDS_REVIEW` is excluded from both sides of the weighted average (ADR-0012).
The reason is the same one ADR-0008 gives for refusing to call an undefined
score zero: a 0 would report that the candidate failed an evaluation that never
actually completed, and the recruiter would have no way to tell the difference.

These tests pin the five interactions that could silently turn uncertainty into
a penalty.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.core.enums import (
    MatchVerdict,
    RecommendationBand,
    RequirementCategory,
    RequirementOrigin,
    ScoreStatus,
)
from app.models.job import Requirement
from app.services import scoring


def _requirement(
    weight: str | int,
    *,
    must_have: bool = False,
    text: str = "A requirement",
    order: int = 0,
) -> Requirement:
    return Requirement(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        text=text,
        category=RequirementCategory.TECHNICAL_SKILL,
        must_have=must_have,
        weight=Decimal(str(weight)),
        display_order=order,
        origin=RequirementOrigin.HR_ADDED,
    )


# --------------------------------------------------------------------------
# The denominator
# --------------------------------------------------------------------------


def test_an_unresolved_requirement_leaves_the_average_untouched() -> None:
    """Two matched out of two scoreable is 100, whatever the third one did."""
    breakdown = scoring.compute_score(
        [
            (_requirement(1, order=0), MatchVerdict.MATCHED),
            (_requirement(1, order=1), MatchVerdict.MATCHED),
            (_requirement(1, order=2), MatchVerdict.NEEDS_REVIEW),
        ]
    )
    assert breakdown.status is ScoreStatus.COMPUTED
    assert breakdown.score == 100
    assert breakdown.total_weight == Decimal("2")


def test_its_weight_is_not_redistributed_to_the_others() -> None:
    """Excluding a weight must not quietly re-price the remaining criteria."""
    with_review = scoring.compute_score(
        [
            (_requirement(1, order=0), MatchVerdict.MATCHED),
            (_requirement(1, order=1), MatchVerdict.NO_EVIDENCE),
            (_requirement(8, order=2), MatchVerdict.NEEDS_REVIEW),
        ]
    )
    without = scoring.compute_score(
        [
            (_requirement(1, order=0), MatchVerdict.MATCHED),
            (_requirement(1, order=1), MatchVerdict.NO_EVIDENCE),
        ]
    )
    assert with_review.score == without.score == 50


def test_an_unresolved_requirement_scores_nothing_rather_than_zero() -> None:
    """A 0 here would be indistinguishable from a genuine NO_EVIDENCE."""
    breakdown = scoring.compute_score([(_requirement(3), MatchVerdict.NEEDS_REVIEW)])
    unresolved = breakdown.needs_review[0]
    assert unresolved.verdict_value is None
    assert unresolved.points is None


def test_everything_unresolved_is_undefined_not_zero() -> None:
    breakdown = scoring.compute_score(
        [
            (_requirement(1, order=0), MatchVerdict.NEEDS_REVIEW),
            (_requirement(2, order=1), MatchVerdict.NEEDS_REVIEW),
        ]
    )
    assert breakdown.status is ScoreStatus.UNDEFINED_NO_DECIDABLE
    assert breakdown.score is None
    assert breakdown.score_raw is None
    assert breakdown.band is None
    assert breakdown.is_defined is False


def test_no_requirements_at_all_keeps_its_own_undefined_status() -> None:
    """The two undefined cases stay distinguishable."""
    assert scoring.compute_score([]).status is ScoreStatus.UNDEFINED_NO_WEIGHT


# --------------------------------------------------------------------------
# The must-have guard
# --------------------------------------------------------------------------


def test_an_unresolved_must_have_does_not_trip_the_missing_evidence_guard() -> None:
    """The guard is for absent evidence. "We could not tell" is not that."""
    breakdown = scoring.compute_score(
        [
            (_requirement(3, must_have=True, order=0), MatchVerdict.MATCHED),
            (_requirement(3, must_have=True, order=1), MatchVerdict.NEEDS_REVIEW),
        ]
    )
    # Every scoreable must-have was met, so coverage is complete...
    assert breakdown.must_have_coverage == Decimal("1")
    # ...but the label must not read as a clean pass.
    assert breakdown.band is RecommendationBand.REVIEW
    assert breakdown.band_raw is RecommendationBand.STRONG_MATCH


def test_a_genuinely_missing_must_have_still_names_itself() -> None:
    """Precedence: real absence outranks uncertainty when both are present."""
    # Weighted so the raw band is high enough for the guard to have work to do:
    # it only ever lowers a band, so a score already at REVIEW cannot show it.
    missing = _requirement(1, must_have=True, order=1)
    breakdown = scoring.compute_score(
        [
            (_requirement(9, must_have=True, order=0), MatchVerdict.MATCHED),
            (missing, MatchVerdict.NO_EVIDENCE),
            (_requirement(5, must_have=True, order=2), MatchVerdict.NEEDS_REVIEW),
        ]
    )
    assert breakdown.band_raw is RecommendationBand.STRONG_MATCH
    assert breakdown.capped is True
    assert breakdown.capped_by_requirement_id == missing.id


def test_must_have_coverage_ignores_what_could_not_be_read() -> None:
    """Averaging an unread requirement in as a zero would understate coverage."""
    breakdown = scoring.compute_score(
        [
            (_requirement(1, must_have=True, order=0), MatchVerdict.MATCHED),
            (_requirement(1, must_have=True, order=1), MatchVerdict.NEEDS_REVIEW),
        ]
    )
    assert breakdown.must_have_coverage == Decimal("1")


def test_all_must_haves_unresolved_reports_no_coverage_rather_than_zero() -> None:
    breakdown = scoring.compute_score(
        [
            (_requirement(1, must_have=True, order=0), MatchVerdict.NEEDS_REVIEW),
            (_requirement(1, order=1), MatchVerdict.MATCHED),
        ]
    )
    assert breakdown.must_have_coverage is None


# --------------------------------------------------------------------------
# Surfacing it
# --------------------------------------------------------------------------


def test_the_breakdown_flags_that_something_needs_review() -> None:
    breakdown = scoring.compute_score(
        [
            (_requirement(1, order=0), MatchVerdict.MATCHED),
            (_requirement(1, order=1), MatchVerdict.NEEDS_REVIEW),
        ]
    )
    assert breakdown.review_flag is True
    assert len(breakdown.needs_review) == 1


def test_a_clean_run_raises_no_flag() -> None:
    breakdown = scoring.compute_score([(_requirement(1), MatchVerdict.MATCHED)])
    assert breakdown.review_flag is False
    assert breakdown.needs_review == []


def test_must_have_review_is_separately_identifiable() -> None:
    breakdown = scoring.compute_score(
        [
            (_requirement(1, must_have=False, order=0), MatchVerdict.NEEDS_REVIEW),
            (_requirement(1, must_have=True, order=1), MatchVerdict.NEEDS_REVIEW),
        ]
    )
    assert len(breakdown.needs_review) == 2
    assert len(breakdown.must_have_needs_review) == 1


# --------------------------------------------------------------------------
# The rest of the contract is unchanged
# --------------------------------------------------------------------------


def test_the_three_scoreable_verdicts_keep_their_values() -> None:
    config = scoring.DEFAULT_CONFIG
    assert config.value_of(MatchVerdict.MATCHED) == Decimal("1.0")
    assert config.value_of(MatchVerdict.PARTIAL) == Decimal("0.5")
    assert config.value_of(MatchVerdict.NO_EVIDENCE) == Decimal("0")


def test_asking_what_an_unresolved_verdict_is_worth_is_an_error() -> None:
    """It has no value. Returning 0 would be the bug this module avoids."""
    import pytest

    with pytest.raises(ValueError):
        scoring.DEFAULT_CONFIG.value_of(MatchVerdict.NEEDS_REVIEW)


def test_the_same_inputs_always_produce_the_same_score() -> None:
    pairs = [
        (_requirement(2, order=0), MatchVerdict.MATCHED),
        (_requirement(1, order=1), MatchVerdict.NEEDS_REVIEW),
        (_requirement(1, order=2), MatchVerdict.PARTIAL),
    ]
    first = scoring.compute_score(pairs)
    second = scoring.compute_score(pairs)
    assert (first.score, first.score_raw, first.status) == (
        second.score,
        second.score_raw,
        second.status,
    )
