"""Job, job-description, extraction and confirmation endpoints.

Thin by design: every route validates its body with Pydantic, calls one
service, and maps the result to a `schemas/api` model. Business rules — above
all the confirmation gate — live in the services, so they cannot be bypassed by
reaching a different route (docs/architecture.md section 2.1).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import LlmClientDep, SessionDep
from app.core.errors import NotFoundError
from app.schemas.api.jobs import (
    ExtractionResponse,
    JobCreateRequest,
    JobDescriptionRequest,
    JobDescriptionResponse,
    JobResponse,
    RequirementCreateRequest,
    RequirementListResponse,
    RequirementResponse,
)
from app.services import jd_extraction, jobs, requirements

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job_response(summary: jobs.JobSummary) -> JobResponse:
    return JobResponse(
        id=summary.job.id,
        title=summary.job.title,
        requirements_confirmed_at=summary.job.requirements_confirmed_at,
        has_description=summary.has_description,
        requirement_count=summary.requirement_count,
        created_at=summary.job.created_at,
        updated_at=summary.job.updated_at,
    )


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a job",
)
def create_job(payload: JobCreateRequest, db: SessionDep) -> JobResponse:
    job = jobs.create_job(db, title=payload.title)
    return _job_response(jobs.summarize(db, job))


@router.get("", response_model=list[JobResponse], summary="List jobs")
def list_jobs(db: SessionDep) -> list[JobResponse]:
    return [_job_response(summary) for summary in jobs.list_jobs(db)]


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Job detail",
    responses={404: {"description": "Job does not exist"}},
)
def get_job(job_id: uuid.UUID, db: SessionDep) -> JobResponse:
    job = jobs.get_job(db, job_id)
    return _job_response(jobs.summarize(db, job))


@router.put(
    "/{job_id}/description",
    response_model=JobDescriptionResponse,
    summary="Attach or replace the job description",
    description=(
        "Replacing the text unconfirms the job and deletes requirements that were "
        "extracted from the previous text, because they are derived from an input "
        "that no longer exists. Hand-added requirements are kept. Nothing is "
        "re-extracted automatically."
    ),
    responses={404: {"description": "Job does not exist"}},
)
def set_description(
    job_id: uuid.UUID, payload: JobDescriptionRequest, db: SessionDep
) -> JobDescriptionResponse:
    description = jobs.set_description(
        db,
        job_id,
        raw_text=payload.raw_text,
        source_type=payload.source_type,
        source_filename=payload.source_filename,
    )
    return JobDescriptionResponse.model_validate(description)


@router.get(
    "/{job_id}/description",
    response_model=JobDescriptionResponse,
    summary="Retrieve the job description",
    responses={404: {"description": "Job or description does not exist"}},
)
def get_description(job_id: uuid.UUID, db: SessionDep) -> JobDescriptionResponse:
    jobs.get_job(db, job_id)
    description = jobs.get_description(db, job_id)
    if description is None:
        raise NotFoundError("This job has no job description yet.")
    return JobDescriptionResponse.model_validate(description)


@router.post(
    "/{job_id}/requirements/extract",
    response_model=ExtractionResponse,
    summary="Extract requirements from the job description",
    description=(
        "Runs the language model over the job description and replaces the job's "
        "extracted requirements. Requires an unconfirmed job with a description "
        "attached. The model proposes text, category and must-have only — it "
        "never produces a score, a rank, or a recommendation."
    ),
    responses={
        404: {"description": "Job does not exist"},
        409: {"description": "No description attached, or requirements are confirmed"},
        502: {"description": "The model returned output that could not be used"},
        503: {"description": "The model could not be reached, or no fixture in demo mode"},
    },
)
def extract_requirements(
    job_id: uuid.UUID, db: SessionDep, client: LlmClientDep
) -> ExtractionResponse:
    result = jd_extraction.extract_requirements(db, job_id, client)
    job = jobs.get_job(db, job_id)
    return ExtractionResponse(
        job_id=job_id,
        llm_call_id=result.llm_call_id,
        source=result.source.value,
        attempts=result.attempts,
        requirements_confirmed_at=job.requirements_confirmed_at,
        requirements=[RequirementResponse.model_validate(item) for item in result.requirements],
    )


@router.get(
    "/{job_id}/requirements",
    response_model=RequirementListResponse,
    summary="List the job's requirements",
    responses={404: {"description": "Job does not exist"}},
)
def list_requirements(job_id: uuid.UUID, db: SessionDep) -> RequirementListResponse:
    job = jobs.get_job(db, job_id)
    items = requirements.list_requirements(db, job_id)
    return RequirementListResponse(
        job_id=job_id,
        requirements_confirmed_at=job.requirements_confirmed_at,
        requirements=[RequirementResponse.model_validate(item) for item in items],
    )


@router.post(
    "/{job_id}/requirements",
    response_model=RequirementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a requirement by hand",
    responses={
        404: {"description": "Job does not exist"},
        409: {"description": "Requirements are confirmed; unconfirm first"},
    },
)
def add_requirement(
    job_id: uuid.UUID, payload: RequirementCreateRequest, db: SessionDep
) -> RequirementResponse:
    requirement = requirements.add_requirement(
        db,
        job_id,
        text=payload.text,
        category=payload.category,
        must_have=payload.must_have,
        weight=payload.weight,
    )
    return RequirementResponse.model_validate(requirement)


@router.post(
    "/{job_id}/requirements/confirm",
    response_model=RequirementListResponse,
    summary="Confirm the requirement set — the gate",
    description=(
        "Freezes the requirement set so it can be screened against. Idempotent: "
        "confirming an already-confirmed job keeps the original timestamp."
    ),
    responses={
        404: {"description": "Job does not exist"},
        409: {"description": "The job has no requirements to confirm"},
    },
)
def confirm_requirements(job_id: uuid.UUID, db: SessionDep) -> RequirementListResponse:
    job = requirements.confirm_requirements(db, job_id)
    items = requirements.list_requirements(db, job_id)
    return RequirementListResponse(
        job_id=job_id,
        requirements_confirmed_at=job.requirements_confirmed_at,
        requirements=[RequirementResponse.model_validate(item) for item in items],
    )


@router.delete(
    "/{job_id}/requirements/confirm",
    response_model=RequirementListResponse,
    summary="Unconfirm the requirement set",
    description=(
        "Releases the freeze so text, category, additions and deletions are "
        "allowed again. From Phase 7 this also invalidates anything derived "
        "from the confirmed set."
    ),
    responses={404: {"description": "Job does not exist"}},
)
def unconfirm_requirements(job_id: uuid.UUID, db: SessionDep) -> RequirementListResponse:
    job = requirements.unconfirm_requirements(db, job_id)
    items = requirements.list_requirements(db, job_id)
    return RequirementListResponse(
        job_id=job_id,
        requirements_confirmed_at=job.requirements_confirmed_at,
        requirements=[RequirementResponse.model_validate(item) for item in items],
    )
