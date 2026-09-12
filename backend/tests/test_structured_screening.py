"""The structured screening path, end to end, with no model anywhere in it.

This is the product ADR-0012 describes: six typed criteria, a CV, and a ranked
result whose every verdict cites the document. The distinguishing property
asserted here is not accuracy -- that belongs to the unit tests -- but that the
whole path runs **without a `CandidateProfile` and without an `LlmClient`**,
because neither is needed once requirements arrive already structured.

The CV is invented. Names and employers are fictional.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.enums import (
    MatchMethod,
    MatchVerdict,
    RecommendationBand,
    ScoreStatus,
)
from app.services import jobs, matching, ranking, requirements, scoring, structured_match
from app.services.structured_match import RequirementSpec
from tests.factories import make_parsed_candidate

pytestmark = pytest.mark.requires_db

STRONG_CV = """Ada Lovelace
Senior Backend Engineer

EXPERIENCE
Northwind Analytics (fictional) - Backend Engineer, January 2023 - Present
Developed and operated REST services in production.
Cobalt Systems (fictional) - Software Engineering Intern, June 2022 - December 2022
Maintained reporting tools and their test suites.

SKILLS
Python, FastAPI, Postgres, Docker, pytest

LANGUAGES
English, Indonesian

EDUCATION
BSc Computer Science, University of the Fictional Midlands, 2022
IPK: 3,62 / 4.00
"""

THIN_CV = """Sam Okonkwo
Operations Analyst

EXPERIENCE
Harbourline Retail (fictional) - Analyst, January 2024 - January 2025
Built weekly reporting for regional inventory planning.

