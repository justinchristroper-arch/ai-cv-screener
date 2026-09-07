"""Scoring: the arithmetic, the edge cases, and what invalidates a stored score.

The claim this milestone makes is narrow and checkable: **a score is a pure
function of stored verdicts and weights, and a recruiter can reproduce it by
hand.** So the formula is tested as a pure function with no database at all,
and the persistence layer is tested separately for the things only a database
can answer -- the confirmation gate, staleness, and job isolation.

Nothing here touches a language model. Scoring has no model call in its path,
which is the point of ADR-0001 and ADR-0008.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    CandidateStatus,
    JdSourceType,
    MatchVerdict,
    RecommendationBand,
    RequirementCategory,
    RequirementOrigin,
    ScoreStatus,
)
from app.core.errors import ConflictError, NotFoundError, RequirementsNotConfirmedError
from app.models.evaluation import MatchResult, Score
from app.models.job import Requirement
from app.services import jobs, matching, profile_extraction, requirements, scoring
from tests.factories import make_candidate_from_fixture, make_job_with_requirements

# --------------------------------------------------------------------------
# Pure formula — no database, no network, no clock
# --------------------------------------------------------------------------


def _requirement(
    weight: str | int,
    *,
    must_have: bool = False,
    text: str = "A requirement",
    order: int = 0,
    category: RequirementCategory = RequirementCategory.TECHNICAL_SKILL,
) -> Requirement:
    """An unsaved requirement row. The scorer never needs it persisted."""
    return Requirement(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        text=text,
        category=category,
        must_have=must_have,
        weight=Decimal(str(weight)),
        display_order=order,
        origin=RequirementOrigin.LLM_EXTRACTED,
    )


M = MatchVerdict.MATCHED
P = MatchVerdict.PARTIAL
N = MatchVerdict.NO_EVIDENCE


def test_the_verdict_values_are_the_specified_ones() -> None:
    """MATCHED 1.0, PARTIAL 0.5, NO_EVIDENCE 0.0 (product-spec section 11)."""
    config = scoring.DEFAULT_CONFIG

    assert config.value_of(M) == Decimal("1.0")
    assert config.value_of(P) == Decimal("0.5")
    assert config.value_of(N) == Decimal("0")


def test_the_worked_example_from_the_specification() -> None:
    """weights 5 / 3 / 2 against MATCHED / PARTIAL / NO_EVIDENCE gives 65."""
    result = scoring.compute_score(
        [
            (_requirement(5, order=0), M),
            (_requirement(3, order=1), P),
            (_requirement(2, order=2), N),
        ]
    )

    assert result.status is ScoreStatus.COMPUTED
    assert [item.points for item in result.contributions] == [
        Decimal("5.0"),
        Decimal("1.5"),
        Decimal("0"),
    ]
    assert result.weighted_sum == Decimal("6.5")
    assert result.total_weight == Decimal("10")
    assert result.score == 65


def test_the_contributions_sum_to_the_weighted_total() -> None:
    """The arithmetic shown to the recruiter has to be the arithmetic used."""
    result = scoring.compute_score(
        [
            (_requirement(4, order=0), M),
            (_requirement(3, order=1), P),
            (_requirement(9, order=2), N),
        ]
    )

    assert sum(item.points for item in result.contributions) == result.weighted_sum


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [(M, 100), (P, 50), (N, 0)],
)
def test_a_single_weighted_requirement(verdict: MatchVerdict, expected: int) -> None:
    """One requirement, so the score is just that verdict's value."""
    result = scoring.compute_score([(_requirement(7), verdict)])

    assert result.score == expected
    assert result.status is ScoreStatus.COMPUTED


def test_no_requirements_gives_an_undefined_score_not_zero() -> None:
    """A 0 would read as "this candidate is terrible".

    The truth is that nothing was asked of them, which is a different statement
    entirely -- the same evidence-first distinction as NO_EVIDENCE, applied to
    arithmetic (ADR-0008).
    """
    result = scoring.compute_score([])

    assert result.status is ScoreStatus.UNDEFINED_NO_WEIGHT
    assert result.score is None
    assert result.score != 0
    assert result.score_raw is None
    assert result.weighted_sum is None
    assert result.total_weight is None
    assert result.band is None
    assert result.band_raw is None


