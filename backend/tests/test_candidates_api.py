"""CV upload through the real HTTP API, and the processing state it produces.

Covers the workflow a recruiter performs — upload a batch, see what succeeded,
see honestly why anything failed — plus the guarantees underneath it: a batch is
not all-or-nothing, a failure is never reported as a success, and no filesystem
path or stack trace reaches the client.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.enums import CandidateFailureReason, CandidateStatus
from app.models.candidate import Candidate, CandidateDocument, ParsedDocument
from tests.pdf_fixtures import (
    CORRUPTED_PDF,
    EMPTY_BYTES,
    FAKE_PDF_PNG_BYTES,
    image_only_pdf,
    injection_cv_pdf,
    many_pages_pdf,
    multipage_cv_pdf,
    simple_cv_pdf,
)

pytestmark = pytest.mark.requires_db


def _create_job(api: TestClient, title: str = "Senior Backend Engineer") -> str:
    response = api.post("/api/jobs", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _upload(api: TestClient, job_id: str, files: list[tuple[str, bytes]]):
    return api.post(
        f"/api/jobs/{job_id}/candidates",
        files=[("files", (name, data, "application/pdf")) for name, data in files],
    )


# --------------------------------------------------------------------------
# F. Successful workflow
# --------------------------------------------------------------------------


def test_uploading_a_cv_parses_it_and_reports_success(api: TestClient) -> None:
    job_id = _create_job(api)

    response = _upload(api, job_id, [("alex_rivera.pdf", simple_cv_pdf())])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["uploaded"] == 1
    assert body["rejected"] == 0
    result = body["results"][0]
    assert result["accepted"] is True
    assert result["status"] == CandidateStatus.PARSED.value
    assert result["failure_reason"] is None
    assert result["candidate_id"]


def test_a_batch_of_several_files_is_processed(api: TestClient) -> None:
    job_id = _create_job(api)

    response = _upload(
        api,
        job_id,
        [("one.pdf", simple_cv_pdf()), ("two.pdf", multipage_cv_pdf())],
    )

    body = response.json()
    assert body["uploaded"] == 2
    assert all(item["status"] == CandidateStatus.PARSED.value for item in body["results"])


def test_candidate_status_and_document_metadata_are_retrievable(api: TestClient) -> None:
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("alex.pdf", simple_cv_pdf())]).json()["results"][0][
        "candidate_id"
    ]

    response = api.get(f"/api/candidates/{candidate_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == CandidateStatus.PARSED.value
    assert body["document"]["original_filename"] == "alex.pdf"
    assert body["document"]["content_type"] == "application/pdf"
    assert body["document"]["page_count"] == 1
    assert len(body["document"]["file_sha256"]) == 64
    assert body["parsed"]["has_text_layer"] is True
    assert body["parsed"]["parser_name"] == "pypdf"
    # Not implemented in this phase; must be null rather than guessed.
    assert body["parsed"]["language_detected"] is None


def test_extracted_text_and_page_map_are_retrievable(api: TestClient) -> None:
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("priya.pdf", multipage_cv_pdf())]).json()["results"][0][
        "candidate_id"
    ]

    response = api.get(f"/api/candidates/{candidate_id}/text")

    assert response.status_code == 200
    body = response.json()
    assert "Priya Raman" in body["text"]
    assert body["page_count"] == 2
    assert len(body["pages"]) == 2
    # The offsets index the text that was actually returned.
    for page in body["pages"]:
        assert body["text"][page["start"] : page["end"]].strip()


def test_the_candidate_list_shows_every_upload(api: TestClient) -> None:
    job_id = _create_job(api)
    _upload(api, job_id, [("one.pdf", simple_cv_pdf()), ("two.pdf", image_only_pdf())])

    response = api.get(f"/api/jobs/{job_id}/candidates")

    assert response.status_code == 200
    statuses = {item["status"] for item in response.json()["candidates"]}
    assert statuses == {CandidateStatus.PARSED.value, CandidateStatus.FAILED.value}


# --------------------------------------------------------------------------
# A. Rejections — no candidate is created
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "data", "expected_code"),
    [
        ("cv.docx", simple_cv_pdf(), "unsupported_file_type"),
        ("cv.pdf", EMPTY_BYTES, "empty_file"),
        ("cv.pdf", FAKE_PDF_PNG_BYTES, "not_a_pdf"),
        ("cv.pdf", CORRUPTED_PDF, "malformed_pdf"),
    ],
)
def test_invalid_files_are_rejected_without_creating_a_candidate(
    api: TestClient, filename: str, data: bytes, expected_code: str
) -> None:
    job_id = _create_job(api)

    body = _upload(api, job_id, [(filename, data)]).json()

    assert body["rejected"] == 1
    assert body["uploaded"] == 0
    result = body["results"][0]
    assert result["accepted"] is False
    assert result["rejection_code"] == expected_code
    assert result["candidate_id"] is None
    assert api.get(f"/api/jobs/{job_id}/candidates").json()["candidates"] == []


def test_an_oversized_file_is_rejected(api: TestClient, monkeypatch) -> None:
    from app.core.config import Settings

    monkeypatch.setattr(Settings, "max_upload_size_bytes", property(lambda self: 500))
    job_id = _create_job(api)

    body = _upload(api, job_id, [("big.pdf", simple_cv_pdf())]).json()

    assert body["results"][0]["rejection_code"] == "file_too_large"


def test_a_pdf_with_too_many_pages_is_rejected(api: TestClient) -> None:
    job_id = _create_job(api)

    body = _upload(api, job_id, [("long.pdf", many_pages_pdf(25))]).json()

    assert body["results"][0]["rejection_code"] == "too_many_pages"


def test_one_bad_file_does_not_fail_the_batch(api: TestClient) -> None:
    """The rule from architecture section 8, exercised end to end."""
    job_id = _create_job(api)

    body = _upload(
        api,
        job_id,
        [
            ("good.pdf", simple_cv_pdf()),
            ("junk.pdf", FAKE_PDF_PNG_BYTES),
            ("also_good.pdf", multipage_cv_pdf()),
        ],
    ).json()

    assert body["uploaded"] == 2
    assert body["rejected"] == 1
    by_name = {item["filename"]: item for item in body["results"]}
    assert by_name["good.pdf"]["status"] == CandidateStatus.PARSED.value
    assert by_name["also_good.pdf"]["status"] == CandidateStatus.PARSED.value
    assert by_name["junk.pdf"]["accepted"] is False


def test_more_files_than_the_batch_limit_is_refused(api: TestClient) -> None:
    job_id = _create_job(api)
    files = [(f"cv_{index}.pdf", simple_cv_pdf()) for index in range(26)]

    response = _upload(api, job_id, files)

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


# --------------------------------------------------------------------------
# E. Failure states — accepted, but unusable
# --------------------------------------------------------------------------


def test_a_scanned_pdf_fails_with_no_text_layer(api: TestClient) -> None:
    """There is no OCR. This must fail, not produce an empty candidate."""
    job_id = _create_job(api)

    result = _upload(api, job_id, [("scan.pdf", image_only_pdf())]).json()["results"][0]

    assert result["accepted"] is True
    assert result["status"] == CandidateStatus.FAILED.value
    assert result["failure_reason"] == CandidateFailureReason.NO_TEXT_LAYER.value
    assert "OCR" in result["detail"]


def test_a_failed_candidate_records_its_reason_in_the_database(
    api: TestClient, db_session: Session
) -> None:
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("scan.pdf", image_only_pdf())]).json()["results"][0][
        "candidate_id"
    ]

    candidate = db_session.get(Candidate, uuid.UUID(candidate_id))

    assert candidate is not None
    assert candidate.status is CandidateStatus.FAILED
    assert candidate.failure_reason is CandidateFailureReason.NO_TEXT_LAYER
    assert candidate.failure_detail


def test_a_failed_candidate_has_no_parsed_document(api: TestClient, db_session: Session) -> None:
    """Nothing is persisted that would later look like a real, empty CV."""
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("scan.pdf", image_only_pdf())]).json()["results"][0][
        "candidate_id"
    ]

    document = (
        db_session.query(CandidateDocument).filter_by(candidate_id=uuid.UUID(candidate_id)).one()
    )
    parsed = db_session.query(ParsedDocument).filter_by(document_id=document.id).one_or_none()

    assert parsed is None


def test_text_is_unavailable_for_a_failed_candidate(api: TestClient) -> None:
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("scan.pdf", image_only_pdf())]).json()["results"][0][
        "candidate_id"
    ]

    response = api.get(f"/api/candidates/{candidate_id}/text")

    assert response.status_code == 409
    assert "FAILED" in response.json()["message"]


def test_a_successful_candidate_carries_no_failure_information(
    api: TestClient, db_session: Session
) -> None:
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("alex.pdf", simple_cv_pdf())]).json()["results"][0][
        "candidate_id"
    ]

    candidate = db_session.get(Candidate, uuid.UUID(candidate_id))

    assert candidate.status is CandidateStatus.PARSED
    assert candidate.failure_reason is None
    assert candidate.failure_detail is None
    assert candidate.stage_started_at is None


def test_display_name_is_left_unset_in_this_phase(api: TestClient, db_session: Session) -> None:
    """A name is extracted from the CV in Phase 6; inventing one now would be data we made up."""
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("alex_rivera.pdf", simple_cv_pdf())]).json()["results"][
        0
    ]["candidate_id"]

    candidate = db_session.get(Candidate, uuid.UUID(candidate_id))

    assert candidate.display_name is None


# --------------------------------------------------------------------------
# Missing resources
# --------------------------------------------------------------------------


def test_uploading_to_an_unknown_job_returns_404(api: TestClient) -> None:
    response = _upload(api, str(uuid.uuid4()), [("cv.pdf", simple_cv_pdf())])

    assert response.status_code == 404


def test_an_unknown_candidate_returns_404(api: TestClient) -> None:
    assert api.get(f"/api/candidates/{uuid.uuid4()}").status_code == 404
    assert api.get(f"/api/candidates/{uuid.uuid4()}/text").status_code == 404


def test_listing_candidates_for_an_unknown_job_returns_404(api: TestClient) -> None:
    assert api.get(f"/api/jobs/{uuid.uuid4()}/candidates").status_code == 404


def test_uploading_no_files_is_a_validation_error(api: TestClient) -> None:
    job_id = _create_job(api)

    assert api.post(f"/api/jobs/{job_id}/candidates").status_code == 422


# --------------------------------------------------------------------------
# G. Security
# --------------------------------------------------------------------------


def test_a_traversal_filename_cannot_control_the_storage_path(
    api: TestClient, db_session: Session, storage
) -> None:
    job_id = _create_job(api)

    result = _upload(api, job_id, [("../../../../evil.pdf", simple_cv_pdf())]).json()["results"][0]

    document = (
        db_session.query(CandidateDocument)
        .filter_by(candidate_id=uuid.UUID(result["candidate_id"]))
        .one()
    )

    assert ".." not in document.stored_path
    assert "evil" not in document.stored_path
    assert document.original_filename == "evil.pdf"  # kept, sanitized, display only
    # The file really is inside the storage root.
    assert storage.root in storage.resolve(document.stored_path).parents


def test_the_api_never_returns_a_filesystem_path(api: TestClient) -> None:
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("alex.pdf", simple_cv_pdf())]).json()["results"][0][
        "candidate_id"
    ]

    body = api.get(f"/api/candidates/{candidate_id}").text

    assert "stored_path" not in body
    for leak in ("C:\\", "/var/", "/tmp/", "uploads/"):
        assert leak not in body


def test_error_responses_leak_no_internals(api: TestClient) -> None:
    job_id = _create_job(api)

    body = _upload(api, job_id, [("cv.pdf", CORRUPTED_PDF)]).text.lower()

    for leak in ("traceback", "pypdf", "sqlalchemy", "c:\\", "/backend/app", "site-packages"):
        assert leak not in body


def test_cv_content_is_not_written_to_the_application_log(api: TestClient, caplog) -> None:
    """A CV is personal data; it must not end up in a log aggregator."""
    job_id = _create_job(api)

    with caplog.at_level(logging.DEBUG, logger="app"):
        _upload(api, job_id, [("alex.pdf", simple_cv_pdf())])

    logged = " ".join(record.getMessage() for record in caplog.records)
    for content in ("Alex Rivera", "alex.rivera@example.invalid", "Northwind Analytics"):
        assert content not in logged


def test_injection_text_in_a_cv_is_flagged_not_obeyed(api: TestClient) -> None:
    """Phase 5 only reads the file — but the flag has to be raised here."""
    job_id = _create_job(api)
    candidate_id = _upload(api, job_id, [("jordan.pdf", injection_cv_pdf())]).json()["results"][0][
        "candidate_id"
    ]

    detail = api.get(f"/api/candidates/{candidate_id}").json()
    text_body = api.get(f"/api/candidates/{candidate_id}/text").json()

    assert detail["parsed"]["injection_flag_count"] >= 1
    # Flagged, and still present verbatim — never silently removed.
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in text_body["text"]
    assert text_body["injection_flags"][0]["pattern"]


def test_the_uploaded_file_is_stored_byte_for_byte(
    api: TestClient, db_session: Session, storage
) -> None:
    """Integrity: what was received is what is on disk, and the hash proves it."""
    from app.core.hashing import sha256_bytes

    job_id = _create_job(api)
    data = simple_cv_pdf()
    candidate_id = _upload(api, job_id, [("alex.pdf", data)]).json()["results"][0]["candidate_id"]

    document = (
        db_session.query(CandidateDocument).filter_by(candidate_id=uuid.UUID(candidate_id)).one()
    )

    assert document.file_sha256 == sha256_bytes(data)
    assert storage.read(document.stored_path) == data
