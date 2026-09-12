"""How an unresolved criterion is reported, over HTTP.

ADR-0012 added a fourth verdict, and the point of adding it was that a
recruiter should be able to tell three different situations apart:

* the CV evidences the criterion;
* the CV shows nothing for it -- a fact about the document;
* the CV shows something the screener could not read safely -- a fact about
  the screener.

The third is the new one, and it is worth nothing unless it survives the trip
to the screen. These tests follow it there: into the score response, where it
must appear as an excluded line rather than a zero, and into the ranking, where
the warning must say which of the two reasons capped a band.

Every CV here is invented.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import jobs, matching, requirements, scoring, structured_match
from app.services.ranking import (
    WARNING_CRITERIA_UNRESOLVED,
    WARNING_MUST_HAVE_NOT_EVIDENCED,
    WARNING_MUST_HAVE_UNRESOLVED,
    WARNING_NOTHING_DECIDABLE,
)
from app.services.structured_match import RequirementSpec
from tests.factories import make_parsed_candidate

pytestmark = pytest.mark.requires_db

#: A grade with no scale is the cleanest source of a genuine NEEDS_REVIEW: the
#: document really does say something, and 3.40 is strong out of 4 and ordinary
#: out of 5, so no reading of it is safe.
UNSCALED_GPA_CV = """Rana Idris
Graduate Trainee

EDUCATION
BSc Computer Science, University of the Fictional Coast, 2024
IPK 3.40

SKILLS
Python, Docker, SQL
"""

NO_GRADE_CV = """Tomas Bergen
Graduate Trainee

EDUCATION
BSc Computer Science, University of the Fictional Coast, 2024