def test_all_weights_zero_gives_an_undefined_score_not_zero() -> None:
    """Also removes the division-by-zero case by construction."""
    result = scoring.compute_score([(_requirement(0), M), (_requirement(0), N)])

    assert result.status is ScoreStatus.UNDEFINED_NO_WEIGHT
    assert result.score is None
    assert result.band is None
    # The lines are still returned: a recruiter can see what was evaluated even
    # though no total can be formed from it.
    assert len(result.contributions) == 2


def test_fractional_weights_are_honoured_exactly() -> None:
    """Weight is NUMERIC(5,2); decimal arithmetic, never float."""
    result = scoring.compute_score(
        [(_requirement("2.50", order=0), M), (_requirement("1.25", order=1), P)]
    )

    assert result.weighted_sum == Decimal("3.125")
    assert result.total_weight == Decimal("3.75")
    assert result.score == 83  # 3.125 / 3.75 = 0.8333... -> 83


def test_a_zero_weighted_requirement_contributes_nothing_but_is_still_shown() -> None:
    result = scoring.compute_score([(_requirement(0, order=0), M), (_requirement(4, order=1), P)])

    assert result.total_weight == Decimal("4")
    assert result.score == 50
    assert len(result.contributions) == 2


def test_mixed_verdicts_with_different_weights() -> None:
    result = scoring.compute_score(
        [
            (_requirement(3, order=0, must_have=True), M),
            (_requirement(3, order=1, must_have=True), P),
            (_requirement(1, order=2), N),
            (_requirement(1, order=3), M),
        ]
    )

    # (3 + 1.5 + 0 + 1) / 8 = 0.6875 -> 69
    assert result.weighted_sum == Decimal("5.5")
    assert result.total_weight == Decimal("8")
    assert result.score == 69


def test_rounding_is_half_up_not_bankers() -> None:
    """62.5 becomes 63, which is what someone checking on paper expects.

    Python's built-in `round` is banker's rounding and would give 62 here --
    defensible, and impossible for a recruiter to predict.
    """
    result = scoring.compute_score(
        [
            (_requirement(1, order=0), M),
            (_requirement(1, order=1), M),
            (_requirement(1, order=2), P),
            (_requirement(1, order=3), N),
        ]
    )

    assert result.score_raw == Decimal("0.62500000")
    assert result.score == 63


def test_contributions_come_back_in_the_recruiters_display_order() -> None:
    result = scoring.compute_score(
        [
            (_requirement(1, order=2, text="third"), M),
            (_requirement(1, order=0, text="first"), M),
            (_requirement(1, order=1, text="second"), M),
        ]
    )

    assert [item.requirement_text for item in result.contributions] == [
        "first",
        "second",
        "third",
    ]


def test_scoring_is_reproducible_on_identical_input() -> None:
    """Same verdicts, same weights, same number. Every time."""
    pairs = [
        (_requirement(3, order=0, must_have=True), M),
        (_requirement(2, order=1), P),
        (_requirement(1, order=2), N),
    ]

    first = scoring.compute_score(pairs)
    second = scoring.compute_score(pairs)

    assert (first.score, first.score_raw, first.weighted_sum, first.total_weight) == (
        second.score,
        second.score_raw,
        second.weighted_sum,
        second.total_weight,
    )
    assert first.band is second.band


# --------------------------------------------------------------------------
# Bands — heuristic reading aids, tested at their exact edges
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (100, RecommendationBand.STRONG_MATCH),
        (90, RecommendationBand.STRONG_MATCH),
        (89, RecommendationBand.GOOD_MATCH),
        (75, RecommendationBand.GOOD_MATCH),
        (74, RecommendationBand.REVIEW),
        (60, RecommendationBand.REVIEW),
        (59, RecommendationBand.LOW_MATCH),
        (0, RecommendationBand.LOW_MATCH),
    ],
)
def test_band_boundaries(score: int, band: RecommendationBand) -> None:
    assert scoring.band_for(score) is band


# --------------------------------------------------------------------------
# Must-have: coverage, and the band guard
# --------------------------------------------------------------------------


def test_must_have_coverage_is_reported_separately_from_the_score() -> None:
    """A high average can conceal a missing hard requirement. So: two figures."""
    result = scoring.compute_score(
        [
            (_requirement(1, order=0, must_have=True), N),
            (_requirement(1, order=1, must_have=True), M),
            (_requirement(8, order=2), M),
        ]
    )

    assert result.score == 90  # 9 of 10 by weight
    assert result.must_have_coverage == Decimal("0.50000000")  # 1 of 2 must-haves


