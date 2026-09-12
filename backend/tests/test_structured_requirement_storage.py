"""The database half of the structured-criteria contract (ADR-0012).

Two things only the database can answer, and both were unverified until this
file existed:

* a `NEEDS_REVIEW` verdict can actually be **written**. The Python enum gained
  the value first; without the matching `ALTER TYPE`, every structured screening
  run would have failed at the point of saving its result.
* a requirement row cannot be stored **half** structured. The CHECK constraint
  is what stops a GPA criterion with no threshold, or a skill criterion with no
  subject, from reaching the matcher as something it has to guess about.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import (
    MatchMethod,
    MatchVerdict,
    RequirementCategory,
    RequirementOrigin,
    RequirementSpecType,
)
from app.models.candidate import Candidate
from app.models.evaluation import MatchResult
from app.models.job import Job, Requirement

pytestmark = pytest.mark.requires_db


def _job(db: Session) -> Job:
    job = Job(title="Structured criteria job")
    db.add(job)
    db.flush()
    return job


def _candidate(db: Session, job: Job) -> Candidate:
    candidate = Candidate(job_id=job.id)
    db.add(candidate)
    db.flush()
    return candidate


def _requirement(job: Job, **overrides) -> Requirement:
    values: dict = {
        "job_id": job.id,
        "text": "Skill: Python",
        "category": RequirementCategory.TECHNICAL_SKILL,
        "must_have": True,
        "weight": Decimal("3"),
        "display_order": 0,
        "origin": RequirementOrigin.HR_ADDED,
    }
    values.update(overrides)
    return Requirement(**values)


# --------------------------------------------------------------------------
# The new verdict has to survive a round trip
# --------------------------------------------------------------------------


def test_a_needs_review_verdict_can_be_stored_and_read_back(db_session: Session) -> None:
    """The enum value exists in PostgreSQL, not only in Python."""
    job = _job(db_session)
    requirement = _requirement(job, spec_type=RequirementSpecType.SKILL, subject="Svelte")
    db_session.add(requirement)
    db_session.flush()

    result = MatchResult(
        candidate_id=_candidate(db_session, job).id,
        requirement_id=requirement.id,
        verdict=MatchVerdict.NEEDS_REVIEW,
        raw_verdict=MatchVerdict.NEEDS_REVIEW,
        # NOTE: `MatchMethod` has no value for the structured engine yet. Wiring
        # `structured_match` into the pipeline will have to decide whether it
        # reuses the existing DETERMINISTIC_* values or gains one of its own.
        decided_by=MatchMethod.DETERMINISTIC_EXACT,
        downgraded=False,
        reason="'Svelte' is not in the skills this screener can evaluate.",
    )
    db_session.add(result)
    db_session.flush()

    stored = db_session.get(MatchResult, result.id)
    assert stored is not None
    assert stored.verdict is MatchVerdict.NEEDS_REVIEW


# --------------------------------------------------------------------------
# A row is wholly structured or wholly legacy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        (
            "skill without a subject",
            {"spec_type": RequirementSpecType.SKILL},
        ),
        (
            "education without a subject",
            {"spec_type": RequirementSpecType.EDUCATION_MIN},
        ),
        (
            "language without a subject",
            {"spec_type": RequirementSpecType.LANGUAGE_PRESENT},
        ),
        (
            "gpa without a threshold",
            {"spec_type": RequirementSpecType.GPA_MIN},
        ),
        (
            "experience without a threshold",
            {"spec_type": RequirementSpecType.EXPERIENCE_MIN},
        ),
        (
            "a skill carrying a numeric threshold it has no use for",
            {
                "spec_type": RequirementSpecType.SKILL,
                "subject": "Python",
                "threshold_value": Decimal("24"),
            },
        ),
        (
            "a scale on something that is not a grade",
            {
                "spec_type": RequirementSpecType.EXPERIENCE_MIN,
                "threshold_value": Decimal("24"),
                "threshold_scale": Decimal("4.00"),
            },
        ),
        (
            "structured values with no type to interpret them",
            {"subject": "Python"},
        ),
    ],
)
def test_half_structured_rows_are_refused(db_session: Session, label: str, overrides: dict) -> None:
    job = _job(db_session)
    db_session.add(_requirement(job, **overrides))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("a legacy free-text requirement", {}),
        (
            "a skill",
            {"spec_type": RequirementSpecType.SKILL, "subject": "Python"},
        ),
        (
            "a degree",
            {"spec_type": RequirementSpecType.EDUCATION_MIN, "subject": "S1"},
        ),
        (
            "a language",
            {"spec_type": RequirementSpecType.LANGUAGE_PRESENT, "subject": "English"},
        ),
        (
            "a grade with its scale",
            {
                "spec_type": RequirementSpecType.GPA_MIN,
                "threshold_value": Decimal("3.00"),
                "threshold_scale": Decimal("4.00"),
            },
        ),
        # Permitted here on purpose, and the reason is asserted rather than
        # asserted-about: see the test below, which stores exactly this row and
        # checks the matcher refuses to answer it.
        (
            "a grade without a scale, which the matcher flags for review",
            {"spec_type": RequirementSpecType.GPA_MIN, "threshold_value": Decimal("3.00")},
        ),
        (
            "an experience minimum",
            {"spec_type": RequirementSpecType.EXPERIENCE_MIN, "threshold_value": Decimal("24")},
        ),
        (
            "an internship minimum",
            {"spec_type": RequirementSpecType.INTERNSHIP_MIN, "threshold_value": Decimal("6")},
        ),
        (
            "internship presence, where no threshold is a legitimate criterion",
            {"spec_type": RequirementSpecType.INTERNSHIP_MIN},
        ),
    ],
)
def test_well_formed_rows_are_accepted(db_session: Session, label: str, overrides: dict) -> None:
    job = _job(db_session)
    requirement = _requirement(job, **overrides)
    db_session.add(requirement)
    db_session.flush()
    assert requirement.id is not None


def test_a_negative_threshold_is_refused(db_session: Session) -> None:
    job = _job(db_session)
    db_session.add(
        _requirement(
            job,
            spec_type=RequirementSpecType.EXPERIENCE_MIN,
            threshold_value=Decimal("-1"),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_zero_scale_is_refused(db_session: Session) -> None:
    """Dividing a grade by nothing is not a scale anyone meant."""
    job = _job(db_session)
    db_session.add(
        _requirement(
            job,
            spec_type=RequirementSpecType.GPA_MIN,
            threshold_value=Decimal("3.00"),
            threshold_scale=Decimal("0"),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_structured_and_legacy_requirements_coexist_on_one_job(db_session: Session) -> None:
    """The migration adds a generation without evicting the previous one."""
    job = _job(db_session)
    db_session.add(_requirement(job, text="Strong experience with Python", display_order=0))
    db_session.add(
        _requirement(
            job,
            text="Skill: Python",
            display_order=1,
            spec_type=RequirementSpecType.SKILL,
            subject="Python",
        )
    )
    db_session.flush()

    rows = db_session.query(Requirement).filter(Requirement.job_id == job.id).all()
    assert len(rows) == 2
    assert {row.spec_type for row in rows} == {None, RequirementSpecType.SKILL}


def test_the_spec_type_vocabulary_matches_the_matcher(db_session: Session) -> None:
    """The database's closed set and the engine's must not drift apart."""
    from app.services import structured_match

    assert {item.value for item in RequirementSpecType} == set(structured_match.SPEC_TYPES)


def test_a_stored_grade_with_no_scale_is_unanswerable_rather_than_matched(
    db_session: Session,
) -> None:
    """The reason the CHECK constraint may stay permissive about the scale.

    The constraint deliberately allows a GPA criterion whose scale is missing,
    on the grounds that the matcher will decline to answer it. That was a claim
    about behaviour and nothing verified it -- and it was false: the comparison
    silently adopted whichever scale the CV stated, so a 3.2 out of 5 satisfied
    a minimum of 3.00 written thinking of 4. Over-crediting, from an assumption
    nobody made.

    This test is the link between the two halves. It stores the row the schema
    permits, reads it back the way `matching` does, and asserts the engine
    refuses it -- so the permissive constraint and the safe behaviour cannot
    drift apart again.
    """
    from app.services import cv_facts, matching, structured_match

    job = _job(db_session)
    row = _requirement(
        job,
        text="Minimum GPA: 3.00",
        category=RequirementCategory.EDUCATION,
        spec_type=RequirementSpecType.GPA_MIN,
        threshold_value=Decimal("3.00"),
    )
    db_session.add(row)
    db_session.flush()

    spec = matching.spec_of(row)
    assert spec is not None and spec.scale is None

    facts = cv_facts.extract_facts("EDUCATION\nGPA: 3.2 / 5.0\n", as_of_year=2026)
    result = structured_match.match(spec, facts)

    assert result.verdict is MatchVerdict.NEEDS_REVIEW
    assert result.verdict is not MatchVerdict.MATCHED
