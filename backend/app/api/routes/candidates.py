"""CV upload and parsing-result endpoints.

Thin, like every other router here: validate, call one service, map the result.
The interesting rules — what a valid PDF is, where bytes are stored, how a
failure is recorded — live in the services, so no future caller can go around
them.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import LlmClientDep, SessionDep, SettingsDep, StorageDep
from app.api.limits import model_calls, uploads
from app.core.enums import DETERMINISTIC_METHODS, MatchVerdict
from app.core.errors import ConflictError, NotFoundError
from app.models.candidate import Candidate
from app.models.evaluation import EvidenceSpan, Score
from app.models.job import Requirement
from app.models.profile import CandidateProfile
from app.schemas.api.candidates import (
    CandidateListResponse,
    CandidateResponse,
    CandidateTextResponse,
    DocumentSummary,
    ParsedDocumentSummary,
    UploadBatchResponse,
    UploadOutcomeResponse,
)
from app.schemas.api.scoring import ContributionResponse, ScoreResponse
from app.schemas.api.screening import (
    CandidateProfileResponse,
    EvidenceResponse,
    EvidenceSummary,
    MatchingRunResponse,
    MatchResultResponse,
    MatchResultsResponse,
    MatchSummary,
    ProfileEducationResponse,
    ProfileExperienceResponse,
    ProfileExtractionResponse,
    ProfileProjectResponse,
    ProfileSkillResponse,
)
from app.services import candidates as candidates_service
from app.services import jobs as jobs_service
from app.services import matching as matching_service
from app.services import profile_extraction as profile_service
from app.services import scoring as scoring_service

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
    dependencies=[Depends(uploads)],
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


# --------------------------------------------------------------------------
# Candidate intelligence: profile extraction and requirement matching
# --------------------------------------------------------------------------


def _evidence_response(span: EvidenceSpan | None) -> EvidenceResponse | None:
    return EvidenceResponse.model_validate(span) if span is not None else None


def _profile_response(
    db: Session, candidate_id: uuid.UUID, profile: CandidateProfile
) -> CandidateProfileResponse:
    items = profile_service.load_profile_items(db, profile)

    def evidence(span_id: uuid.UUID | None) -> EvidenceResponse | None:
        return _evidence_response(items.spans.get(span_id)) if span_id else None

    cited = sum(
        1
        for group in (items.skills, items.experience, items.education, items.projects)
        for item in group
        if item.evidence_span_id is not None
    )
    verified = items.verified_evidence_count

    return CandidateProfileResponse(
        candidate_id=candidate_id,
        profile_id=profile.id,
        parsed_document_id=profile.parsed_document_id,
        prompt_version=profile.prompt_version,
        llm_call_id=profile.llm_call_id,
        created_at=profile.created_at,
        skills=[
            ProfileSkillResponse(
                id=item.id,
                raw_name=item.raw_name,
                normalized_name=item.normalized_name,
                evidence=evidence(item.evidence_span_id),
            )
            for item in items.skills
        ],
        experience=[
            ProfileExperienceResponse(
                id=item.id,
                role_title=item.role_title,
                organization=item.organization,
                start_date=item.start_date,
                end_date=item.end_date,
                date_precision=item.date_precision,
                is_current=item.is_current,
                description=item.description,
                evidence=evidence(item.evidence_span_id),
            )
            for item in items.experience
        ],
        education=[
            ProfileEducationResponse(
                id=item.id,
                degree=item.degree,
                field_of_study=item.field_of_study,
                institution=item.institution,
                completion_year=item.completion_year,
                evidence=evidence(item.evidence_span_id),
            )
            for item in items.education
        ],
        projects=[
            ProfileProjectResponse(
                id=item.id,
                name=item.name,
                description=item.description,
                technologies=list(item.technologies or []),
                evidence=evidence(item.evidence_span_id),
            )
            for item in items.projects
        ],
        evidence_summary=EvidenceSummary(
            items=items.item_count,
            with_evidence=cited,
            verified=verified,
            unverified=cited - verified,
        ),
    )


def _match_results_response(db: Session, candidate: Candidate) -> MatchResultsResponse:
    rows = matching_service.list_match_results(db, candidate.id)
    job = jobs_service.get_job(db, candidate.job_id)

    return MatchResultsResponse(
        candidate_id=candidate.id,
        job_id=candidate.job_id,
        requirements_confirmed_at=job.requirements_confirmed_at,
        results=[
            MatchResultResponse(
                requirement_id=row.requirement.id,
                requirement_text=row.requirement.text,
                category=row.requirement.category,
                must_have=row.requirement.must_have,
                display_order=row.requirement.display_order,
                verdict=row.result.verdict,
                decided_by=row.result.decided_by,
                reason=row.result.reason,
                evidence=_evidence_response(row.span),
                raw_verdict=row.result.raw_verdict,
                downgraded=row.result.downgraded,
                llm_call_id=row.result.llm_call_id,
            )
            for row in rows
        ],
        summary=MatchSummary(
            total=len(rows),
            matched=sum(1 for row in rows if row.result.verdict is MatchVerdict.MATCHED),
            partial=sum(1 for row in rows if row.result.verdict is MatchVerdict.PARTIAL),
            no_evidence=sum(1 for row in rows if row.result.verdict is MatchVerdict.NO_EVIDENCE),
            needs_review=sum(1 for row in rows if row.result.verdict is MatchVerdict.NEEDS_REVIEW),
            downgraded=sum(1 for row in rows if row.result.downgraded),
            decided_deterministically=sum(
                1 for row in rows if row.result.decided_by in DETERMINISTIC_METHODS
            ),
            decided_by_model=sum(
                1 for row in rows if row.result.decided_by not in DETERMINISTIC_METHODS
            ),
        ),
    )


@router.post(
    "/api/candidates/{candidate_id}/profile",
    response_model=ProfileExtractionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(model_calls)],
    summary="Extract a structured profile from the candidate's CV",
    description=(
        "Reads the parsed CV text and stores the skills, roles, qualifications and "
        "projects it states, each with a quoted passage verified against the document. "
        "The model never returns a score, a rank or a recommendation, and the profile "
        "has no field for a name, age, gender, nationality, photo, address, phone or "
        "email. Idempotent: a candidate that already has a profile, or whose document "
        "text has already been extracted, is served without a model call."
    ),
    responses={
        404: {"description": "Candidate does not exist"},
        409: {"description": "This candidate has no extracted text"},
        502: {"description": "The model returned output that could not be used"},
        503: {"description": "The model could not be reached, or no fixture in demo mode"},
    },
)
def extract_candidate_profile(
    candidate_id: uuid.UUID, db: SessionDep, client: LlmClientDep
) -> ProfileExtractionResponse:
    result = profile_service.extract_profile(db, candidate_id, client)
    return ProfileExtractionResponse(
        candidate_id=candidate_id,
        source=result.source.value if result.source else None,
        attempts=result.attempts,
        cache_hit=result.cache_hit,
        profile=_profile_response(db, candidate_id, result.profile),
    )


@router.get(
    "/api/candidates/{candidate_id}/profile",
    response_model=CandidateProfileResponse,
    summary="The candidate's extracted profile",
    description=(
        "Every item carries the passage it was read from, with its verification "
        "status. An item whose quote could not be located in the document is shown "
        "and flagged rather than hidden."
    ),
    responses={404: {"description": "Candidate does not exist, or has no profile yet"}},
)
def get_candidate_profile(candidate_id: uuid.UUID, db: SessionDep) -> CandidateProfileResponse:
    profile = profile_service.require_profile(db, candidate_id)
    return _profile_response(db, candidate_id, profile)


@router.post(
    "/api/candidates/{candidate_id}/matches",
    response_model=MatchingRunResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(model_calls)],
    summary="Match the candidate against the job's confirmed requirements",
    description=(
        "Requires the job's requirements to be confirmed: matching against a draft "
        "set is refused server-side, not merely discouraged in the UI. Deterministic "
        "rules settle every pair they can before the model is asked about the rest, "
        "and a proposed verdict whose evidence cannot be verified in the CV is "
        "downgraded to NO_EVIDENCE and flagged. No score, band or rank is produced."
    ),
    responses={
        404: {"description": "Candidate does not exist"},
        409: {
            "description": (
                "Requirements are not confirmed, the job has none, or the candidate "
                "has no extracted profile"
            )
        },
        502: {"description": "The model returned output that could not be used"},
        503: {"description": "The model could not be reached, or no fixture in demo mode"},
    },
)
def run_candidate_matching(
    candidate_id: uuid.UUID, db: SessionDep, client: LlmClientDep
) -> MatchingRunResponse:
    outcome = matching_service.run_matching(db, candidate_id, client)
    candidate = candidates_service.get_candidate(db, candidate_id)
    base = _match_results_response(db, candidate)
    return MatchingRunResponse(
        **base.model_dump(),
        source=outcome.source.value if outcome.source else None,
        attempts=outcome.attempts,
    )


@router.get(
    "/api/candidates/{candidate_id}/matches",
    response_model=MatchResultsResponse,
    summary="Stored verdicts for the candidate, with their evidence",
    description=(
        "Returns one entry per requirement, in the recruiter's display order. "
        "NO_EVIDENCE states that this document contains no verified evidence for a "
        "requirement; it is never a claim that the candidate lacks the skill."
    ),
    responses={404: {"description": "Candidate does not exist"}},
)
def get_candidate_matches(candidate_id: uuid.UUID, db: SessionDep) -> MatchResultsResponse:
    candidate = candidates_service.get_candidate(db, candidate_id)
    return _match_results_response(db, candidate)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _score_response(
    db: Session,
    candidate: Candidate,
    row: Score,
    breakdown: scoring_service.ScoreBreakdown,
) -> ScoreResponse:
    capped_text: str | None = None
    if row.capped_by_requirement_id is not None:
        requirement = db.get(Requirement, row.capped_by_requirement_id)
        capped_text = requirement.text if requirement else None

    return ScoreResponse(
        candidate_id=candidate.id,
        job_id=candidate.job_id,
        status=row.status,
        score=row.score,
        score_raw=row.score_raw,
        weighted_sum=row.weighted_sum,
        total_weight=row.total_weight,
        must_have_coverage=row.must_have_coverage,
        band=row.band,
        band_raw=row.band_raw,
        capped=row.capped,
        capped_by_requirement_id=row.capped_by_requirement_id,
        capped_by_requirement_text=capped_text,
        review_flag=breakdown.review_flag,
        needs_review_count=len(breakdown.needs_review),
        must_have_needs_review_count=len(breakdown.must_have_needs_review),
        scoring_config_version=row.scoring_config_version,
        computed_at=row.computed_at,
        contributions=[
            ContributionResponse(
                requirement_id=item.requirement_id,
                requirement_text=item.requirement_text,
                category=item.category,
                must_have=item.must_have,
                display_order=item.display_order,
                weight=item.weight,
                verdict=item.verdict,
                verdict_value=item.verdict_value,
                points=item.points,
            )
            for item in breakdown.contributions
        ],
    )


@router.post(
    "/api/candidates/{candidate_id}/score",
    response_model=ScoreResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Compute the candidate's score from stored verdicts and weights",
    description=(
        "Deterministic arithmetic over the stored match results and the "
        "recruiter's weights: no model call is made, and the same verdicts and "
        "weights always produce the same number. Requires the job's requirements "
        "to be confirmed and every one of them to have a verdict. Recomputation "
        "is free and expected — changing a weight and re-running costs nothing. "
        "The score orders a worklist; it never rejects, hides or filters anyone."
    ),
    responses={
        404: {"description": "Candidate does not exist"},
        409: {
            "description": (
                "Requirements are not confirmed, or some confirmed requirement has "
                "no match result for this candidate"
            )
        },
    },
)
def score_candidate(candidate_id: uuid.UUID, db: SessionDep) -> ScoreResponse:
    row, breakdown = scoring_service.score_candidate(db, candidate_id)
    candidate = candidates_service.get_candidate(db, candidate_id)
    return _score_response(db, candidate, row, breakdown)


@router.get(
    "/api/candidates/{candidate_id}/score",
    response_model=ScoreResponse,
    summary="The candidate's stored score and its per-requirement breakdown",
    description=(
        "The breakdown is recomputed from the requirements and verdicts still in "
        "the database rather than stored a second time, so what is returned is "
        "always the arithmetic behind the stored total."
    ),
    responses={404: {"description": "Candidate does not exist, or has no score yet"}},
)
def get_candidate_score(candidate_id: uuid.UUID, db: SessionDep) -> ScoreResponse:
    candidate = candidates_service.get_candidate(db, candidate_id)
    row = scoring_service.get_score(db, candidate_id)
    if row is None:
        raise NotFoundError(f"Candidate {candidate_id} has no score yet. Run scoring first.")
    breakdown = scoring_service.load_breakdown(db, candidate_id)
    return _score_response(db, candidate, row, breakdown)