def test_no_must_haves_means_no_coverage_figure() -> None:
    """Null, not 0 and not 1: there is no ratio to report."""
    result = scoring.compute_score([(_requirement(1), M)])

    assert result.must_have_coverage is None


def test_must_haves_with_no_weight_have_no_coverage_figure() -> None:
    result = scoring.compute_score(
        [(_requirement(0, order=0, must_have=True), M), (_requirement(3, order=1), M)]
    )

    assert result.must_have_coverage is None
    assert result.score == 100


def test_an_unevidenced_must_have_caps_the_band_but_not_the_score() -> None:
    """The guard lowers a label. It never lowers the number and never hides anyone."""
    result = scoring.compute_score(
        [
            (_requirement(1, order=0, must_have=True, text="Security clearance"), N),
            (_requirement(19, order=1), M),
        ]
    )

    assert result.score == 95, "the arithmetic is untouched by the guard"
    assert result.band_raw is RecommendationBand.STRONG_MATCH
    assert result.band is RecommendationBand.REVIEW
    assert result.capped is True
    assert result.capped_by_requirement_id == result.contributions[0].requirement_id


def test_the_guard_names_the_first_unevidenced_must_have_in_display_order() -> None:
    """Deterministic: the same input always blames the same requirement."""
    result = scoring.compute_score(
        [
            (_requirement(1, order=1, must_have=True, text="second"), N),
            (_requirement(1, order=0, must_have=True, text="first"), N),
            (_requirement(18, order=2), M),
        ]
    )

    blamed = next(
        item
        for item in result.contributions
        if item.requirement_id == result.capped_by_requirement_id
    )
    assert blamed.requirement_text == "first"


def test_a_partial_must_have_does_not_trigger_the_guard() -> None:
    """The guard is about absent evidence, not incomplete evidence."""
    result = scoring.compute_score(
        [(_requirement(1, order=0, must_have=True), P), (_requirement(19, order=1), M)]
    )

    assert result.capped is False
    assert result.band is RecommendationBand.STRONG_MATCH


def test_the_guard_never_raises_a_band() -> None:
    """Capping a LOW_MATCH to REVIEW would be the opposite of a guard."""
    result = scoring.compute_score(
        [(_requirement(1, order=0, must_have=True), N), (_requirement(1, order=1), N)]
    )

    assert result.score == 0
    assert result.band_raw is RecommendationBand.LOW_MATCH
    assert result.band is RecommendationBand.LOW_MATCH
    assert result.capped is False


def test_the_guard_can_be_switched_off() -> None:
    """product-spec section 12 requires it to be configurable."""
    from dataclasses import replace

    config = replace(scoring.DEFAULT_CONFIG, must_have_guard=False, version="scoring-test-noguard")
    pairs = [(_requirement(1, order=0, must_have=True), N), (_requirement(19, order=1), M)]

    assert scoring.compute_score(pairs).capped is True
    assert scoring.compute_score(pairs, config=config).capped is False


def test_must_have_changes_the_band_and_coverage_but_never_the_score() -> None:
    """The flag is recruiter metadata; it must not change how evidence counted."""
    plain = scoring.compute_score([(_requirement(1, order=0), N), (_requirement(19, order=1), M)])
    flagged = scoring.compute_score(
        [(_requirement(1, order=0, must_have=True), N), (_requirement(19, order=1), M)]
    )

    assert plain.score == flagged.score == 95
    assert plain.weighted_sum == flagged.weighted_sum
    assert plain.capped is False
    assert flagged.capped is True


def test_the_config_version_travels_with_the_result() -> None:
    assert scoring.compute_score([(_requirement(1), M)]).config_version == "scoring-v1"


# --------------------------------------------------------------------------
# Against the database
# --------------------------------------------------------------------------

pytestmark_db = pytest.mark.requires_db


def _matched_candidate(db: Session, replay_client, *, fixture: str = "cv_alex_rivera"):
    """A confirmed job with a candidate whose verdicts are already stored."""
    job = make_job_with_requirements(db, replay_client)
    candidate, _ = make_candidate_from_fixture(db, job.id, fixture)
    profile_extraction.extract_profile(db, candidate.id, replay_client)
    matching.run_matching(db, candidate.id, replay_client)
    return job, candidate