SKILLS
Python, Docker, SQL
"""


def _gpa() -> RequirementSpec:
    return RequirementSpec(
        structured_match.GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00")
    )


def _screen(db: Session, *, specs, cv: str, filename: str = "cv.pdf"):
    """A confirmed job, one candidate, matched and scored. No model involved."""
    job = jobs.create_job(db, title="Graduate intake")
    for spec, must_have, weight in specs:
        requirements.add_structured_requirement(
            db, job.id, spec=spec, must_have=must_have, weight=weight
        )
    requirements.confirm_requirements(db, job.id)

    candidate, _ = make_parsed_candidate(db, job.id, cv, filename=filename)
    matching.run_matching(db, candidate.id, client=None)  # type: ignore[arg-type]
    scoring.score_candidate(db, candidate.id)
    return job, candidate


# --------------------------------------------------------------------------
# The score response
# --------------------------------------------------------------------------


def test_an_unresolved_criterion_is_an_excluded_line_not_a_zero(
    api: TestClient, db_session: Session
) -> None:
    _, candidate = _screen(
        db_session,
        specs=[
            (_gpa(), False, Decimal("4")),
            (RequirementSpec(structured_match.SKILL, subject="Python"), False, Decimal("6")),
        ],
        cv=UNSCALED_GPA_CV,
    )

    body = api.get(f"/api/candidates/{candidate.id}/score").json()

    lines = {item["verdict"]: item for item in body["contributions"]}
    assert set(lines) == {"NEEDS_REVIEW", "MATCHED"}

    unresolved = lines["NEEDS_REVIEW"]
    # Null rather than 0.0: a zero would be a value in the arithmetic, and this
    # line takes no part in it at all.
    assert unresolved["verdict_value"] is None
    assert unresolved["points"] is None
    assert Decimal(unresolved["weight"]) == Decimal("4")

    # ...and its weight is absent from the denominator, not redistributed: the
    # matched criterion is still worth exactly the 6 the recruiter gave it.
    assert Decimal(body["total_weight"]) == Decimal("6")
    assert Decimal(body["weighted_sum"]) == Decimal("6")
    assert body["score"] == 100


def test_the_score_says_how_much_of_the_job_it_covers(api: TestClient, db_session: Session) -> None:
    """A 100 over one of two criteria must not read like a 100 over two."""
    _, candidate = _screen(
        db_session,
        specs=[
            (_gpa(), True, None),
            (RequirementSpec(structured_match.SKILL, subject="Python"), False, None),
        ],
        cv=UNSCALED_GPA_CV,
    )

    body = api.get(f"/api/candidates/{candidate.id}/score").json()

    assert body["review_flag"] is True
    assert body["needs_review_count"] == 1
    assert body["must_have_needs_review_count"] == 1
    assert len(body["contributions"]) == 2


def test_a_clean_score_carries_no_review_flag(api: TestClient, db_session: Session) -> None:
    _, candidate = _screen(
        db_session,
        specs=[(RequirementSpec(structured_match.SKILL, subject="Python"), True, None)],
        cv=UNSCALED_GPA_CV,
    )

    body = api.get(f"/api/candidates/{candidate.id}/score").json()

    assert body["review_flag"] is False
    assert body["needs_review_count"] == 0
    assert body["must_have_needs_review_count"] == 0
    assert body["contributions"][0]["verdict_value"] is not None


def test_nothing_decidable_is_reported_as_undefined_rather_than_zero(
    api: TestClient, db_session: Session
) -> None:
    _, candidate = _screen(
        db_session,
        specs=[(_gpa(), True, None)],
        cv=UNSCALED_GPA_CV,
    )

    body = api.get(f"/api/candidates/{candidate.id}/score").json()

    assert body["status"] == "UNDEFINED_NO_DECIDABLE"
    assert body["score"] is None
    assert body["band"] is None
    assert body["total_weight"] is None
    assert body["review_flag"] is True
    # Not capped: there is no band to cap. The guard is about a number that
    # exists and would read too well, and here there is no number at all.
    assert body["capped"] is False


# --------------------------------------------------------------------------
# The ranking
# --------------------------------------------------------------------------


def _ranked_row(api: TestClient, job_id) -> dict:
    body = api.get(f"/api/jobs/{job_id}/ranking").json()
    assert len(body["ranked"]) == 1, body
    return body["ranked"][0]


def test_the_ranking_says_when_a_number_covers_less_than_the_criteria_list(
    api: TestClient, db_session: Session
) -> None:
    job, _ = _screen(
        db_session,
        specs=[
            (_gpa(), False, None),
            (RequirementSpec(structured_match.SKILL, subject="Python"), False, None),
        ],
        cv=UNSCALED_GPA_CV,
    )

    row = _ranked_row(api, job.id)

    assert row["needs_review_count"] == 1
    assert WARNING_CRITERIA_UNRESOLVED in row["warnings"]
    assert WARNING_NOTHING_DECIDABLE not in row["warnings"]


def test_the_ranking_says_when_nothing_at_all_could_be_decided(
    api: TestClient, db_session: Session
) -> None:
    job, _ = _screen(
        db_session,
        specs=[(_gpa(), False, None)],
        cv=UNSCALED_GPA_CV,
    )

    row = _ranked_row(api, job.id)

    assert row["score"] is None
    assert WARNING_NOTHING_DECIDABLE in row["warnings"]
    # The more specific line replaces the general one rather than joining it.
    assert WARNING_CRITERIA_UNRESOLVED not in row["warnings"]


def test_a_cap_names_uncertainty_rather_than_absence_when_that_is_the_cause(
    api: TestClient, db_session: Session
) -> None:
    """The two reasons for a capped band mean opposite things about the CV."""
    job, _ = _screen(
        db_session,
        specs=[
            (_gpa(), True, Decimal("1")),
            (RequirementSpec(structured_match.SKILL, subject="Python"), False, Decimal("9")),
        ],
        cv=UNSCALED_GPA_CV,
    )

    row = _ranked_row(api, job.id)

    assert row["band_raw"] == "STRONG_MATCH"
    assert row["band"] == "REVIEW"
    assert row["capped"] is True
    assert WARNING_MUST_HAVE_UNRESOLVED in row["warnings"]
    assert WARNING_MUST_HAVE_NOT_EVIDENCED not in row["warnings"]


def test_a_cap_still_names_absence_when_the_cv_genuinely_shows_nothing(
    api: TestClient, db_session: Session
) -> None:
    """The original guard, unchanged. Absence takes precedence over uncertainty."""
    job, _ = _screen(
        db_session,
        specs=[
            (
                RequirementSpec(structured_match.LANGUAGE_PRESENT, subject="Japanese"),
                True,
                Decimal("1"),
            ),
            (RequirementSpec(structured_match.SKILL, subject="Python"), False, Decimal("9")),
        ],
        cv=NO_GRADE_CV,
    )

    row = _ranked_row(api, job.id)

    assert row["capped"] is True
    assert WARNING_MUST_HAVE_NOT_EVIDENCED in row["warnings"]
    assert WARNING_MUST_HAVE_UNRESOLVED not in row["warnings"]


def test_an_ordinary_result_carries_none_of_the_review_warnings(
    api: TestClient, db_session: Session
) -> None:
    job, _ = _screen(
        db_session,
        specs=[(RequirementSpec(structured_match.SKILL, subject="Python"), True, None)],
        cv=NO_GRADE_CV,
    )

    row = _ranked_row(api, job.id)

    assert row["needs_review_count"] == 0
    assert row["warnings"] == []


# --------------------------------------------------------------------------
# The audit trail
# --------------------------------------------------------------------------


def test_structured_verdicts_are_reported_as_decided_by_code(
    api: TestClient, db_session: Session
) -> None:
    """ "Decided by code" is a claim, and it has to stay true.

    The count is derived from a list of methods, and the structured engine was
    a new method. A list that has not kept up reports every structured verdict
    as the model's work -- the opposite of what happened, on the one screen a
    recruiter would use to check.
    """
    _, candidate = _screen(
        db_session,
        specs=[
            (RequirementSpec(structured_match.SKILL, subject="Python"), True, None),
            (RequirementSpec(structured_match.EDUCATION_MIN, subject="S1"), False, None),
        ],
        cv=NO_GRADE_CV,
    )

    summary = api.get(f"/api/candidates/{candidate.id}/matches").json()["summary"]

    assert summary["total"] == 2
    assert summary["decided_deterministically"] == 2
    assert summary["decided_by_model"] == 0
