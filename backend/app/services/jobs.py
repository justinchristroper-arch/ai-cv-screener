"""Stage 1: jobs and their job descriptions.

Small on purpose. The interesting rule here is what happens when a job
description is **replaced**: the requirements extracted from the old text are
derived data, and docs/data-model.md section 7 is explicit that derived rows
must not outlive the inputs they were derived from.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.enums import JdSourceType, RequirementOrigin
from app.core.errors import NotFoundError
from app.core.hashing import sha256_text
from app.models.job import Job, JobDescription, Requirement
from app.services import document_parsing, invalidation


@dataclass(frozen=True)
class JobSummary:
    """A job plus the derived facts the API reports about it.

    Assembled here rather than stored: `Job` deliberately has no `status`
    column, because a stored status duplicates knowable state and drifts
    (docs/data-model.md section 4.1).
    """

    job: Job
    has_description: bool
    requirement_count: int


def create_job(db: Session, *, title: str) -> Job:
    job = Job(title=title.strip())
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} does not exist.")
    return job


def summarize(db: Session, job: Job) -> JobSummary:
    has_description = (
        db.scalar(
            select(func.count()).select_from(JobDescription).where(JobDescription.job_id == job.id)
        )
        or 0
    ) > 0
    requirement_count = (
        db.scalar(select(func.count()).select_from(Requirement).where(Requirement.job_id == job.id))
        or 0
    )
    return JobSummary(job=job, has_description=has_description, requirement_count=requirement_count)


def list_jobs(db: Session, *, limit: int = 100) -> list[JobSummary]:
    jobs = list(db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)))
    return [summarize(db, job) for job in jobs]


def get_description(db: Session, job_id: uuid.UUID) -> JobDescription | None:
    return db.scalar(select(JobDescription).where(JobDescription.job_id == job_id))


def injection_flags(description: JobDescription) -> list[dict]:
    """Passages in a job description that read as instructions to the system.

    The same scanner the parser runs over CV text (docs/architecture.md section
    5), pointed at the other untrusted channel. Today a description is typed by
    an authenticated colleague, but it becomes an arbitrary-text input to a
    model the moment a live provider is configured, and an untrusted channel
    nobody looks at is the one that gets used.

    **Computed on read rather than stored.** For a CV the flags are provenance:
    they describe the exact text every later quote is verified against, so they
    belong on the row. A description has no such substrate, and scanning on read
    means a description written before a pattern was added is still covered by
    it — stored flags would quietly go stale instead.

    Flagged, never stripped, and never a reason to reject the description. The
    load-bearing controls are elsewhere and unchanged: the text goes to the
    model inside a delimited data block and never into the system prompt, the
    reply is validated against a schema with no field for an instruction, and a
    human reviews and confirms every requirement before anything is screened.
    """
    return document_parsing.scan_for_injection(description.raw_text)


def set_description(
    db: Session,
    job_id: uuid.UUID,
    *,
    raw_text: str,
    source_type: JdSourceType,
    source_filename: str | None = None,
) -> JobDescription:
    """Attach or replace the job description. One per job, by constraint.

    Replacing the text has two consequences, both from the staleness table in
    docs/data-model.md section 7:

    * the job is **unconfirmed** — a confirmation applied to requirements read
      out of different text no longer means anything;
    * `LLM_EXTRACTED` requirements are **deleted** — they are derived from the
      replaced input. Requirements a human added by hand are kept, because they
      are not model-derived and silently discarding someone's work would be a
      worse default than leaving it for them to review.

    Nothing is re-extracted automatically. Running extraction is a deliberate,
    logged, billable act, so it stays an explicit call.
    """
    get_job(db, job_id)

    text = raw_text.strip()
    existing = get_description(db, job_id)

    if existing is None:
        description = JobDescription(
            job_id=job_id,
            source_type=source_type,
            source_filename=source_filename,
            raw_text=text,
            text_sha256=sha256_text(text),
        )
        db.add(description)
        db.commit()
        db.refresh(description)
        return description

    replaced_text = existing.raw_text != text
    existing.raw_text = text
    existing.text_sha256 = sha256_text(text)
    existing.source_type = source_type
    existing.source_filename = source_filename

    if replaced_text:
        db.execute(
            delete(Requirement).where(
                Requirement.job_id == job_id,
                Requirement.origin == RequirementOrigin.LLM_EXTRACTED,
            )
        )
        job = get_job(db, job_id)
        job.requirements_confirmed_at = None
        # Deleting the requirements takes their match results with them, by
        # ON DELETE CASCADE. Scores are not cascaded from `requirement` and have
        # to be discarded explicitly, or a candidate would keep a number
        # computed against criteria that no longer exist.
        invalidation.invalidate_match_results_for_job(db, job_id)

    db.commit()
    db.refresh(existing)
    return existing