#: Hand-computed from the bundled fixtures. Nine must-haves at weight 3 and four
#: nice-to-haves at weight 1; verdicts 8 MATCHED, 3 PARTIAL, 2 NO_EVIDENCE.
#: weighted_sum 25.5, total_weight 31, 25.5 / 31 = 0.82258065 -> 82.
EXPECTED_SCORE = 82
EXPECTED_WEIGHTED_SUM = Decimal("25.5")
EXPECTED_TOTAL_WEIGHT = Decimal("31")


@pytest.mark.requires_db
def test_the_stored_score_matches_the_hand_computed_value(
    db_session: Session, replay_client
) -> None:
    _, candidate = _matched_candidate(db_session, replay_client)

    row, breakdown = scoring.score_candidate(db_session, candidate.id)

    assert row.status is ScoreStatus.COMPUTED
    assert row.weighted_sum == EXPECTED_WEIGHTED_SUM
    assert row.total_weight == EXPECTED_TOTAL_WEIGHT
    assert row.score == EXPECTED_SCORE
    assert row.band is RecommendationBand.GOOD_MATCH
    assert row.capped is False
    assert breakdown.score == EXPECTED_SCORE
    assert len(breakdown.contributions) == 13


@pytest.mark.requires_db
def test_every_input_needed_to_reproduce_the_score_is_persisted(
    db_session: Session, replay_client
) -> None:
    """docs/data-model.md section 6: recomputing from stored rows reproduces it."""
    _, candidate = _matched_candidate(db_session, replay_client)
    row, _ = scoring.score_candidate(db_session, candidate.id)

    recomputed = scoring.load_breakdown(db_session, candidate.id)

    assert recomputed.weighted_sum == row.weighted_sum
    assert recomputed.total_weight == row.total_weight
    assert recomputed.score_raw == row.score_raw
    assert recomputed.score == row.score
    assert recomputed.must_have_coverage == row.must_have_coverage
    assert recomputed.band is row.band
    assert recomputed.band_raw is row.band_raw
    assert row.scoring_config_version == "scoring-v1"


@pytest.mark.requires_db
def test_scoring_twice_produces_the_same_number(db_session: Session, replay_client) -> None:
    _, candidate = _matched_candidate(db_session, replay_client)

    first, _ = scoring.score_candidate(db_session, candidate.id)
    first_values = (first.score, first.score_raw, first.weighted_sum, first.total_weight)
    second, _ = scoring.score_candidate(db_session, candidate.id)

    assert (
        second.score,
        second.score_raw,
        second.weighted_sum,
        second.total_weight,
    ) == first_values
    stored = db_session.scalars(select(Score).where(Score.candidate_id == candidate.id)).all()
    assert len(stored) == 1, "one score per candidate, always"


@pytest.mark.requires_db
def test_a_scored_candidate_is_marked_scored(db_session: Session, replay_client) -> None:
    _, candidate = _matched_candidate(db_session, replay_client)

    scoring.score_candidate(db_session, candidate.id)

    db_session.refresh(candidate)
    assert candidate.status is CandidateStatus.SCORED


# --------------------------------------------------------------------------
# The confirmation gate, and verdict completeness
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_an_unconfirmed_requirement_set_cannot_be_scored(
    db_session: Session, replay_client
) -> None:
    """The gate is server-side, and scoring goes through the same accessor."""
    job, candidate = _matched_candidate(db_session, replay_client)
    requirements.unconfirm_requirements(db_session, job.id)

    with pytest.raises(RequirementsNotConfirmedError):
        scoring.score_candidate(db_session, candidate.id)


@pytest.mark.requires_db
def test_a_candidate_without_verdicts_cannot_be_scored(db_session: Session, replay_client) -> None:
    """Scoring a partial verdict set would under-count and look complete."""
    job = make_job_with_requirements(db_session, replay_client)
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")

    with pytest.raises(ConflictError):
        scoring.score_candidate(db_session, candidate.id)


@pytest.mark.requires_db
def test_a_partial_verdict_set_is_refused(db_session: Session, replay_client) -> None:
    _, candidate = _matched_candidate(db_session, replay_client)
    victim = db_session.scalars(
        select(MatchResult).where(MatchResult.candidate_id == candidate.id).limit(1)
    ).one()
    db_session.delete(victim)
    db_session.commit()

    with pytest.raises(ConflictError):
        scoring.score_candidate(db_session, candidate.id)


