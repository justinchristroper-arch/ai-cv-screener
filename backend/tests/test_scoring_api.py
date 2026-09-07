"""The two scoring endpoints, over HTTP.

Routes are thin, so these tests are about the contract: the right status codes,
the confirmation gate refusing from behind the API as well as in front of it, a
breakdown a recruiter can add up by eye, and nothing in a response that should
not be there -- no ranking, no hiring verdict, no filesystem path, no sensitive
attribute.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Requirement
from app.services import matching, profile_extraction, requirements
from tests.factories import make_candidate_from_fixture, make_job_with_requirements

pytestmark = pytest.mark.requires_db


@pytest.fixture()
def matched(db_session: Session, replay_client):
    """A confirmed job with a candidate whose verdicts are already stored."""
    job = make_job_with_requirements(db_session, replay_client)
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")
    profile_extraction.extract_profile(db_session, candidate.id, replay_client)
    matching.run_matching(db_session, candidate.id, replay_client)
    return job, candidate


# --------------------------------------------------------------------------
# Computing a score
# --------------------------------------------------------------------------


def test_scoring_returns_the_number_and_its_arithmetic(api: TestClient, matched) -> None:
    _, candidate = matched

    response = api.post(f"/api/candidates/{candidate.id}/score")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "COMPUTED"
    assert body["score"] == 82
    assert Decimal(body["weighted_sum"]) == Decimal("25.5")
    assert Decimal(body["total_weight"]) == Decimal("31")
    assert body["band"] == "GOOD_MATCH"
    assert body["capped"] is False
    assert body["scoring_config_version"] == "scoring-v1"
    assert len(body["contributions"]) == 13


def test_the_contributions_add_up_to_the_stated_total(api: TestClient, matched) -> None:
    """The whole point of the breakdown: a recruiter can check it by hand."""
    _, candidate = matched

    body = api.post(f"/api/candidates/{candidate.id}/score").json()

    points = sum(Decimal(item["points"]) for item in body["contributions"])
    weight = sum(Decimal(item["weight"]) for item in body["contributions"])
    assert points == Decimal(body["weighted_sum"])
    assert weight == Decimal(body["total_weight"])

    for item in body["contributions"]:
        assert Decimal(item["points"]) == Decimal(item["weight"]) * Decimal(item["verdict_value"])


def test_each_verdict_maps_to_its_specified_value(api: TestClient, matched) -> None:
    _, candidate = matched
    expected = {"MATCHED": Decimal("1.0"), "PARTIAL": Decimal("0.5"), "NO_EVIDENCE": Decimal("0")}

    body = api.post(f"/api/candidates/{candidate.id}/score").json()

    for item in body["contributions"]:
        assert Decimal(item["verdict_value"]) == expected[item["verdict"]]


def test_contributions_are_returned_in_display_order(api: TestClient, matched) -> None:
    _, candidate = matched

    body = api.post(f"/api/candidates/{candidate.id}/score").json()

    assert [item["display_order"] for item in body["contributions"]] == list(range(13))


def test_the_score_can_be_retrieved_again(api: TestClient, matched) -> None:
    _, candidate = matched
    created = api.post(f"/api/candidates/{candidate.id}/score").json()

    fetched = api.get(f"/api/candidates/{candidate.id}/score").json()

    assert fetched["score"] == created["score"]
    assert fetched["weighted_sum"] == created["weighted_sum"]
    assert fetched["contributions"] == created["contributions"]


def test_rescoring_reproduces_the_same_number(api: TestClient, matched) -> None:
    _, candidate = matched
    first = api.post(f"/api/candidates/{candidate.id}/score").json()

    second = api.post(f"/api/candidates/{candidate.id}/score").json()

    assert second["score"] == first["score"]
    assert second["score_raw"] == first["score_raw"]
    assert second["contributions"] == first["contributions"]


def test_must_have_coverage_is_reported_alongside_the_score(api: TestClient, matched) -> None:
    _, candidate = matched

    body = api.post(f"/api/candidates/{candidate.id}/score").json()

    # Nine must-haves at weight 3: 24 points of 27.
    assert Decimal(body["must_have_coverage"]).quantize(Decimal("0.0001")) == Decimal("0.8889")


# --------------------------------------------------------------------------
# The must-have guard, over HTTP
# --------------------------------------------------------------------------


def test_an_unevidenced_must_have_caps_the_band_and_names_itself(
    api: TestClient, db_session: Session, matched
) -> None:
    job, candidate = matched
    kubernetes = db_session.scalars(
        select(Requirement).where(
            Requirement.job_id == job.id, Requirement.text == "Experience with Kubernetes"
        )
    ).one()
    requirements.update_requirement(db_session, kubernetes.id, must_have=True)

    body = api.post(f"/api/candidates/{candidate.id}/score").json()

    assert body["score"] == 82, "the guard lowers a label, never the number"
    assert body["band_raw"] == "GOOD_MATCH"
    assert body["band"] == "REVIEW"
    assert body["capped"] is True
    assert body["capped_by_requirement_id"] == str(kubernetes.id)
    assert body["capped_by_requirement_text"] == "Experience with Kubernetes"


# --------------------------------------------------------------------------
# The gate and other refusals
# --------------------------------------------------------------------------


def test_scoring_before_confirmation_is_refused(
    api: TestClient, db_session: Session, matched
) -> None:
    """Enforced server-side, not merely discouraged in a UI."""
    job, candidate = matched
    requirements.unconfirm_requirements(db_session, job.id)

    response = api.post(f"/api/candidates/{candidate.id}/score")

    assert response.status_code == 409
    assert response.json()["code"] == "requirements_not_confirmed"


def test_scoring_without_verdicts_is_refused(
    api: TestClient, db_session: Session, replay_client
) -> None:
    job = make_job_with_requirements(db_session, replay_client)
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")

    response = api.post(f"/api/candidates/{candidate.id}/score")

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert "Run matching" in response.json()["message"]


def test_a_score_that_was_never_computed_is_a_404(api: TestClient, matched) -> None:
    _, candidate = matched

    response = api.get(f"/api/candidates/{candidate.id}/score")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_an_unknown_candidate_is_a_404(api: TestClient) -> None:
    assert api.post(f"/api/candidates/{uuid.uuid4()}/score").status_code == 404
    assert api.get(f"/api/candidates/{uuid.uuid4()}/score").status_code == 404


def test_a_stale_score_is_gone_rather_than_served(
    api: TestClient, db_session: Session, matched
) -> None:
    """Changing a weight removes the old number instead of serving it."""
    job, candidate = matched
    api.post(f"/api/candidates/{candidate.id}/score")
    kubernetes = db_session.scalars(
        select(Requirement).where(
            Requirement.job_id == job.id, Requirement.text == "Experience with Kubernetes"
        )
    ).one()

    requirements.update_requirement(db_session, kubernetes.id, weight=Decimal("10"))

    assert api.get(f"/api/candidates/{candidate.id}/score").status_code == 404
    rescored = api.post(f"/api/candidates/{candidate.id}/score").json()
    assert Decimal(rescored["total_weight"]) == Decimal("40")


def test_a_negative_weight_is_rejected_at_the_boundary(
    api: TestClient, db_session: Session, matched
) -> None:
    """422 from the schema, before any service or the database sees it."""
    job, _ = matched
    kubernetes = db_session.scalars(
        select(Requirement).where(
            Requirement.job_id == job.id, Requirement.text == "Experience with Kubernetes"
        )
    ).one()

    response = api.patch(f"/api/requirements/{kubernetes.id}", json={"weight": -5})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# What the response must not contain
# --------------------------------------------------------------------------


def test_the_score_response_carries_no_ranking_or_hiring_verdict(api: TestClient, matched) -> None:
    """This milestone stops at a score, and the API says so."""
    _, candidate = matched

    body = api.post(f"/api/candidates/{candidate.id}/score").text

    for field in ("rank", "position", "shortlist", "hire", "reject", "recommendation"):
        assert f'"{field}"' not in body


def test_the_score_response_carries_no_sensitive_attribute_or_name(
    api: TestClient, matched
) -> None:
    _, candidate = matched

    body = api.post(f"/api/candidates/{candidate.id}/score").text

    for value in (
        "Alex Rivera",
        "14 March 1994",
        "Fictionalese",
        "Marital status",
        "12 Invented Lane",
        "alex.rivera@example.invalid",
    ):
        assert value not in body


def test_the_score_response_leaks_no_path_or_key(api: TestClient, matched) -> None:
    _, candidate = matched

    body = api.post(f"/api/candidates/{candidate.id}/score").text

    assert "stored_path" not in body
    assert "var/uploads" not in body
    assert "api_key" not in body.lower()


def test_the_endpoints_are_documented(api: TestClient) -> None:
    paths = api.get("/openapi.json").json()["paths"]

    assert set(paths["/api/candidates/{candidate_id}/score"]) == {"post", "get"}
    description = paths["/api/candidates/{candidate_id}/score"]["post"]["description"]
    assert "never rejects, hides or filters anyone" in description
