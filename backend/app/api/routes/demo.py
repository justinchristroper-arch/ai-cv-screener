"""Endpoints for the synthetic demo data.

Two routes, both thin. The rule that matters — seeding is refused outside demo
mode — lives in the service, so it holds for any caller and not only for these
routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import LlmClientDep, SessionDep, SettingsDep, StorageDep
from app.api.limits import model_calls
from app.schemas.api.demo import (
    DemoSamplesResponse,
    DemoSeedResponse,
    SampleCriteriaResponse,
    SampleCvResponse,
)
from app.services import demo

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.get(
    "/samples",
    response_model=DemoSamplesResponse,
    summary="The synthetic inputs a demo is driven with",
    description=(
        "Returns the sample job description and the list of sample CVs bundled with "
        "the project. Every one of them is invented; no real applicant's data is "
        "included. The description is read from the recorded extraction fixture, so "
        "pasting it drives the workflow with no API key and no cost."
    ),
)
def get_samples(settings: SettingsDep) -> DemoSamplesResponse:
    samples = demo.get_samples()
    return DemoSamplesResponse(
        demo_mode=settings.demo_mode,
        job_title=samples.job_title,
        job_description=samples.job_description,
        criteria=[
            SampleCriteriaResponse(
                id=item.id,
                label=item.label,
                language=item.language,
                demonstrates=item.demonstrates,
                text=item.text,
                full_walkthrough=item.full_walkthrough,
            )
            for item in samples.criteria
        ],
        cvs=[
            SampleCvResponse(filename=cv.filename, label=cv.label, demonstrates=cv.demonstrates)
            for cv in samples.cvs
        ],
    )


@router.post(
    "/jobs",
    response_model=DemoSeedResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(model_calls)],
    summary="Seed a ready-to-browse demo job",
    description=(
        "Runs the whole pipeline over the synthetic samples: create the job, attach "
        "the description, extract requirements, confirm them, upload the CVs, then "
        "profile, match and score each one. Nothing is faked or short-circuited — "
        "the scanned sample fails at parsing because it genuinely has no text.\n\n"
        "**Refused unless the server is in demo mode.** An endpoint that manufactures "
        "candidate records has no place in a deployment handling real applications.\n\n"
        "`criteria_id` picks which sample brief to seed from — the formal job "
        "description by default, or one of the informal ones from `/api/demo/samples`."
    ),
    responses={
        404: {"description": "No sample criteria set with that id"},
        409: {"description": "The server is not in demo mode, or the samples are missing"},
        502: {"description": "A recorded fixture could not be used"},
        503: {"description": "No recorded fixture for this input"},
    },
)
def seed_demo_job(
    db: SessionDep,
    client: LlmClientDep,
    storage: StorageDep,
    settings: SettingsDep,
    criteria_id: str | None = None,
) -> DemoSeedResponse:
    result = demo.seed_demo_job(
        db,
        client,
        storage=storage,
        demo_mode=settings.demo_mode,
        max_size_bytes=settings.max_upload_size_bytes,
        max_pages=settings.max_pdf_pages,
        max_files=settings.max_files_per_batch,
        criteria_id=criteria_id,
    )
    return DemoSeedResponse(
        job_id=result.job.id,
        job_title=result.job.title,
        uploaded=result.uploaded,
        rejected=result.rejected,
        screened=result.screened,
        failed=result.failed,
    )