@pytest.mark.requires_db
def test_an_unknown_candidate_is_a_not_found(db_session: Session) -> None:
    with pytest.raises(NotFoundError):
        scoring.score_candidate(db_session, uuid.uuid4())


# --------------------------------------------------------------------------
# Staleness: a stored score must never outlive its inputs
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_changing_a_weight_invalidates_the_score_and_costs_nothing_to_redo(
    db_session: Session, replay_client
) -> None:
    """The change a recruiter makes most often is the one that is free."""
    job, candidate = _matched_candidate(db_session, replay_client)
    scoring.score_candidate(db_session, candidate.id)

    kubernetes = _requirement_named(db_session, job.id, "Experience with Kubernetes")
    requirements.update_requirement(db_session, kubernetes.id, weight=Decimal("10"))

    assert scoring.get_score(db_session, candidate.id) is None
    db_session.refresh(candidate)
    assert candidate.status is CandidateStatus.EXTRACTED

    # Every verdict survived, so recomputing needs no model call.
    row, _ = scoring.score_candidate(db_session, candidate.id)
    assert row.total_weight == EXPECTED_TOTAL_WEIGHT + 9
    assert row.weighted_sum == EXPECTED_WEIGHTED_SUM


@pytest.mark.requires_db
def test_changing_must_have_invalidates_the_score(db_session: Session, replay_client) -> None:
    job, candidate = _matched_candidate(db_session, replay_client)
    scoring.score_candidate(db_session, candidate.id)

    kubernetes = _requirement_named(db_session, job.id, "Experience with Kubernetes")
    requirements.update_requirement(db_session, kubernetes.id, must_have=True)

    assert scoring.get_score(db_session, candidate.id) is None


@pytest.mark.requires_db
def test_promoting_an_unevidenced_requirement_to_must_have_caps_the_band(
    db_session: Session, replay_client
) -> None:
    """End to end, on real verdicts: the guard fires and the score is unchanged."""
    job, candidate = _matched_candidate(db_session, replay_client)
    kubernetes = _requirement_named(db_session, job.id, "Experience with Kubernetes")
    requirements.update_requirement(db_session, kubernetes.id, must_have=True)

    row, _ = scoring.score_candidate(db_session, candidate.id)

    assert row.score == EXPECTED_SCORE, "the guard never touches the number"
    assert row.band_raw is RecommendationBand.GOOD_MATCH
    assert row.band is RecommendationBand.REVIEW
    assert row.capped is True
    assert row.capped_by_requirement_id == kubernetes.id


@pytest.mark.requires_db
def test_re_running_matching_invalidates_the_score(db_session: Session, replay_client) -> None:
    """A score belongs to the verdicts it was computed from, and to no others."""
    _, candidate = _matched_candidate(db_session, replay_client)
    scoring.score_candidate(db_session, candidate.id)

    matching.run_matching(db_session, candidate.id, replay_client)

    assert scoring.get_score(db_session, candidate.id) is None


@pytest.mark.requires_db
def test_unconfirming_a_job_discards_its_verdicts_and_scores(
    db_session: Session, replay_client
) -> None:
    """The honest, visible price of reopening a confirmed requirement set."""
    job, candidate = _matched_candidate(db_session, replay_client)
    scoring.score_candidate(db_session, candidate.id)

    requirements.unconfirm_requirements(db_session, job.id)

    assert scoring.get_score(db_session, candidate.id) is None
    assert (
        db_session.scalars(
            select(MatchResult).where(MatchResult.candidate_id == candidate.id)
        ).all()
        == []
    )


@pytest.mark.requires_db
def test_unconfirming_a_job_that_was_never_confirmed_destroys_nothing(
    db_session: Session, replay_client
) -> None:
    job = make_job_with_requirements(db_session, replay_client, confirm=False)

    requirements.unconfirm_requirements(db_session, job.id)

    assert len(requirements.list_requirements(db_session, job.id)) == 13


@pytest.mark.requires_db
def test_replacing_the_job_description_invalidates_scores(
    db_session: Session, replay_client
) -> None:
    job, candidate = _matched_candidate(db_session, replay_client)
    scoring.score_candidate(db_session, candidate.id)

    jobs.set_description(
        db_session,
        job.id,
        raw_text="A completely different role with different requirements.",
        source_type=JdSourceType.PASTED,
    )

    assert scoring.get_score(db_session, candidate.id) is None


