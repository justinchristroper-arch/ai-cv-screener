"""The four candidate-intelligence endpoints, over HTTP.

Routes are thin by design, so these tests are about the contract rather than
the logic: the right status codes, the confirmation gate refusing from behind
the API as well as in front of it, evidence surfaced with its verification
status, and nothing in a response that should not be there -- no score, no rank,
no filesystem path, no API key.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import requirements
from tests.factories import make_candidate_from_fixture, make_job_with_requirements

pytestmark = pytest.mark.requires_db


@pytest.fixture()
def screened(db_session: Session, replay_client):
    """A confirmed job and a parsed candidate, ready for the endpoints."""
    job = make_job_with_requirements(db_session, replay_client)
    candidate, parsed = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")
    return job, candidate, parsed


# --------------------------------------------------------------------------
# A. Profile
# --------------------------------------------------------------------------


def test_extracting_a_profile_returns_it(api: TestClient, screened) -> None:
    _, candidate, _ = screened

    response = api.post(f"/api/candidates/{candidate.id}/profile")

    assert response.status_code == 201
    body = response.json()
    assert body["cache_hit"] is False
    assert body["attempts"] == 1
    assert body["source"] == "FIXTURE"
    profile = body["profile"]
    assert len(profile["skills"]) == 6
    assert len(profile["experience"]) == 2
    assert len(profile["education"]) == 1
    assert len(profile["projects"]) == 1
    assert profile["evidence_summary"] == {
        "items": 10,
        "with_evidence": 10,
        "verified": 10,
        "unverified": 0,
    }


def test_the_profile_can_be_retrieved_again(api: TestClient, screened) -> None:
    _, candidate, _ = screened
    api.post(f"/api/candidates/{candidate.id}/profile")

    response = api.get(f"/api/candidates/{candidate.id}/profile")

    assert response.status_code == 200
    assert response.json()["candidate_id"] == str(candidate.id)


def test_a_second_extraction_is_served_from_the_cache(api: TestClient, screened) -> None:
    _, candidate, _ = screened
    api.post(f"/api/candidates/{candidate.id}/profile")

    response = api.post(f"/api/candidates/{candidate.id}/profile")

    assert response.status_code == 201
    assert response.json()["cache_hit"] is True
    assert response.json()["attempts"] == 0


def test_every_profile_item_ships_its_evidence(api: TestClient, screened) -> None:
    _, candidate, _ = screened

    profile = api.post(f"/api/candidates/{candidate.id}/profile").json()["profile"]

    for group in ("skills", "experience", "education", "projects"):
        for item in profile[group]:
            evidence = item["evidence"]
            assert evidence is not None, f"{group} item has no evidence"
            assert evidence["verification_status"] == "VERIFIED_EXACT"
            assert evidence["page_number"] == 1
            assert evidence["start_char"] is not None


def test_the_profile_response_carries_no_sensitive_attribute(api: TestClient, screened) -> None:
    """The CV prints a personal-details block. None of it comes back."""
    _, candidate, _ = screened

    body = api.post(f"/api/candidates/{candidate.id}/profile").text

    for value in (
        "14 March 1994",
        "Fictionalese",
        "Marital status",
        "12 Invented Lane",
        "alex.rivera@example.invalid",
    ):
        assert value not in body
    for field in ("date_of_birth", "gender", "nationality", "photo", "home_address"):
        assert f'"{field}"' not in body


def test_the_profile_response_carries_no_score_or_rank(api: TestClient, screened) -> None:
    _, candidate, _ = screened

    body = api.post(f"/api/candidates/{candidate.id}/profile").text

    for field in ("score", "rank", "band", "recommendation"):
        assert f'"{field}"' not in body


def test_a_profile_that_has_not_been_extracted_is_a_404(api: TestClient, screened) -> None:
    _, candidate, _ = screened

    response = api.get(f"/api/candidates/{candidate.id}/profile")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_an_unknown_candidate_is_a_404(api: TestClient) -> None:
    response = api.post(f"/api/candidates/{uuid.uuid4()}/profile")

    assert response.status_code == 404


def test_a_candidate_with_no_parsed_text_is_a_409(
    api: TestClient, db_session: Session, replay_client
) -> None:
    from app.core.enums import CandidateStatus
    from app.models.candidate import Candidate

    job = make_job_with_requirements(db_session, replay_client)
    candidate = Candidate(job_id=job.id, status=CandidateStatus.UPLOADED)
    db_session.add(candidate)
    db_session.commit()

    response = api.post(f"/api/candidates/{candidate.id}/profile")

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


# --------------------------------------------------------------------------
# B. Matching
# --------------------------------------------------------------------------


def test_matching_returns_a_verdict_for_every_requirement(api: TestClient, screened) -> None:
    _, candidate, _ = screened
    api.post(f"/api/candidates/{candidate.id}/profile")

    response = api.post(f"/api/candidates/{candidate.id}/matches")

    assert response.status_code == 201
    body = response.json()
    assert len(body["results"]) == 13
    assert body["summary"] == {
        "total": 13,
        "matched": 8,
        "partial": 3,
        "no_evidence": 2,
        "downgraded": 0,
        "decided_deterministically": 5,
        "decided_by_model": 8,
    }
    assert body["source"] == "FIXTURE"
    assert body["attempts"] == 1


def test_the_results_can_be_retrieved_again(api: TestClient, screened) -> None:
    _, candidate, _ = screened
    api.post(f"/api/candidates/{candidate.id}/profile")
    api.post(f"/api/candidates/{candidate.id}/matches")

    response = api.get(f"/api/candidates/{candidate.id}/matches")

    assert response.status_code == 200
    assert len(response.json()["results"]) == 13


def test_results_come_back_in_the_recruiters_display_order(api: TestClient, screened) -> None:
    _, candidate, _ = screened
    api.post(f"/api/candidates/{candidate.id}/profile")

    results = api.post(f"/api/candidates/{candidate.id}/matches").json()["results"]

    assert [item["display_order"] for item in results] == list(range(13))


def test_a_positive_verdict_ships_the_passage_it_rests_on(api: TestClient, screened) -> None:
    _, candidate, _ = screened
    api.post(f"/api/candidates/{candidate.id}/profile")

    results = api.post(f"/api/candidates/{candidate.id}/matches").json()["results"]

    for item in results:
        if item["verdict"] == "NO_EVIDENCE":
            continue
        assert item["evidence"] is not None, item["requirement_text"]
        assert item["evidence"]["verification_status"] in (
            "VERIFIED_EXACT",
            "VERIFIED_NORMALIZED",
        )
        assert item["evidence"]["quoted_text"]


def test_no_evidence_is_worded_as_a_statement_about_the_document(api: TestClient, screened) -> None:
    _, candidate, _ = screened
    api.post(f"/api/candidates/{candidate.id}/profile")

    results = api.post(f"/api/candidates/{candidate.id}/matches").json()["results"]
    absent = [item for item in results if item["verdict"] == "NO_EVIDENCE"]

    assert absent
    for item in absent:
        assert "No evidence found in the CV" in item["reason"]
        assert "not about the candidate" in item["reason"]


def test_matching_before_confirmation_is_refused_by_the_api(
    api: TestClient, db_session: Session, replay_client
) -> None:
    """The gate is server-side. A client that skips the confirm step is refused."""
    job = make_job_with_requirements(db_session, replay_client, confirm=False)
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")
    api.post(f"/api/candidates/{candidate.id}/profile")

    response = api.post(f"/api/candidates/{candidate.id}/matches")

    assert response.status_code == 409
    assert response.json()["code"] == "requirements_not_confirmed"

    requirements.confirm_requirements(db_session, job.id)
    assert api.post(f"/api/candidates/{candidate.id}/matches").status_code == 201


def test_matching_without_a_profile_is_a_409(api: TestClient, screened) -> None:
    _, candidate, _ = screened

    response = api.post(f"/api/candidates/{candidate.id}/matches")

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_results_for_a_candidate_that_was_never_matched_are_empty(
    api: TestClient, screened
) -> None:
    """An empty list, not a 404: the candidate exists and has no verdicts yet."""
    _, candidate, _ = screened

    response = api.get(f"/api/candidates/{candidate.id}/matches")

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["summary"]["total"] == 0


def test_the_match_response_carries_no_score_band_or_rank(api: TestClient, screened) -> None:
    """This milestone stops at evidence-backed verdicts, and the API says so."""
    _, candidate, _ = screened
    api.post(f"/api/candidates/{candidate.id}/profile")

    body = api.post(f"/api/candidates/{candidate.id}/matches").text

    for field in ("score", "band", "rank", "weighted_sum", "recommendation"):
        assert f'"{field}"' not in body


def test_no_response_leaks_a_filesystem_path_or_a_key(api: TestClient, screened) -> None:
    _, candidate, _ = screened
    profile_body = api.post(f"/api/candidates/{candidate.id}/profile").text
    match_body = api.post(f"/api/candidates/{candidate.id}/matches").text

    for body in (profile_body, match_body):
        assert "stored_path" not in body
        assert "var/uploads" not in body
        assert "C:\\\\" not in body
        assert "api_key" not in body.lower()


def test_the_endpoints_are_documented_in_the_openapi_schema(api: TestClient) -> None:
    paths = api.get("/openapi.json").json()["paths"]

    assert "/api/candidates/{candidate_id}/profile" in paths
    assert "/api/candidates/{candidate_id}/matches" in paths
    assert set(paths["/api/candidates/{candidate_id}/matches"]) == {"post", "get"}
