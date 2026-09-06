"""The Phase 4 HTTP surface, end to end.

Covers the workflow a recruiter actually performs — create a job, attach a
description, extract, review, edit, confirm — plus the status codes and the
refusals that protect it. Runs in demo mode against recorded fixtures, so no
API key and no network are involved.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.llm.fixtures import fixture_jd_text
from app.services import requirements as requirements_service

pytestmark = pytest.mark.requires_db

BACKEND_JD = fixture_jd_text("jd_backend_engineer")


def _create_job(api: TestClient, title: str = "Senior Backend Engineer") -> str:
    response = api.post("/api/jobs", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _attach_description(api: TestClient, job_id: str, text: str = BACKEND_JD) -> None:
    response = api.put(
        f"/api/jobs/{job_id}/description",
        json={"raw_text": text, "source_type": "PASTED"},
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# The whole workflow
# --------------------------------------------------------------------------


def test_the_full_phase_4_workflow(api: TestClient) -> None:
    """Create -> describe -> extract -> edit -> confirm, over HTTP."""
    job_id = _create_job(api)
    _attach_description(api, job_id)

    extraction = api.post(f"/api/jobs/{job_id}/requirements/extract")
    assert extraction.status_code == 200, extraction.text
    body = extraction.json()
    assert body["source"] == "FIXTURE"
    assert body["attempts"] == 1
    assert len(body["requirements"]) == 13
    assert body["requirements_confirmed_at"] is None

    listing = api.get(f"/api/jobs/{job_id}/requirements")
    assert listing.status_code == 200
    assert len(listing.json()["requirements"]) == 13

    target = listing.json()["requirements"][0]["id"]
    edit = api.patch(f"/api/requirements/{target}", json={"weight": "5.00"})
    assert edit.status_code == 200
    assert float(edit.json()["weight"]) == 5.0

    confirm = api.post(f"/api/jobs/{job_id}/requirements/confirm")
    assert confirm.status_code == 200
    assert confirm.json()["requirements_confirmed_at"] is not None


def test_job_detail_reports_derived_state(api: TestClient) -> None:
    """`Job` stores no status column; these facts are derived on read."""
    job_id = _create_job(api)

    before = api.get(f"/api/jobs/{job_id}").json()
    assert before["has_description"] is False
    assert before["requirement_count"] == 0

    _attach_description(api, job_id)
    api.post(f"/api/jobs/{job_id}/requirements/extract")

    after = api.get(f"/api/jobs/{job_id}").json()
    assert after["has_description"] is True
    assert after["requirement_count"] == 13


def test_jobs_can_be_listed(api: TestClient) -> None:
    job_id = _create_job(api, title="A listed job")

    response = api.get("/api/jobs")

    assert response.status_code == 200
    assert job_id in {item["id"] for item in response.json()}


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": "ok", "unexpected": "field"},
    ],
)
def test_invalid_job_payloads_are_rejected(api: TestClient, payload: dict) -> None:
    assert api.post("/api/jobs", json=payload).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "Valid", "category": "NOT_A_CATEGORY", "must_have": True},
        {"text": "", "category": "EDUCATION", "must_have": True},
        {"text": "Valid", "category": "EDUCATION"},
        {"text": "Valid", "category": "EDUCATION", "must_have": True, "weight": "-1"},
        {"text": "Valid", "category": "EDUCATION", "must_have": True, "weight": "999"},
        {"text": "Valid", "category": "EDUCATION", "must_have": True, "origin": "LLM_EXTRACTED"},
    ],
)
def test_invalid_requirement_payloads_are_rejected(api: TestClient, payload: dict) -> None:
    """A client cannot post an arbitrary enum, an out-of-range weight, or set `origin`."""
    job_id = _create_job(api)

    response = api.post(f"/api/jobs/{job_id}/requirements", json=payload)

    assert response.status_code == 422, response.text


def test_a_client_cannot_choose_a_requirements_origin(api: TestClient) -> None:
    """`origin` is decided by the server: hand-added requirements are HR_ADDED."""
    job_id = _create_job(api)

    response = api.post(
        f"/api/jobs/{job_id}/requirements",
        json={
            "text": "Must hold a driving licence",
            "category": "SOFT_SKILL_OTHER",
            "must_have": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["origin"] == "HR_ADDED"
    assert response.json()["proposed_text"] is None


# --------------------------------------------------------------------------
# Missing resources
# --------------------------------------------------------------------------


def test_unknown_job_returns_404(api: TestClient) -> None:
    response = api.get(f"/api/jobs/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_unknown_requirement_returns_404(api: TestClient) -> None:
    response = api.patch(f"/api/requirements/{uuid.uuid4()}", json={"must_have": False})

    assert response.status_code == 404


def test_missing_description_returns_404(api: TestClient) -> None:
    job_id = _create_job(api)

    assert api.get(f"/api/jobs/{job_id}/description").status_code == 404


def test_a_malformed_uuid_is_a_validation_error(api: TestClient) -> None:
    assert api.get("/api/jobs/not-a-uuid").status_code == 422


# --------------------------------------------------------------------------
# Invalid state transitions
# --------------------------------------------------------------------------


def test_extraction_without_a_description_returns_409(api: TestClient) -> None:
    job_id = _create_job(api)

    response = api.post(f"/api/jobs/{job_id}/requirements/extract")

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_confirming_an_empty_requirement_set_returns_409(api: TestClient) -> None:
    job_id = _create_job(api)

    assert api.post(f"/api/jobs/{job_id}/requirements/confirm").status_code == 409


def test_editing_frozen_fields_after_confirmation_returns_409(api: TestClient) -> None:
    job_id = _create_job(api)
    _attach_description(api, job_id)
    extraction = api.post(f"/api/jobs/{job_id}/requirements/extract").json()
    api.post(f"/api/jobs/{job_id}/requirements/confirm")
    target = extraction["requirements"][0]["id"]

    text_edit = api.patch(f"/api/requirements/{target}", json={"text": "Rewritten"})
    category_edit = api.patch(f"/api/requirements/{target}", json={"category": "PROJECT"})

    assert text_edit.status_code == 409
    assert category_edit.status_code == 409


def test_weight_edits_after_confirmation_still_succeed(api: TestClient) -> None:
    job_id = _create_job(api)
    _attach_description(api, job_id)
    extraction = api.post(f"/api/jobs/{job_id}/requirements/extract").json()
    api.post(f"/api/jobs/{job_id}/requirements/confirm")
    target = extraction["requirements"][0]["id"]

    response = api.patch(f"/api/requirements/{target}", json={"weight": "4.5", "must_have": False})

    assert response.status_code == 200
    assert float(response.json()["weight"]) == 4.5
    assert response.json()["must_have"] is False


def test_adding_or_deleting_after_confirmation_returns_409(api: TestClient) -> None:
    job_id = _create_job(api)
    _attach_description(api, job_id)
    extraction = api.post(f"/api/jobs/{job_id}/requirements/extract").json()
    api.post(f"/api/jobs/{job_id}/requirements/confirm")
    target = extraction["requirements"][0]["id"]

    added = api.post(
        f"/api/jobs/{job_id}/requirements",
        json={"text": "Sneaky extra", "category": "EDUCATION", "must_have": True},
    )
    deleted = api.delete(f"/api/requirements/{target}")

    assert added.status_code == 409
    assert deleted.status_code == 409


def test_re_extraction_after_confirmation_returns_409(api: TestClient) -> None:
    job_id = _create_job(api)
    _attach_description(api, job_id)
    api.post(f"/api/jobs/{job_id}/requirements/extract")
    api.post(f"/api/jobs/{job_id}/requirements/confirm")

    response = api.post(f"/api/jobs/{job_id}/requirements/extract")

    assert response.status_code == 409


def test_unconfirm_reopens_editing(api: TestClient) -> None:
    job_id = _create_job(api)
    _attach_description(api, job_id)
    extraction = api.post(f"/api/jobs/{job_id}/requirements/extract").json()
    api.post(f"/api/jobs/{job_id}/requirements/confirm")
    target = extraction["requirements"][0]["id"]

    unconfirm = api.delete(f"/api/jobs/{job_id}/requirements/confirm")
    edit = api.patch(f"/api/requirements/{target}", json={"text": "Now editable again"})

    assert unconfirm.status_code == 200
    assert unconfirm.json()["requirements_confirmed_at"] is None
    assert edit.status_code == 200
    assert edit.json()["text"] == "Now editable again"


def test_a_deleted_requirement_is_gone(api: TestClient) -> None:
    job_id = _create_job(api)
    _attach_description(api, job_id)
    extraction = api.post(f"/api/jobs/{job_id}/requirements/extract").json()
    target = extraction["requirements"][0]["id"]

    deleted = api.delete(f"/api/requirements/{target}")

    assert deleted.status_code == 204
    remaining = api.get(f"/api/jobs/{job_id}/requirements").json()["requirements"]
    assert target not in {item["id"] for item in remaining}


# --------------------------------------------------------------------------
# Extraction failure surfaces honestly
# --------------------------------------------------------------------------


def test_unusable_model_output_returns_502(api: TestClient) -> None:
    """A bad upstream reply is 502, not a 500 and not a silent success."""
    job_id = _create_job(api)
    _attach_description(api, job_id, text=fixture_jd_text("jd_unrecoverable_attempt1"))

    response = api.post(f"/api/jobs/{job_id}/requirements/extract")

    assert response.status_code == 502
    assert response.json()["code"] == "extraction_failed"
    assert api.get(f"/api/jobs/{job_id}/requirements").json()["requirements"] == []


def test_a_missing_fixture_returns_503_in_demo_mode(api: TestClient) -> None:
    """Demo mode never silently reaches for the provider."""
    job_id = _create_job(api)
    _attach_description(api, job_id, text="A job description never recorded as a fixture.")

    response = api.post(f"/api/jobs/{job_id}/requirements/extract")

    assert response.status_code == 503
    assert response.json()["code"] == "llm_unavailable"


def test_error_responses_leak_nothing_internal(api: TestClient) -> None:
    """No stack traces, no SQL, no file paths (architecture section 8)."""
    job_id = _create_job(api)
    _attach_description(api, job_id, text=fixture_jd_text("jd_unrecoverable_attempt1"))

    body = api.post(f"/api/jobs/{job_id}/requirements/extract").text.lower()

    for leak in ("traceback", "sqlalchemy", "select ", "c:\\", "/backend/app", "psycopg"):
        assert leak not in body


# --------------------------------------------------------------------------
# The gate, observed through the API
# --------------------------------------------------------------------------


def test_the_api_never_reports_an_unconfirmed_set_as_confirmed(
    api: TestClient, db_session: Session
) -> None:
    """The listing is honest about the gate's state, and the gate agrees."""
    from app.core.errors import RequirementsNotConfirmedError

    job_id = _create_job(api)
    _attach_description(api, job_id)
    api.post(f"/api/jobs/{job_id}/requirements/extract")

    listing = api.get(f"/api/jobs/{job_id}/requirements").json()
    assert listing["requirements_confirmed_at"] is None
    assert len(listing["requirements"]) == 13

    # And the downstream accessor refuses, on the same data.
    with pytest.raises(RequirementsNotConfirmedError):
        requirements_service.get_confirmed_requirements(db_session, uuid.UUID(job_id))