def _requirement_named(db: Session, job_id: uuid.UUID, text: str) -> Requirement:
    return db.scalars(
        select(Requirement).where(Requirement.job_id == job_id, Requirement.text == text)
    ).one()


# --------------------------------------------------------------------------
# Boundaries: weights, jobs, and sensitive data
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_negative_weight_is_rejected(db_session: Session, replay_client) -> None:
    job, _ = _matched_candidate(db_session, replay_client)
    kubernetes = _requirement_named(db_session, job.id, "Experience with Kubernetes")

    with pytest.raises(ConflictError):
        requirements.update_requirement(db_session, kubernetes.id, weight=Decimal("-1"))


@pytest.mark.requires_db
def test_an_absurd_weight_is_rejected(db_session: Session, replay_client) -> None:
    """One requirement must not be able to make every other one irrelevant."""
    job, _ = _matched_candidate(db_session, replay_client)
    kubernetes = _requirement_named(db_session, job.id, "Experience with Kubernetes")

    with pytest.raises(ConflictError):
        requirements.update_requirement(db_session, kubernetes.id, weight=Decimal("1000"))


@pytest.mark.requires_db
def test_a_score_only_ever_sees_its_own_jobs_requirements(
    db_session: Session, replay_client
) -> None:
    """No cross-job leakage: each job is an island (architecture section 13)."""
    job_a, candidate_a = _matched_candidate(db_session, replay_client)
    job_b, candidate_b = _matched_candidate(db_session, replay_client)

    _, breakdown = scoring.score_candidate(db_session, candidate_a.id)

    own = {item.requirement_id for item in breakdown.contributions}
    other = {item.id for item in requirements.list_requirements(db_session, job_b.id)}
    assert own.isdisjoint(other)
    assert own == {item.id for item in requirements.list_requirements(db_session, job_a.id)}

    # And scoring one candidate leaves the other alone.
    assert scoring.get_score(db_session, candidate_b.id) is None


@pytest.mark.requires_db
def test_scoring_reads_no_sensitive_attribute_and_no_candidate_name(
    db_session: Session, replay_client
) -> None:
    """The scorer reads `requirement` and `match_result`. That is the whole input.

    The bundled CV prints a full personal-details block and the candidate has a
    display name; neither can reach the arithmetic, because neither table the
    scorer queries contains them.
    """
    _, candidate = _matched_candidate(db_session, replay_client)
    db_session.refresh(candidate)
    assert candidate.display_name == "Alex Rivera", "the name exists, elsewhere"

    _, breakdown = scoring.score_candidate(db_session, candidate.id)

    rendered = " ".join(
        f"{item.requirement_text} {item.category.value} {item.weight} {item.verdict.value}"
        for item in breakdown.contributions
    )
    for value in (
        "Alex Rivera",
        "14 March 1994",
        "Fictionalese",
        "Single",
        "12 Invented Lane",
        "alex.rivera@example.invalid",
    ):
        assert value not in rendered


@pytest.mark.requires_db
def test_absence_is_never_rendered_as_a_claim_about_the_candidate(
    db_session: Session, replay_client
) -> None:
    """A NO_EVIDENCE line reports a verdict and a zero. It makes no other claim."""
    _, candidate = _matched_candidate(db_session, replay_client)

    _, breakdown = scoring.score_candidate(db_session, candidate.id)

    absent = [item for item in breakdown.contributions if item.verdict is N]
    assert absent
    for item in absent:
        assert item.verdict_value == Decimal("0")
        assert item.points == Decimal("0")
        lowered = item.requirement_text.lower()
        for forbidden in ("does not have", "lacks", "unqualified", "rejected"):
            assert forbidden not in lowered


@pytest.mark.requires_db
def test_a_weak_candidate_is_scored_and_kept_not_rejected(
    db_session: Session, replay_client
) -> None:
    """Low Match is a label on a visible candidate, never a removal."""
    _, candidate = _matched_candidate(db_session, replay_client, fixture="cv_prompt_injection")

    row, _ = scoring.score_candidate(db_session, candidate.id)

    # 3 (Python MATCHED) + 1.5 (duration PARTIAL) = 4.5 of 31 -> 15.
    assert row.score == 15
    assert row.band is RecommendationBand.LOW_MATCH
    assert row.capped is False, "the guard cannot lower a band that is already lowest"
    db_session.refresh(candidate)
    assert candidate.status is CandidateStatus.SCORED
    assert candidate.failure_reason is None