SKILLS
Excel, SQL, Tableau
"""


def _six_criteria(db: Session, job_id) -> None:
    """One of each supported criterion type, as the criteria builder would."""
    for spec, must_have in [
        (RequirementSpec(structured_match.EDUCATION_MIN, subject="S1"), True),
        (
            RequirementSpec(
                structured_match.GPA_MIN,
                threshold_value=Decimal("3.00"),
                scale=Decimal("4.00"),
            ),
            True,
        ),
        (
            RequirementSpec(structured_match.EXPERIENCE_MIN, threshold_value=Decimal("24")),
            True,
        ),
        (RequirementSpec(structured_match.SKILL, subject="Python"), True),
        (
            RequirementSpec(structured_match.INTERNSHIP_MIN, threshold_value=Decimal("6")),
            False,
        ),
        (RequirementSpec(structured_match.LANGUAGE_PRESENT, subject="English"), False),
    ]:
        requirements.add_structured_requirement(db, job_id, spec=spec, must_have=must_have)


def _job_with_criteria(db: Session):
    job = jobs.create_job(db, title="Backend Engineer")
    _six_criteria(db, job.id)
    requirements.confirm_requirements(db, job.id)
    db.refresh(job)
    return job


# --------------------------------------------------------------------------
# The path itself
# --------------------------------------------------------------------------


def test_screening_runs_with_no_profile_and_no_model(db_session: Session) -> None:
    """The headline claim: structured criteria need neither."""
    job = _job_with_criteria(db_session)
    candidate, _ = make_parsed_candidate(db_session, job.id, STRONG_CV, filename="ada.pdf")

    # `client=None` would be used if anything reached for a model. Nothing does.
    outcome = matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]

    assert outcome.llm_count == 0
    assert outcome.llm_call_id is None
    assert outcome.deterministic_count == 6
    assert len(outcome.results) == 6


def test_every_criterion_is_decided_by_the_structured_engine(db_session: Session) -> None:
    job = _job_with_criteria(db_session)
    candidate, _ = make_parsed_candidate(db_session, job.id, STRONG_CV, filename="ada.pdf")
    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]

    rows = matching.list_match_results(db_session, candidate.id)
    assert {row.result.decided_by for row in rows} == {MatchMethod.DETERMINISTIC_STRUCTURED}


def test_a_strong_cv_matches_every_criterion(db_session: Session) -> None:
    job = _job_with_criteria(db_session)
    candidate, _ = make_parsed_candidate(db_session, job.id, STRONG_CV, filename="ada.pdf")
    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]

    rows = matching.list_match_results(db_session, candidate.id)
    assert all(row.result.verdict is MatchVerdict.MATCHED for row in rows), [
        (row.requirement.text, row.result.verdict) for row in rows
    ]


def test_every_positive_verdict_cites_a_line_of_the_cv(db_session: Session) -> None:
    """Evidence-first, verified against the stored document (ADR-0002)."""
    job = _job_with_criteria(db_session)
    candidate, parsed = make_parsed_candidate(db_session, job.id, STRONG_CV, filename="ada.pdf")
    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]

    for row in matching.list_match_results(db_session, candidate.id):
        if row.result.verdict is MatchVerdict.NO_EVIDENCE:
            continue
        assert row.span is not None, row.requirement.text
        assert row.span.quoted_text in parsed.full_text
        assert row.result.downgraded is False


def test_absence_is_reported_as_absence_not_as_a_claim(db_session: Session) -> None:
    job = _job_with_criteria(db_session)
    candidate, _ = make_parsed_candidate(db_session, job.id, THIN_CV, filename="sam.pdf")
    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]

    absent = [
        row
        for row in matching.list_match_results(db_session, candidate.id)
        if row.result.verdict is MatchVerdict.NO_EVIDENCE
    ]
    assert absent
    for row in absent:
        assert row.span is None
        assert row.result.reason.startswith("The CV")
        assert "candidate" not in row.result.reason.lower()


# --------------------------------------------------------------------------
# NEEDS_REVIEW travels all the way to the score
# --------------------------------------------------------------------------


def test_a_grade_with_no_scale_reaches_the_recruiter_as_needs_review(
    db_session: Session,
) -> None:
    job = jobs.create_job(db_session, title="Fresh graduate intake")
    requirements.add_structured_requirement(
        db_session,
        job.id,
        spec=RequirementSpec(
            structured_match.GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00")
        ),
        must_have=True,
    )
    requirements.add_structured_requirement(
        db_session,
        job.id,
        spec=RequirementSpec(structured_match.SKILL, subject="Python"),
        must_have=True,
    )
    requirements.confirm_requirements(db_session, job.id)

    cv = "EDUCATION\nIPK 3.40\n\nSKILLS\nPython, Docker\n"
    candidate, _ = make_parsed_candidate(db_session, job.id, cv, filename="unscaled.pdf")
    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]

    verdicts = {
        row.requirement.spec_type: row.result.verdict
        for row in matching.list_match_results(db_session, candidate.id)
    }
    from app.core.enums import RequirementSpecType

    assert verdicts[RequirementSpecType.GPA_MIN] is MatchVerdict.NEEDS_REVIEW
    assert verdicts[RequirementSpecType.SKILL] is MatchVerdict.MATCHED

    _, breakdown = scoring.score_candidate(db_session, candidate.id)
    # The unresolved criterion is excluded from both sides: one matched
    # requirement out of one scoreable requirement is 100.
    assert breakdown.status is ScoreStatus.COMPUTED
    assert breakdown.score == 100
    assert breakdown.review_flag is True
    # ...but a must-have nobody could read must not look like a clean pass.
    assert breakdown.band is RecommendationBand.REVIEW
    assert breakdown.band_raw is RecommendationBand.STRONG_MATCH


def test_all_unresolved_gives_an_undefined_score_not_a_zero(db_session: Session) -> None:
    job = jobs.create_job(db_session, title="Unanswerable")
    requirements.add_structured_requirement(
        db_session,
        job.id,
        spec=RequirementSpec(
            structured_match.GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00")
        ),
        must_have=True,
    )
    requirements.confirm_requirements(db_session, job.id)

    cv = "EDUCATION\nIPK 3.40\n"
    candidate, _ = make_parsed_candidate(db_session, job.id, cv, filename="only.pdf")
    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]

    _, breakdown = scoring.score_candidate(db_session, candidate.id)
    assert breakdown.status is ScoreStatus.UNDEFINED_NO_DECIDABLE
    assert breakdown.score is None


# --------------------------------------------------------------------------
# Score reconstructibility and ranking
# --------------------------------------------------------------------------


def test_a_stored_score_can_be_rebuilt_from_its_verdicts(db_session: Session) -> None:
    """ADR-0008: the number follows from rows a recruiter can inspect."""
    job = _job_with_criteria(db_session)
    candidate, _ = make_parsed_candidate(db_session, job.id, STRONG_CV, filename="ada.pdf")
    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]

    stored, computed = scoring.score_candidate(db_session, candidate.id)
    rebuilt = scoring.load_breakdown(db_session, candidate.id)

    assert rebuilt is not None
    assert rebuilt.score == computed.score == stored.score
    assert rebuilt.score_raw == computed.score_raw
    assert [item.verdict for item in rebuilt.contributions] == [
        item.verdict for item in computed.contributions
    ]


def test_screening_is_repeatable(db_session: Session) -> None:
    job = _job_with_criteria(db_session)
    candidate, _ = make_parsed_candidate(db_session, job.id, STRONG_CV, filename="ada.pdf")

    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]
    first = {
        row.requirement.text: (row.result.verdict, row.span and row.span.quoted_text)
        for row in matching.list_match_results(db_session, candidate.id)
    }
    matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]
    second = {
        row.requirement.text: (row.result.verdict, row.span and row.span.quoted_text)
        for row in matching.list_match_results(db_session, candidate.id)
    }
    assert first == second


def test_a_stronger_cv_outranks_a_thinner_one(db_session: Session) -> None:
    job = _job_with_criteria(db_session)
    strong, _ = make_parsed_candidate(db_session, job.id, STRONG_CV, filename="ada.pdf")
    thin, _ = make_parsed_candidate(db_session, job.id, THIN_CV, filename="sam.pdf")

    for candidate in (strong, thin):
        matching.run_matching(db_session, candidate.id, client=None)  # type: ignore[arg-type]
        scoring.score_candidate(db_session, candidate.id)

    result = ranking.rank_job_candidates(db_session, job.id)
    order = [entry.candidate.id for entry in result.ranked]
    assert order.index(strong.id) < order.index(thin.id)


# --------------------------------------------------------------------------
# The boundary is enforced at creation, not only at match time
# --------------------------------------------------------------------------


def test_an_unsupported_skill_cannot_be_added_as_a_criterion(db_session: Session) -> None:
    from app.core.errors import ConflictError

    job = jobs.create_job(db_session, title="Unsupported")
    with pytest.raises(ConflictError, match="not a skill this screener supports"):
        requirements.add_structured_requirement(
            db_session,
            job.id,
            spec=RequirementSpec(structured_match.SKILL, subject="Svelte"),
            must_have=True,
        )


def test_a_gpa_criterion_requires_its_scale(db_session: Session) -> None:
    """The recruiter states the scale; the engine never assumes one."""
    from app.core.errors import ConflictError

    job = jobs.create_job(db_session, title="No scale")
    with pytest.raises(ConflictError, match="scale"):
        requirements.add_structured_requirement(
            db_session,
            job.id,
            spec=RequirementSpec(structured_match.GPA_MIN, threshold_value=Decimal("3.00")),
            must_have=True,
        )


def test_a_structured_requirement_renders_for_display(db_session: Session) -> None:
    job = jobs.create_job(db_session, title="Display")
    row = requirements.add_structured_requirement(
        db_session,
        job.id,
        spec=RequirementSpec(structured_match.EXPERIENCE_MIN, threshold_value=Decimal("24")),
        must_have=True,
    )
    assert row.text == "At least 2 years of professional experience"
