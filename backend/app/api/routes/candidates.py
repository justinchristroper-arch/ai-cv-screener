"""CV upload and parsing-result endpoints.

Thin, like every other router here: validate, call one service, map the result.
The interesting rules — what a valid PDF is, where bytes are stored, how a
failure is recorded — live in the services, so no future caller can go around
them.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, SettingsDep, StorageDep
from app.core.errors import ConflictError
from app.models.candidate import Candidate
from app.schemas.api.candidates import (
    CandidateListResponse,
    CandidateResponse,
    CandidateTextResponse,
    DocumentSummary,
    ParsedDocumentSummary,
    UploadBatchResponse,
    UploadOutcomeResponse,
)
from app.services import candidates as candidates_service

router = APIRouter(tags=["candidates"])


def _candidate_response(db: Session, candidate: Candidate) -> CandidateResponse:
    document = candidates_service.get_document(db, candidate.id)
    parsed = candidates_service.get_parsed_document(db, candidate.id)
    return CandidateResponse(
        id=candidate.id,
        job_id=candidate.job_id,
        display_name=candidate.display_name,
        status=candidate.status,
        failure_reason=candidate.failure_reason,
        failure_detail=candidate.failure_detail,
        created_at=candidate.created_at,
        document=DocumentSummary.model_validate(document) if document else None,
        parsed=(
            ParsedDocumentSummary(
                page_count=parsed.page_count,
                char_count=parsed.char_count,
                has_text_layer=parsed.has_text_layer,
                normalization_version=parsed.normalization_version,
                parser_name=parsed.parser_name,
                parser_version=parsed.parser_version,
                text_sha256=parsed.text_sha256,
                language_detected=parsed.language_detected,
                injection_flag_count=len(parsed.injection_flags or []),
            )
            if parsed
            else None
        ),
    )


@router.post(
    "/api/jobs/{job_id}/candidates",
    response_model=UploadBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload CV PDFs for a job",
    description=(
        "Accepts one or more PDF files. Each file is validated and parsed "
        "independently: an invalid file is reported against its own filename and "
        "does not affect the rest of the batch. Files are validated by their "
        "actual content, not by the Content-Type header or the file extension "
        "alone. Scanned or image-only PDFs are recorded as failures — this "
        "system does not perform OCR."
    ),
    responses={
        404: {"description": "Job does not exist"},
        409: {"description": "No files supplied, or more than the per-batch limit"},
    },
)
def upload_candidates(
    job_id: uuid.UUID,
    db: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    files: list[UploadFile] = File(..., description="One or more CV PDFs"),
) -> UploadBatchResponse:
    # The whole file is read into memory to hash and validate it. Bounded by
    # max_upload_size_mb (10 MB) and max_files_per_batch (25), so the worst
    # case is modest; streaming to disk before validating would mean writing
    # unvalidated bytes, which is the trade this deliberately avoids.
    payloads: list[tuple[str, bytes]] = [(file.filename or "", file.file.read()) for file in files]

    outcomes = candidates_service.upload_candidates(
        db,
        job_id,
        payloads,
        storage=storage,
        max_size_bytes=settings.max_upload_size_bytes,
        max_pages=settings.max_pdf_pages,
        max_files=settings.max_files_per_batch,
    )

    return UploadBatchResponse(
        job_id=job_id,
        uploaded=sum(1 for outcome in outcomes if outcome.accepted),
        rejected=sum(1 for outcome in outcomes if not outcome.accepted),
        results=[
            UploadOutcomeResponse(
                filename=outcome.filename,
                accepted=outcome.accepted,
                candidate_id=outcome.candidate_id,
                status=outcome.status,
                failure_reason=outcome.failure_reason,
                rejection_code=outcome.rejection_code,
                detail=outcome.detail,
            )
            for outcome in outcomes
        ],
    )


@router.get(
    "/api/jobs/{job_id}/candidates",
    response_model=CandidateListResponse,
    summary="List a job's candidates and their processing state",
    description=(
        "Includes failed candidates with their reason. A file that failed is never "
        "hidden from the recruiter who uploaded it."
    ),
    responses={404: {"description": "Job does not exist"}},
)
def list_candidates(job_id: uuid.UUID, db: SessionDep) -> CandidateListResponse:
    items = candidates_service.list_candidates(db, job_id)
    return CandidateListResponse(
        job_id=job_id,
        candidates=[_candidate_response(db, candidate) for candidate in items],
    )


@router.get(
    "/api/candidates/{candidate_id}",
    response_model=CandidateResponse,
    summary="Candidate processing status and document metadata",
    responses={404: {"description": "Candidate does not exist"}},
)
def get_candidate(candidate_id: uuid.UUID, db: SessionDep) -> CandidateResponse:
    candidate = candidates_service.get_candidate(db, candidate_id)
    return _candidate_response(db, candidate)


@router.get(
    "/api/candidates/{candidate_id}/text",
    response_model=CandidateTextResponse,
    summary="Extracted text with its page map",
    description=(
        "The normalized text and the character range of each page. "
        "`text[start:end]` for a page range returns exactly that page, which is "
        "what lets a later evidence quote be traced to a location without asking "
        "a model where it came from."
    ),
    responses={
        404: {"description": "Candidate does not exist"},
        409: {"description": "This candidate has no extracted text"},
    },
)
def get_candidate_text(candidate_id: uuid.UUID, db: SessionDep) -> CandidateTextResponse:
    candidate = candidates_service.get_candidate(db, candidate_id)
    parsed = candidates_service.get_parsed_document(db, candidate_id)
    if parsed is None:
        raise ConflictError(
            "This candidate has no extracted text. "
            f"Its processing status is {candidate.status.value}."
        )
    return CandidateTextResponse(
        candidate_id=candidate_id,
        normalization_version=parsed.normalization_version,
        page_count=parsed.page_count,
        char_count=parsed.char_count,
        text=parsed.full_text,
        pages=parsed.page_offsets,
        injection_flags=parsed.injection_flags or [],
    )
