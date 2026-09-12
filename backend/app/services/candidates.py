"""Stage 5: accept CV uploads, store them, and drive parsing.

One file becomes one `Candidate` + one `CandidateDocument` + (on success) one
`ParsedDocument`. A file that fails validation becomes none of those — it is
reported back against its filename and nothing is stored.

**A failure is scoped to one file.** A batch of twenty-five where three are
corrupt yields twenty-two usable candidates and three honest failures, never a
rejected batch (docs/architecture.md section 8).

Parsing runs inline here rather than in a background task. Architecture section
7 specifies background processing for the per-candidate pipeline, but its
stated reason is the minutes of LLM work in stages 7-11, none of which exists
yet: parsing alone is milliseconds, and running it inline means the upload
response tells the truth about the outcome immediately. The orchestration is
isolated in `_parse_candidate` precisely so that Phase 6 can move it onto a
background task without the API or the parsing service changing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import CandidateFailureReason, CandidateStatus
from app.core.errors import ConflictError, NotFoundError
from app.core.hashing import sha256_bytes
from app.core.storage import DocumentStorage
from app.models.candidate import Candidate, CandidateDocument, ParsedDocument
from app.models.job import Job
from app.services import document_parsing
from app.services.document_parsing import PdfExtractionError, PdfRejection

logger = logging.getLogger(__name__)

#: Kept as display metadata only, never used to build a path. Bounded so a
#: pathological name cannot bloat the row or a UI.
MAX_STORED_FILENAME_LENGTH = 255


@dataclass(frozen=True)
class UploadOutcome:
    """What happened to one file in a batch."""

    filename: str
    accepted: bool
    candidate_id: uuid.UUID | None = None
    status: CandidateStatus | None = None
    failure_reason: CandidateFailureReason | None = None
    detail: str | None = None
    #: Set only when the file was rejected before any row was created.
    rejection_code: str | None = None


def sanitize_filename(filename: str) -> str:
    """Reduce a client filename to something safe to store and display.

    This value is **metadata only** — `DocumentStorage` derives the actual path
    from a generated UUID and never consults it. Sanitizing anyway means a
    hostile name cannot cause trouble somewhere else later: a log line, a
    download header, a UI.

    Everything before the last separator is discarded, so `../../etc/passwd`
    becomes `passwd`. Control characters go; the rest of the name is preserved,
    because a CV legitimately called `Résumé (final).pdf` should keep its name.
    """
    name = filename.replace("\\", "/").split("/")[-1]
    name = "".join(char for char in name if char.isprintable())
    name = name.strip().strip(".")
    if not name:
        return "unnamed.pdf"
    return name[:MAX_STORED_FILENAME_LENGTH]


def upload_candidates(
    db: Session,
    job_id: uuid.UUID,
    files: list[tuple[str, bytes]],
    *,
    storage: DocumentStorage,
    max_size_bytes: int,
    max_pages: int,
    max_files: int,
) -> list[UploadOutcome]:
    """Validate, store and parse each uploaded file.

    The job must exist. It does **not** need confirmed requirements: uploading
    CVs and confirming a requirement set are independent activities, and the
    confirmation gate guards *screening*, not ingestion (ADR-0004).
    """
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} does not exist.")

    if not files:
        raise ConflictError("No files were uploaded.")

    if len(files) > max_files:
        raise ConflictError(
            f"{len(files)} files were uploaded; the limit is {max_files} per batch."
        )

    outcomes: list[UploadOutcome] = []
    for filename, data in files:
        outcomes.append(
            _process_one(
                db,
                job_id,
                filename=filename,
                data=data,
                storage=storage,
                max_size_bytes=max_size_bytes,
                max_pages=max_pages,
            )
        )
    return outcomes


def _process_one(
    db: Session,
    job_id: uuid.UUID,
    *,
    filename: str,
    data: bytes,
    storage: DocumentStorage,
    max_size_bytes: int,
    max_pages: int,
) -> UploadOutcome:
    safe_name = sanitize_filename(filename)

    try:
        page_count = document_parsing.validate_upload(
            filename=safe_name,
            data=data,
            max_size_bytes=max_size_bytes,
            max_pages=max_pages,
        )
    except PdfRejection as rejection:
        # Nothing is stored and no candidate exists: the file never became a
        # candidate in the first place.
        logger.info("Rejected upload %r: %s", safe_name, rejection.code)
        return UploadOutcome(
            filename=safe_name,
            accepted=False,
            rejection_code=rejection.code,
            detail=rejection.message,
        )

    candidate = Candidate(
        job_id=job_id,
        # Left NULL on purpose. A candidate's name is extracted from the CV in
        # Phase 6; deriving one from a filename now would invent data. The API
        # returns the original filename for display instead.
        display_name=None,
        status=CandidateStatus.UPLOADED,
        stage_started_at=datetime.now(timezone.utc),
    )
    db.add(candidate)
    db.flush()

    document = CandidateDocument(
        candidate_id=candidate.id,
        original_filename=safe_name,
        # Placeholder: the real path needs the row id, which needs the flush
        # below. Never a client-supplied value at any point.
        stored_path="",
        # The verified type, not the client's Content-Type header.
        content_type="application/pdf",
        size_bytes=len(data),
        page_count=page_count,
        file_sha256=sha256_bytes(data),
    )
    db.add(document)
    db.flush()

    document.stored_path = storage.save(document.id, data)
    db.commit()

    return _parse_candidate(db, candidate, document, storage)


def _parse_candidate(
    db: Session,
    candidate: Candidate,
    document: CandidateDocument,
    storage: DocumentStorage,
) -> UploadOutcome:
    """Run extraction and record the result on the candidate."""
    candidate.status = CandidateStatus.PARSING
    candidate.stage_started_at = datetime.now(timezone.utc)
    db.commit()

    try:
        parsed = document_parsing.extract_text(storage.read(document.stored_path))
    except PdfExtractionError as exc:
        _mark_failed(db, candidate, exc.reason, exc.detail)
        logger.info("Candidate %s failed parsing: %s", candidate.id, exc.reason.value)
        return UploadOutcome(
            filename=document.original_filename,
            accepted=True,
            candidate_id=candidate.id,
            status=CandidateStatus.FAILED,
            failure_reason=exc.reason,
            detail=exc.detail,
        )
    except OSError as exc:
        # The row exists but its bytes do not. Recorded rather than raised, so
        # one unreadable file does not fail the whole batch.
        _mark_failed(
            db,
            candidate,
            CandidateFailureReason.EXTRACTION_FAILED,
            "The stored file could not be read.",
        )
        logger.error("Storage read failed for candidate %s: %s", candidate.id, type(exc).__name__)
        return UploadOutcome(
            filename=document.original_filename,
            accepted=True,
            candidate_id=candidate.id,
            status=CandidateStatus.FAILED,
            failure_reason=CandidateFailureReason.EXTRACTION_FAILED,
            detail="The stored file could not be read.",
        )

    db.add(
        ParsedDocument(
            document_id=document.id,
            full_text=parsed.full_text,
            normalization_version=parsed.normalization_version,
            page_offsets=parsed.page_offsets,
            char_count=parsed.char_count,
            page_count=parsed.page_count,
            has_text_layer=parsed.has_text_layer,
            # Not detected in this phase. The UNSUPPORTED_LANGUAGE failure
            # reason exists in the model but language detection is not a
            # Phase 5 deliverable, so this stays NULL rather than guessed.
            language_detected=None,
            injection_flags=parsed.injection_flags or None,
            multi_column_pages=parsed.multi_column_pages or None,
            text_sha256=parsed.text_sha256,
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
        )
    )

    candidate.status = CandidateStatus.PARSED
    candidate.stage_started_at = None
    candidate.failure_reason = None
    candidate.failure_detail = None
    db.commit()

    return UploadOutcome(
        filename=document.original_filename,
        accepted=True,
        candidate_id=candidate.id,
        status=CandidateStatus.PARSED,
    )


def _mark_failed(
    db: Session,
    candidate: Candidate,
    reason: CandidateFailureReason,
    detail: str,
) -> None:
    """Record an honest failure.

    `failure_reason` is never left unset: the database enforces that a FAILED
    candidate has one (`ck_candidate_failure_reason_iff_failed`), which is the
    product rule that failures are reported rather than swallowed.
    """
    candidate.status = CandidateStatus.FAILED
    candidate.failure_reason = reason
    candidate.failure_detail = detail
    candidate.stage_started_at = None
    db.commit()


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def list_candidates(db: Session, job_id: uuid.UUID) -> list[Candidate]:
    """Every candidate for a job, including failed ones.

    Failures are never hidden: a recruiter has to be able to see that a file
    they uploaded did not make it (docs/product-spec.md section 12).
    """
    if db.get(Job, job_id) is None:
        raise NotFoundError(f"Job {job_id} does not exist.")
    return list(
        db.scalars(
            select(Candidate)
            .where(Candidate.job_id == job_id)
            .order_by(Candidate.created_at, Candidate.id)
        )
    )


def get_candidate(db: Session, candidate_id: uuid.UUID) -> Candidate:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise NotFoundError(f"Candidate {candidate_id} does not exist.")
    return candidate


def get_document(db: Session, candidate_id: uuid.UUID) -> CandidateDocument | None:
    return db.scalar(
        select(CandidateDocument).where(CandidateDocument.candidate_id == candidate_id)
    )


def get_parsed_document(db: Session, candidate_id: uuid.UUID) -> ParsedDocument | None:
    document = get_document(db, candidate_id)
    if document is None:
        return None
    return db.scalar(select(ParsedDocument).where(ParsedDocument.document_id == document.id))
