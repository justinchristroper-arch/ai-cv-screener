"""Ranking: the order, the tie-break, and who is never dropped from the list.

The order is fixed by docs/data-model.md section 9, so these tests assert that
specific order rather than whatever the implementation happens to produce. The
tie-break is tested as a pure function -- no database -- because that is where
determinism has to hold, and the grouped behaviour is tested against real rows
because only a database can produce a failed candidate.

Nothing here touches a language model. Ranking reads stored scores and orders
them; there is no model call in its path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.enums import (
    CandidateFailureReason,
    CandidateStatus,
    RecommendationBand,
    ScoreStatus,
)
from app.core.errors import NotFoundError
from app.llm.fixtures import fixture_cv_text
from app.models.candidate import Candidate
from app.models.evaluation import Score
from app.services import jobs, matching, profile_extraction, ranking, requirements, scoring
from tests.factories import (
    make_candidate_from_fixture,
    make_job_with_requirements,
    make_parsed_candidate,
)

# --------------------------------------------------------------------------
# The tie-break, as a pure function
# --------------------------------------------------------------------------

BASE_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _inputs(
    *,
    score: int | None = 50,
    coverage: str | None = None,
    matched: int = 0,
    minutes: int = 0,
    candidate_id: uuid.UUID | None = None,
) -> ranking.RankingInputs:
    return ranking.RankingInputs(
        candidate_id=candidate_id or uuid.uuid4(),
        created_at=BASE_TIME + timedelta(minutes=minutes),
        score=score,
        must_have_coverage=Decimal(coverage) if coverage is not None else None,
        matched_count=matched,
    )


def _order(*entries: ranking.RankingInputs) -> list[uuid.UUID]:
    return [item.candidate_id for item in ranking.order_candidates(entries)]


def test_a_higher_score_comes_first() -> None:
    low = _inputs(score=40)
    high = _inputs(score=90)

    assert _order(low, high) == [high.candidate_id, low.candidate_id]


def test_an_undefined_score_sorts_last_never_as_zero() -> None:
    """The distinction the scorer makes, carried through to the list.

    If undefined sorted as 0 it would sit below a candidate who genuinely
    scored 0 against real requirements -- and above nothing, which would imply
    the two were compared. They were not.
    """
    undefined = _inputs(score=None)
    zero = _inputs(score=0)
    low = _inputs(score=1)

    assert _order(undefined, zero, low) == [
        low.candidate_id,
        zero.candidate_id,
        undefined.candidate_id,
    ]


def test_two_undefined_scores_still_have_a_stable_order() -> None:
    first = _inputs(score=None, minutes=0)
    second = _inputs(score=None, minutes=5)

    assert _order(second, first) == [first.candidate_id, second.candidate_id]


def test_must_have_coverage_breaks_a_score_tie() -> None:
    weaker = _inputs(score=80, coverage="0.5")
    stronger = _inputs(score=80, coverage="0.9")

    assert _order(weaker, stronger) == [stronger.candidate_id, weaker.candidate_id]


def test_a_null_coverage_sorts_last_among_equal_scores() -> None:
    """Null means the job asked for no must-haves, not that coverage was zero."""
    unknown = _inputs(score=80, coverage=None)
    covered = _inputs(score=80, coverage="0.1")

    assert _order(unknown, covered) == [covered.candidate_id, unknown.candidate_id]


def test_matched_count_breaks_a_score_and_coverage_tie() -> None:
    """Concrete evidence beats accumulated partials at the same score."""
    partials = _inputs(score=75, coverage="0.5", matched=2)
    concrete = _inputs(score=75, coverage="0.5", matched=6)

    assert _order(partials, concrete) == [concrete.candidate_id, partials.candidate_id]


def test_arrival_time_breaks_everything_else() -> None:
    later = _inputs(score=75, coverage="0.5", matched=3, minutes=10)
    earlier = _inputs(score=75, coverage="0.5", matched=3, minutes=0)

    assert _order(later, earlier) == [earlier.candidate_id, later.candidate_id]


def test_the_id_makes_the_order_total() -> None:
    """Identical on every other rule and created in the same instant.

    Without this last term the two would come back in whatever order the
    database felt like, and a recruiter refreshing the page would see them swap.
    """
    first = _inputs(candidate_id=uuid.UUID(int=1))
    second = _inputs(candidate_id=uuid.UUID(int=2))

    assert _order(second, first) == [first.candidate_id, second.candidate_id]
    assert _order(first, second) == [first.candidate_id, second.candidate_id]


def test_the_full_order_against_a_hand_constructed_expectation() -> None:
    """Every rule exercised at once, in the order the data model specifies."""
    top = _inputs(score=95, coverage="1.0", matched=9, minutes=0)
    same_score_better_coverage = _inputs(score=80, coverage="0.9", matched=4, minutes=1)
    same_score_worse_coverage = _inputs(score=80, coverage="0.4", matched=8, minutes=2)
    same_again_more_matched = _inputs(score=60, coverage="0.5", matched=5, minutes=3)
    same_again_fewer_matched = _inputs(score=60, coverage="0.5", matched=1, minutes=4)
    earliest_of_the_identical = _inputs(score=10, coverage="0.0", matched=0, minutes=5)
    latest_of_the_identical = _inputs(score=10, coverage="0.0", matched=0, minutes=6)
    undefined = _inputs(score=None, minutes=7)

    shuffled = [
        undefined,
        same_again_fewer_matched,
        latest_of_the_identical,
        top,
        same_score_worse_coverage,
        earliest_of_the_identical,
        same_score_better_coverage,
        same_again_more_matched,
    ]

    assert [item.candidate_id for item in ranking.order_candidates(shuffled)] == [
        top.candidate_id,
        same_score_better_coverage.candidate_id,
        same_score_worse_coverage.candidate_id,
        same_again_more_matched.candidate_id,
        same_again_fewer_matched.candidate_id,
        earliest_of_the_identical.candidate_id,
        latest_of_the_identical.candidate_id,
        undefined.candidate_id,
    ]


def test_ordering_is_stable_across_repeated_runs_and_input_orders() -> None:
    """Determinism, including through ties, is the point of rule 4."""
    entries = [
        _inputs(
            score=80,
            coverage="0.5",
            matched=3,
            minutes=index % 3,
            candidate_id=uuid.UUID(int=index),
        )
        for index in range(1, 12)
    ]

    expected = [item.candidate_id for item in ranking.order_candidates(entries)]
    assert [item.candidate_id for item in ranking.order_candidates(entries)] == expected
    assert [item.candidate_id for item in ranking.order_candidates(list(reversed(entries)))] == (
        expected
    )


def test_the_ranking_key_reads_nothing_but_the_five_ordering_fields() -> None:
    """A name cannot influence a position, structurally rather than by promise."""
    fields = set(ranking.RankingInputs.__dataclass_fields__)

    assert fields == {
        "candidate_id",
        "created_at",
        "score",
        "must_have_coverage",
        "matched_count",
    }
    for forbidden in ("display_name", "name", "email", "age", "gender", "nationality"):
        assert forbidden not in fields


def test_an_empty_job_ranks_to_an_empty_list() -> None:
    assert ranking.order_candidates([]) == []


# --------------------------------------------------------------------------
# Against the database
# --------------------------------------------------------------------------

pytestmark_db = pytest.mark.requires_db


def _scored_candidate(db: Session, job, replay_client, fixture: str = "cv_alex_rivera"):
    candidate, _ = make_candidate_from_fixture(db, job.id, fixture)
    profile_extraction.extract_profile(db, candidate.id, replay_client)
    matching.run_matching(db, candidate.id, replay_client)
    scoring.score_candidate(db, candidate.id)
    return candidate


@pytest.mark.requires_db
def test_a_scored_candidate_appears_in_the_ranked_group(db_session: Session, replay_client) -> None:
    job = make_job_with_requirements(db_session, replay_client)
    candidate = _scored_candidate(db_session, job, replay_client)

    result = ranking.rank_job_candidates(db_session, job.id)

    assert result.total == 1
    assert [entry.candidate.id for entry in result.ranked] == [candidate.id]
    entry = result.ranked[0]
    assert entry.position == 1
    assert entry.score.score == 82
    assert entry.score.band is RecommendationBand.GOOD_MATCH
    assert entry.matched_count == 8
    assert entry.original_filename == "cv_alex_rivera.pdf"


@pytest.mark.requires_db
def test_a_stronger_candidate_outranks_a_weaker_one(db_session: Session, replay_client) -> None:
    """82 against 15, on the two bundled CVs."""
    job = make_job_with_requirements(db_session, replay_client)
    weak = _scored_candidate(db_session, job, replay_client, "cv_prompt_injection")
    strong = _scored_candidate(db_session, job, replay_client, "cv_alex_rivera")

    result = ranking.rank_job_candidates(db_session, job.id)

    assert [entry.candidate.id for entry in result.ranked] == [strong.id, weak.id]
    assert [entry.position for entry in result.ranked] == [1, 2]
    assert [entry.score.score for entry in result.ranked] == [82, 15]


@pytest.mark.requires_db
def test_the_lowest_scoring_candidate_is_still_in_the_list(
    db_session: Session, replay_client
) -> None:
    """No candidate is filtered out by score, at any point (product-spec 12)."""
    job = make_job_with_requirements(db_session, replay_client)
    weak = _scored_candidate(db_session, job, replay_client, "cv_prompt_injection")

    result = ranking.rank_job_candidates(db_session, job.id)

    assert weak.id in {entry.candidate.id for entry in result.ranked}
    assert result.ranked[-1].score.band is RecommendationBand.LOW_MATCH


@pytest.mark.requires_db
def test_an_unscored_candidate_is_listed_separately_not_dropped(
    db_session: Session, replay_client
) -> None:
    job = make_job_with_requirements(db_session, replay_client)
    scored = _scored_candidate(db_session, job, replay_client)
    pending, _ = make_candidate_from_fixture(db_session, job.id, "cv_prompt_injection")

    result = ranking.rank_job_candidates(db_session, job.id)

    assert [entry.candidate.id for entry in result.ranked] == [scored.id]
    assert [entry.candidate.id for entry in result.not_yet_scored] == [pending.id]
    assert result.failed == []
    assert result.total == 2


@pytest.mark.requires_db
def test_a_failed_candidate_is_listed_with_its_reason(db_session: Session, replay_client) -> None:
    """A file that failed is never hidden from the recruiter who uploaded it."""
    job = make_job_with_requirements(db_session, replay_client)
    _scored_candidate(db_session, job, replay_client)

    broken = Candidate(
        job_id=job.id,
        status=CandidateStatus.FAILED,
        failure_reason=CandidateFailureReason.NO_TEXT_LAYER,
        failure_detail="The PDF contains no extractable text.",
    )
    db_session.add(broken)
    db_session.commit()

    result = ranking.rank_job_candidates(db_session, job.id)

    assert [entry.candidate.id for entry in result.failed] == [broken.id]
    assert ranking.failure_reason_of(result.failed[0]) is CandidateFailureReason.NO_TEXT_LAYER
    assert broken.id not in {entry.candidate.id for entry in result.ranked}


@pytest.mark.requires_db
def test_a_failed_candidate_is_never_merged_into_the_order_even_with_a_score(
    db_session: Session, replay_client
) -> None:
    """A number attached to a broken pipeline is worse than no number."""
    job = make_job_with_requirements(db_session, replay_client)
    candidate = _scored_candidate(db_session, job, replay_client)

    candidate.status = CandidateStatus.FAILED
    candidate.failure_reason = CandidateFailureReason.MATCHING_FAILED
    db_session.commit()

    result = ranking.rank_job_candidates(db_session, job.id)

    assert result.ranked == []
    assert [entry.candidate.id for entry in result.failed] == [candidate.id]


@pytest.mark.requires_db
def test_an_undefined_score_ranks_last_against_real_ones(
    db_session: Session, replay_client
) -> None:
    job = make_job_with_requirements(db_session, replay_client)
    scored = _scored_candidate(db_session, job, replay_client)
    undefined_candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_prompt_injection")
    db_session.add(
        Score(
            candidate_id=undefined_candidate.id,
            status=ScoreStatus.UNDEFINED_NO_WEIGHT,
            capped=False,
            scoring_config_version="scoring-v1",
        )
    )
    db_session.commit()

    result = ranking.rank_job_candidates(db_session, job.id)

    assert [entry.candidate.id for entry in result.ranked] == [
        scored.id,
        undefined_candidate.id,
    ]
    assert result.ranked[1].score.score is None
    assert ranking.WARNING_SCORE_UNDEFINED in result.ranked[1].warnings


@pytest.mark.requires_db
def test_the_ranking_is_reproducible(db_session: Session, replay_client) -> None:
    job = make_job_with_requirements(db_session, replay_client)
    _scored_candidate(db_session, job, replay_client, "cv_prompt_injection")
    _scored_candidate(db_session, job, replay_client, "cv_alex_rivera")

    first = [entry.candidate.id for entry in ranking.rank_job_candidates(db_session, job.id).ranked]
    second = [
        entry.candidate.id for entry in ranking.rank_job_candidates(db_session, job.id).ranked
    ]

    assert first == second


@pytest.mark.requires_db
def test_renaming_a_candidate_does_not_move_them(db_session: Session, replay_client) -> None:
    """The display name is carried for the reader and read by nothing else."""
    job = make_job_with_requirements(db_session, replay_client)
    _scored_candidate(db_session, job, replay_client, "cv_prompt_injection")
    strong = _scored_candidate(db_session, job, replay_client, "cv_alex_rivera")
    before = [
        entry.candidate.id for entry in ranking.rank_job_candidates(db_session, job.id).ranked
    ]

    strong.display_name = "Zzz Last-Alphabetically"
    db_session.commit()

    after = [entry.candidate.id for entry in ranking.rank_job_candidates(db_session, job.id).ranked]
    assert after == before


# --------------------------------------------------------------------------
# Job isolation
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_ranking_contains_only_its_own_jobs_candidates(
    db_session: Session, replay_client
) -> None:
    """Each job is an island (architecture section 13)."""
    job_a = make_job_with_requirements(db_session, replay_client)
    candidate_a = _scored_candidate(db_session, job_a, replay_client)
    job_b = make_job_with_requirements(db_session, replay_client)
    candidate_b = _scored_candidate(db_session, job_b, replay_client)

    ranking_a = ranking.rank_job_candidates(db_session, job_a.id)
    ranking_b = ranking.rank_job_candidates(db_session, job_b.id)

    assert [entry.candidate.id for entry in ranking_a.ranked] == [candidate_a.id]
    assert [entry.candidate.id for entry in ranking_b.ranked] == [candidate_b.id]
    assert ranking_a.total == ranking_b.total == 1


@pytest.mark.requires_db
def test_an_unknown_job_is_a_not_found(db_session: Session) -> None:
    with pytest.raises(NotFoundError):
        ranking.rank_job_candidates(db_session, uuid.uuid4())


@pytest.mark.requires_db
def test_a_job_with_no_candidates_ranks_to_three_empty_groups(
    db_session: Session,
) -> None:
    job = jobs.create_job(db_session, title="Nobody has applied yet")

    result = ranking.rank_job_candidates(db_session, job.id)

    assert result.ranked == []
    assert result.not_yet_scored == []
    assert result.failed == []
    assert result.total == 0


# --------------------------------------------------------------------------
# Warnings
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_capped_band_is_flagged_as_a_warning(db_session: Session, replay_client) -> None:
    job = make_job_with_requirements(db_session, replay_client)
    candidate = _scored_candidate(db_session, job, replay_client)
    kubernetes = next(
        item
        for item in requirements.list_requirements(db_session, job.id)
        if item.text == "Experience with Kubernetes"
    )
    requirements.update_requirement(db_session, kubernetes.id, must_have=True)
    scoring.score_candidate(db_session, candidate.id)

    result = ranking.rank_job_candidates(db_session, job.id)

    entry = result.ranked[0]
    assert entry.score.capped is True
    assert ranking.WARNING_MUST_HAVE_NOT_EVIDENCED in entry.warnings


@pytest.mark.requires_db
def test_a_cv_with_instruction_like_text_is_flagged(db_session: Session, replay_client) -> None:
    """Phase 5 flagged it; the ranked list is where a recruiter finally sees it."""
    job = make_job_with_requirements(db_session, replay_client)
    _scored_candidate(db_session, job, replay_client, "cv_prompt_injection")

    result = ranking.rank_job_candidates(db_session, job.id)

    assert ranking.WARNING_INSTRUCTION_LIKE_TEXT in result.ranked[0].warnings


@pytest.mark.requires_db
def test_a_cv_laid_out_in_columns_is_flagged(db_session: Session, replay_client) -> None:
    """Parsing recorded the layout; the ranked list is where it reaches a human.

    A flagged candidate is still scored and still ranked on the same terms as
    everyone else. The warning says the reading order could not be relied on --
    it does not withhold a result, and it does not move anyone down the list.
    """
    job = make_job_with_requirements(db_session, replay_client)
    candidate, _ = make_parsed_candidate(
        db_session,
        job.id,
        fixture_cv_text("cv_alex_rivera"),
        filename="cv_alex_rivera.pdf",
        multi_column_pages=[1],
    )
    profile_extraction.extract_profile(db_session, candidate.id, replay_client)
    matching.run_matching(db_session, candidate.id, replay_client)
    scoring.score_candidate(db_session, candidate.id)

    result = ranking.rank_job_candidates(db_session, job.id)

    entry = result.ranked[0]
    assert ranking.WARNING_MULTI_COLUMN_LAYOUT in entry.warnings
    assert entry.score.score == 82, "the warning does not change the score"


@pytest.mark.requires_db
def test_an_ordinary_candidate_carries_no_warnings(db_session: Session, replay_client) -> None:
    job = make_job_with_requirements(db_session, replay_client)
    _scored_candidate(db_session, job, replay_client)

    result = ranking.rank_job_candidates(db_session, job.id)

    assert result.ranked[0].warnings == []


@pytest.mark.requires_db
def test_unconfirming_a_job_moves_everyone_to_not_yet_scored(
    db_session: Session, replay_client
) -> None:
    """Scores are invalidated, so the honest report is "not scored", not a stale number."""
    job = make_job_with_requirements(db_session, replay_client)
    candidate = _scored_candidate(db_session, job, replay_client)

    requirements.unconfirm_requirements(db_session, job.id)

    result = ranking.rank_job_candidates(db_session, job.id)
    assert result.ranked == []
    assert [entry.candidate.id for entry in result.not_yet_scored] == [candidate.id]
