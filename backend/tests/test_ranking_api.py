"""The ranked-list endpoint, over HTTP.

The contract, not the arithmetic: the grouped shape, the documented order, the
absence of anything that could hide a candidate, and the absence of anything
sensitive in a response a whole team will read.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.enums import CandidateFailureReason, CandidateStatus
from app.models.candidate import Candidate
from app.services import matching, profile_extraction, requirements, scoring
from tests.factories import make_candidate_from_fixture, make_job_with_requirements

pytestmark = pytest.mark.requires_db


def _scored(db: Session, job, replay_client, fixture: str = "cv_alex_rivera"):
    candidate, _ = make_candidate_from_fixture(db, job.id, fixture)
    profile_extraction.extract_profile(db, candidate.id, replay_client)
    matching.run_matching(db, candidate.id, replay_client)
    scoring.score_candidate(db, candidate.id)
    return candidate


@pytest.fixture()
def job(db_session: Session, replay_client):
    return make_job_with_requirements(db_session, replay_client)


# --------------------------------------------------------------------------
# Shape and order
# --------------------------------------------------------------------------


def test_the_ranking_returns_three_groups_and_a_summary(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    _scored(db_session, job, replay_client)

    response = api.get(f"/api/jobs/{job.id}/ranking")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "job_id",
        "job_title",
        "requirements_confirmed_at",
        "ranked",
        "not_yet_scored",
        "failed",
        "summary",
    }
    assert body["summary"] == {"total": 1, "ranked": 1, "not_yet_scored": 0, "failed": 0}


def test_candidates_come_back_in_score_order(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    weak = _scored(db_session, job, replay_client, "cv_prompt_injection")
    strong = _scored(db_session, job, replay_client, "cv_alex_rivera")

    ranked = api.get(f"/api/jobs/{job.id}/ranking").json()["ranked"]

    assert [item["candidate_id"] for item in ranked] == [str(strong.id), str(weak.id)]
    assert [item["position"] for item in ranked] == [1, 2]
    assert [item["score"] for item in ranked] == [82, 15]


def test_every_row_carries_what_placed_it_there(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    """A recruiter can see why one candidate sits above another."""
    _scored(db_session, job, replay_client)

    row = api.get(f"/api/jobs/{job.id}/ranking").json()["ranked"][0]

    assert row["score"] == 82
    assert row["band"] == "GOOD_MATCH"
    assert row["band_raw"] == "GOOD_MATCH"
    assert row["capped"] is False
    assert row["score_status"] == "COMPUTED"
    assert Decimal(row["must_have_coverage"]).quantize(Decimal("0.0001")) == Decimal("0.8889")
    assert row["matched_count"] == 8
    assert row["scoring_config_version"] == "scoring-v1"
    assert row["display_name"] == "Alex Rivera"
    assert row["original_filename"] == "cv_alex_rivera.pdf"


def test_repeated_requests_return_an_identical_list(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    _scored(db_session, job, replay_client, "cv_prompt_injection")
    _scored(db_session, job, replay_client, "cv_alex_rivera")

    first = api.get(f"/api/jobs/{job.id}/ranking").json()["ranked"]
    second = api.get(f"/api/jobs/{job.id}/ranking").json()["ranked"]

    assert first == second


# --------------------------------------------------------------------------
# Nobody disappears
# --------------------------------------------------------------------------


def test_an_unscored_candidate_is_reported_not_dropped(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    _scored(db_session, job, replay_client)
    pending, _ = make_candidate_from_fixture(db_session, job.id, "cv_prompt_injection")

    body = api.get(f"/api/jobs/{job.id}/ranking").json()

    assert [item["candidate_id"] for item in body["not_yet_scored"]] == [str(pending.id)]
    assert body["not_yet_scored"][0]["status"] == "PARSED"
    assert body["summary"] == {"total": 2, "ranked": 1, "not_yet_scored": 1, "failed": 0}


def test_a_failed_candidate_is_reported_with_its_reason(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    _scored(db_session, job, replay_client)
    broken = Candidate(
        job_id=job.id,
        status=CandidateStatus.FAILED,
        failure_reason=CandidateFailureReason.NO_TEXT_LAYER,
        failure_detail="The PDF contains no extractable text.",
    )
    db_session.add(broken)
    db_session.commit()

    body = api.get(f"/api/jobs/{job.id}/ranking").json()

    assert [item["candidate_id"] for item in body["failed"]] == [str(broken.id)]
    assert body["failed"][0]["failure_reason"] == "NO_TEXT_LAYER"
    assert body["failed"][0]["failure_detail"]
    assert body["summary"]["failed"] == 1


def test_the_weakest_candidate_is_returned_not_filtered(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    weak = _scored(db_session, job, replay_client, "cv_prompt_injection")

    ranked = api.get(f"/api/jobs/{job.id}/ranking").json()["ranked"]

    assert [item["candidate_id"] for item in ranked] == [str(weak.id)]
    assert ranked[0]["band"] == "LOW_MATCH"


def test_the_endpoint_takes_no_parameter_that_could_hide_a_candidate(
    api: TestClient,
) -> None:
    """No limit, no offset, no threshold, no top-K. By design, and asserted."""
    operation = api.get("/openapi.json").json()["paths"]["/api/jobs/{job_id}/ranking"]["get"]

    names = {parameter["name"] for parameter in operation.get("parameters", [])}
    assert names == {"job_id"}


def test_extra_query_parameters_do_not_change_the_result(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    _scored(db_session, job, replay_client, "cv_prompt_injection")
    _scored(db_session, job, replay_client, "cv_alex_rivera")

    plain = api.get(f"/api/jobs/{job.id}/ranking").json()
    with_params = api.get(f"/api/jobs/{job.id}/ranking?limit=1&top=1&min_score=90").json()

    assert with_params == plain
    assert len(with_params["ranked"]) == 2


# --------------------------------------------------------------------------
# Warnings, isolation, and what must not be in the response
# --------------------------------------------------------------------------


def test_warnings_are_surfaced_next_to_the_score(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    _scored(db_session, job, replay_client, "cv_prompt_injection")

    row = api.get(f"/api/jobs/{job.id}/ranking").json()["ranked"][0]

    assert "INSTRUCTION_LIKE_TEXT_IN_CV" in row["warnings"]


def test_a_capped_band_is_surfaced_as_a_warning(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    candidate = _scored(db_session, job, replay_client)
    kubernetes = next(
        item
        for item in requirements.list_requirements(db_session, job.id)
        if item.text == "Experience with Kubernetes"
    )
    requirements.update_requirement(db_session, kubernetes.id, must_have=True)
    scoring.score_candidate(db_session, candidate.id)

    row = api.get(f"/api/jobs/{job.id}/ranking").json()["ranked"][0]

    assert row["capped"] is True
    assert row["band"] == "REVIEW"
    assert row["band_raw"] == "GOOD_MATCH"
    assert "MUST_HAVE_NOT_EVIDENCED" in row["warnings"]


def test_one_jobs_ranking_never_contains_another_jobs_candidates(
    api: TestClient, db_session: Session, replay_client
) -> None:
    job_a = make_job_with_requirements(db_session, replay_client)
    candidate_a = _scored(db_session, job_a, replay_client)
    job_b = make_job_with_requirements(db_session, replay_client)
    candidate_b = _scored(db_session, job_b, replay_client)

    body_a = api.get(f"/api/jobs/{job_a.id}/ranking").json()
    body_b = api.get(f"/api/jobs/{job_b.id}/ranking").json()

    assert [item["candidate_id"] for item in body_a["ranked"]] == [str(candidate_a.id)]
    assert [item["candidate_id"] for item in body_b["ranked"]] == [str(candidate_b.id)]
    assert body_a["job_id"] == str(job_a.id)
    assert body_b["job_id"] == str(job_b.id)


def test_an_unknown_job_is_a_404(api: TestClient) -> None:
    response = api.get(f"/api/jobs/{uuid.uuid4()}/ranking")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_the_response_carries_no_sensitive_attribute_or_path(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    _scored(db_session, job, replay_client)

    body = api.get(f"/api/jobs/{job.id}/ranking").text

    for value in (
        "14 March 1994",
        "Fictionalese",
        "Marital status",
        "12 Invented Lane",
        "alex.rivera@example.invalid",
        "stored_path",
        "var/uploads",
    ):
        assert value not in body


def test_the_response_carries_no_hiring_decision(
    api: TestClient, db_session: Session, job, replay_client
) -> None:
    """A ranking orders a worklist. It does not close one."""
    _scored(db_session, job, replay_client)

    body = api.get(f"/api/jobs/{job.id}/ranking").text

    for field in ("shortlist", "hire", "reject", "advance", "recommendation", "decision"):
        assert f'"{field}"' not in body


def test_the_endpoint_is_documented_as_unfiltered(api: TestClient) -> None:
    operation = api.get("/openapi.json").json()["paths"]["/api/jobs/{job_id}/ranking"]["get"]

    assert "Nothing is filtered" in operation["description"]
    assert "sorts last rather than as a zero" in operation["description"]
