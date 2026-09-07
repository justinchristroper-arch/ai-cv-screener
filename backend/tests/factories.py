"""Builders for the rows the candidate-intelligence tests need.

Most of these tests are about what happens *after* a PDF has been parsed, so
they create a `parsed_document` directly from fixture text rather than going
through reportlab and pypdf every time. The text is the same text either way --
`test_profile_extraction.py` asserts that the bundled CV fixture matches what
the real parser produces from the real PDF, so this shortcut cannot quietly
drift away from the pipeline it stands in for.

Every name, employer and institution here is invented.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.enums import CandidateStatus, JdSourceType
from app.core.hashing import sha256_bytes, sha256_text
from app.core.text import NORMALIZATION_VERSION
from app.llm.fixtures import fixture_cv_text, fixture_jd_text
from app.models.candidate import Candidate, CandidateDocument, ParsedDocument
from app.models.job import Job
from app.services import document_parsing, jd_extraction, jobs, requirements


def make_parsed_candidate(
    db: Session,
    job_id: uuid.UUID,
    text: str,
    *,
    filename: str = "candidate.pdf",
    status: CandidateStatus = CandidateStatus.PARSED,
) -> tuple[Candidate, ParsedDocument]:
    """A candidate whose document has already been parsed to `text`.

    The page map covers the whole text as one page, which is what a one-page CV
    produces, so `full_text[start:end]` still returns exactly that page.
    """
    candidate = Candidate(job_id=job_id, status=status)
    db.add(candidate)
    db.flush()

    document = CandidateDocument(
        candidate_id=candidate.id,
        original_filename=filename,
        stored_path=f"{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}.pdf",
        content_type="application/pdf",
        size_bytes=len(text.encode("utf-8")) or 1,
        page_count=1,
        file_sha256=sha256_bytes(text.encode("utf-8")),
    )
    db.add(document)
    db.flush()

    parsed = ParsedDocument(
        document_id=document.id,
        full_text=text,
        normalization_version=NORMALIZATION_VERSION,
        page_offsets=[{"page": 1, "start": 0, "end": len(text)}],
        char_count=len(text),
        page_count=1,
        has_text_layer=True,
        language_detected=None,
        injection_flags=document_parsing.scan_for_injection(text) or None,
        text_sha256=sha256_text(text),
        parser_name=document_parsing.PARSER_NAME,
        parser_version=document_parsing.PARSER_VERSION,
    )
    db.add(parsed)
    db.commit()
    db.refresh(candidate)
    db.refresh(parsed)
    return candidate, parsed


def make_candidate_from_fixture(
    db: Session, job_id: uuid.UUID, fixture_name: str
) -> tuple[Candidate, ParsedDocument]:
    """A parsed candidate whose text is byte-identical to a recorded fixture."""
    return make_parsed_candidate(
        db, job_id, fixture_cv_text(fixture_name), filename=f"{fixture_name}.pdf"
    )


def make_job_with_requirements(db: Session, replay_client, *, confirm: bool = True) -> Job:
    """The bundled backend-engineer job, with its thirteen requirements.

    Extraction runs from the recorded JD fixture, so the requirement texts are
    exactly the ones the matching fixtures were recorded against.
    """
    job = jobs.create_job(db, title="Senior Backend Engineer")
    jobs.set_description(
        db,
        job.id,
        raw_text=fixture_jd_text("jd_backend_engineer"),
        source_type=JdSourceType.PASTED,
    )
    jd_extraction.extract_requirements(db, job.id, replay_client)
    if confirm:
        requirements.confirm_requirements(db, job.id)
    db.refresh(job)
    return job
