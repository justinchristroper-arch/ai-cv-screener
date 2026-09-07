"""Demo mode: the product has to be showable with no API key and no cost.

The load-bearing test here is the first one. A recorded fixture is keyed by a
hash of its input, so the demo only replays while the committed sample PDFs
still parse to exactly the text those fixtures were recorded against. If a PDF,
the parser or the normalizer drifts, this fails with a clear message instead of
the demo failing later with a missing-fixture error nobody can trace.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.enums import CandidateStatus, RecommendationBand
from app.core.errors import ConflictError
from app.llm.fixtures import fixture_cv_text, fixture_jd_text
from app.services import demo, ranking
from app.services.document_parsing import extract_text

# --------------------------------------------------------------------------
# The samples still match the fixtures they are replayed against
# --------------------------------------------------------------------------


def test_every_sample_cv_is_present() -> None:
    missing = [cv.filename for cv in demo.SAMPLE_CVS if not cv.path.is_file()]

    assert missing == [], f"missing synthetic samples: {missing}"


@pytest.mark.parametrize(
    ("filename", "fixture"),
    [
        ("alex-rivera-backend-engineer.pdf", "cv_alex_rivera"),
        ("jordan-blake-injected-instructions.pdf", "cv_prompt_injection"),
    ],
)
def test_a_sample_cv_parses_to_exactly_its_fixture_text(filename: str, fixture: str) -> None:
    """Drift here breaks the demo silently. Catch it loudly instead."""
    pdf = (demo.SAMPLE_DIR / filename).read_bytes()

    assert extract_text(pdf).full_text == fixture_cv_text(fixture)


def test_the_scanned_sample_really_has_no_text_layer() -> None:
    """It is meant to fail, and seeing it fail is part of the demonstration."""
    from app.services.document_parsing import PdfExtractionError

    pdf = (demo.SAMPLE_DIR / "scanned-no-text-layer.pdf").read_bytes()

    with pytest.raises(PdfExtractionError):
        extract_text(pdf)


def test_the_sample_job_description_is_not_duplicated() -> None:
    """One copy, read from the fixture, so it cannot drift away from it."""
    assert demo.get_samples().job_description == fixture_jd_text("jd_backend_engineer")


def test_the_demo_job_is_labelled_as_synthetic() -> None:
    assert demo.get_samples().job_title.startswith("[Demo]")


# --------------------------------------------------------------------------
# Seeding is refused outside demo mode
# --------------------------------------------------------------------------


def test_seeding_is_refused_when_demo_mode_is_off() -> None:
    """An endpoint that manufactures candidate records must not exist in live mode."""
    with pytest.raises(ConflictError) as excinfo:
        demo.require_demo_mode(False)

    assert "DEMO_MODE" in str(excinfo.value)


def test_seeding_is_allowed_in_demo_mode() -> None:
    demo.require_demo_mode(True)  # does not raise


@pytest.mark.requires_db
def test_the_service_refuses_to_seed_outside_demo_mode(
    db_session: Session, replay_client, storage
) -> None:
    """The guard lives in the service, so no caller can go around it."""
    with pytest.raises(ConflictError):
        demo.seed_demo_job(
            db_session,
            replay_client,
            storage=storage,
            demo_mode=False,
            max_size_bytes=10 * 1024 * 1024,
            max_pages=20,
            max_files=25,
        )


# --------------------------------------------------------------------------
# A seeded job, end to end
# --------------------------------------------------------------------------


@pytest.fixture()
def seeded(db_session: Session, replay_client, storage):
    return demo.seed_demo_job(
        db_session,
        replay_client,
        storage=storage,
        demo_mode=True,
        max_size_bytes=10 * 1024 * 1024,
        max_pages=20,
        max_files=25,
    )


@pytest.mark.requires_db
def test_seeding_produces_a_confirmed_job_with_requirements(db_session: Session, seeded) -> None:
    from app.services import requirements

    assert seeded.job.requirements_confirmed_at is not None
    assert len(requirements.list_requirements(db_session, seeded.job.id)) == 13


@pytest.mark.requires_db
def test_seeding_screens_the_parseable_candidates_and_fails_the_scan(
    db_session: Session, seeded
) -> None:
    assert seeded.uploaded == 3
    assert seeded.rejected == 0
    assert seeded.screened == 2
    assert seeded.failed == 1


@pytest.mark.requires_db
def test_the_seeded_job_ranks_the_way_the_samples_describe(db_session: Session, seeded) -> None:
    """The demo a reader sees: a strong match, a flagged one, and an honest failure."""
    result = ranking.rank_job_candidates(db_session, seeded.job.id)

    assert [entry.score.score for entry in result.ranked] == [82, 15]
    assert [entry.score.band for entry in result.ranked] == [
        RecommendationBand.GOOD_MATCH,
        RecommendationBand.LOW_MATCH,
    ]
    assert ranking.WARNING_INSTRUCTION_LIKE_TEXT in result.ranked[1].warnings
    assert len(result.failed) == 1
    assert result.failed[0].candidate.status is CandidateStatus.FAILED
    assert result.failed[0].candidate.failure_reason is not None


@pytest.mark.requires_db
def test_the_seeded_job_stores_no_sensitive_attribute(db_session: Session, seeded) -> None:
    """The strong sample prints a personal-details block. None of it is stored."""
    from app.services import profile_extraction

    result = ranking.rank_job_candidates(db_session, seeded.job.id)
    top = result.ranked[0].candidate
    profile = profile_extraction.require_profile(db_session, top.id)
    items = profile_extraction.load_profile_items(db_session, profile)

    stored = " | ".join(
        [
            *(f"{s.raw_name} {s.normalized_name}" for s in items.skills),
            *(f"{e.role_title} {e.organization} {e.description}" for e in items.experience),
            *(f"{e.degree} {e.field_of_study} {e.institution}" for e in items.education),
            *(span.quoted_text for span in items.spans.values()),
        ]
    )
    for value in (
        "14 March 1994",
        "Fictionalese",
        "Marital status",
        "12 Invented Lane",
        "alex.rivera@example.invalid",
    ):
        assert value not in stored


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_the_samples_endpoint_describes_the_bundled_data(api: TestClient) -> None:
    body = api.get("/api/demo/samples").json()

    assert body["demo_mode"] is True
    assert body["job_title"].startswith("[Demo]")
    assert "Northwind Analytics" in body["job_description"]
    assert len(body["cvs"]) == 3
    assert all(cv["filename"].endswith(".pdf") for cv in body["cvs"])
    assert all(cv["demonstrates"] for cv in body["cvs"])


@pytest.mark.requires_db
def test_seeding_over_http_returns_a_browsable_job(api: TestClient) -> None:
    response = api.post("/api/demo/jobs")

    assert response.status_code == 201
    body = response.json()
    assert body["job_title"].startswith("[Demo]")
    assert (body["uploaded"], body["screened"], body["failed"]) == (3, 2, 1)

    ranked = api.get(f"/api/jobs/{body['job_id']}/ranking").json()
    assert ranked["summary"] == {"total": 3, "ranked": 2, "not_yet_scored": 0, "failed": 1}
    assert [row["score"] for row in ranked["ranked"]] == [82, 15]


@pytest.mark.requires_db
def test_the_demo_response_exposes_no_filesystem_path(api: TestClient) -> None:
    body = api.get("/api/demo/samples").text

    assert "data/sample" not in body
    assert "var/uploads" not in body
    assert "C:\\\\" not in body
