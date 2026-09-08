"""Stages 3 and 4: HR review of extracted requirements, and the confirmation gate.

The gate (ADR-0004) is `Job.requirements_confirmed_at`. It is `NULL` until a
human confirms, and it is enforced **here, in the service layer** — not in the
UI and not only in a route — so that no API path, background task, or future
caller can bypass it.

What confirmation freezes, and what it does not:

===========================  ==================================================
Change                        While confirmed
===========================  ==================================================
`text`, `category`            Rejected. These change what a verdict *means*, so
                              altering them silently would invalidate every
                              match result derived from them. Unconfirm first.
Add / delete a requirement    Rejected. Confirmation freezes the *set*; adding
                              or removing a member changes the set's identity.
`weight`, `must_have`         Allowed, always. They do not affect verdicts, only
                              the arithmetic applied to them, so a recruiter can
                              re-weight freely at no cost (ADR-0008).
===========================  ==================================================

ADR-0004 states the first and third rows explicitly. The middle row is this
module's reading of "freeze the requirement set": an addition or a deletion
changes the set just as surely as an edit does, so it gets the same treatment.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import RequirementCategory, RequirementOrigin
from app.core.errors import ConflictError, NotFoundError, RequirementsNotConfirmedError
from app.core.protected_attributes import scan as scan_for_protected_attributes
from app.models.job import Job, Requirement
from app.services import invalidation
from app.services.jd_extraction import (
    MUST_HAVE_DEFAULT_WEIGHT,
    NICE_TO_HAVE_DEFAULT_WEIGHT,
)

#: Guard against a weight that would silently dominate every other requirement.
MAX_WEIGHT = Decimal("100")


def _get_job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} does not exist.")
    return job


def _require_unconfirmed(job: Job, action: str) -> None:
    """Refuse a structural change to a frozen requirement set."""
    if job.requirements_confirmed_at is not None:
        raise ConflictError(
            f"This job's requirements are confirmed, so {action} is not allowed. "
            "Unconfirm the job first — that makes the change deliberate and "
            "invalidates anything already derived from the confirmed set."
        )


def list_requirements(db: Session, job_id: uuid.UUID) -> list[Requirement]:
    """Every requirement for a job, confirmed or not. Inspection is always allowed."""
    _get_job(db, job_id)
    return list(
        db.scalars(
            select(Requirement)
            .where(Requirement.job_id == job_id)
            .order_by(Requirement.display_order, Requirement.created_at)
        )
    )


def get_confirmed_requirements(db: Session, job_id: uuid.UUID) -> list[Requirement]:
    """The **only** accessor downstream scoring may use.

    This is the server-side half of the gate. It raises rather than returning
    an empty list, because an empty list is ambiguous — a caller could mistake
    it for "this job has no requirements" and carry on scoring against nothing.

    Phases 7-10 call this, never `list_requirements`.
    """
    job = _get_job(db, job_id)
    if job.requirements_confirmed_at is None:
        raise RequirementsNotConfirmedError(
            "This job's requirements have not been confirmed by a human. "
            "Candidates cannot be screened against an unconfirmed requirement set."
        )
    return list_requirements(db, job_id)


def add_requirement(
    db: Session,
    job_id: uuid.UUID,
    *,
    text: str,
    category: RequirementCategory,
    must_have: bool,
    weight: Decimal | None = None,
) -> Requirement:
    """Add a requirement by hand. Structural, so the job must be unconfirmed."""
    job = _get_job(db, job_id)
    _require_unconfirmed(job, "adding a requirement")

    _validate_weight(weight)

    next_order = db.scalar(
        select(func.coalesce(func.max(Requirement.display_order), -1) + 1).where(
            Requirement.job_id == job_id
        )
    )

    requirement = Requirement(
        job_id=job_id,
        text=text.strip(),
        category=category,
        must_have=must_have,
        weight=weight if weight is not None else _default_weight(must_have),
        display_order=int(next_order or 0),
        origin=RequirementOrigin.HR_ADDED,
        # No `proposed_*`: the model never proposed this one, and recording a
        # proposal that did not happen would corrupt the correction signal.
        proposed_text=None,
        proposed_category=None,
        proposed_must_have=None,
    )
    db.add(requirement)
    # Normally a no-op: the job has to be unconfirmed to get here, and
    # unconfirming already discarded everything derived from the old set. Stated
    # anyway so "adding a requirement invalidates the job's scores" is true of
    # this function rather than true only via a chain of reasoning elsewhere.
    invalidation.invalidate_scores_for_job(db, job_id)
    db.commit()
    db.refresh(requirement)
    return requirement


def update_requirement(
    db: Session,
    requirement_id: uuid.UUID,
    *,
    text: str | None = None,
    category: RequirementCategory | None = None,
    must_have: bool | None = None,
    weight: Decimal | None = None,
) -> Requirement:
    """Edit a requirement, enforcing which fields are frozen by confirmation."""
    requirement = db.get(Requirement, requirement_id)
    if requirement is None:
        raise NotFoundError(f"Requirement {requirement_id} does not exist.")

    job = _get_job(db, requirement.job_id)

    changes_meaning = text is not None or category is not None
    if changes_meaning and job.requirements_confirmed_at is not None:
        raise ConflictError(
            "This job's requirements are confirmed, so text and category cannot "
            "be edited. Weight and the must-have flag can. To change wording or "
            "category, unconfirm the job first."
        )

    if weight is not None:
        _validate_weight(weight)

    if text is not None:
        stripped = text.strip()
        if not stripped:
            raise ConflictError("Requirement text cannot be blank.")
        requirement.text = stripped
    if category is not None:
        requirement.category = category

    # Weight and must-have are the two fields a recruiter edits freely, and both
    # feed the arithmetic rather than the verdicts (docs/data-model.md section
    # 7). Any stored score in this job is therefore now wrong and is dropped;
    # every verdict survives, so recomputing costs nothing and no model call.
    scoring_inputs_changed = False
    if must_have is not None and must_have != requirement.must_have:
        requirement.must_have = must_have
        scoring_inputs_changed = True
    if weight is not None and weight != requirement.weight:
        requirement.weight = weight
        scoring_inputs_changed = True

    if scoring_inputs_changed or changes_meaning:
        invalidation.invalidate_scores_for_job(db, requirement.job_id)

    db.commit()
    db.refresh(requirement)
    return requirement


def delete_requirement(db: Session, requirement_id: uuid.UUID) -> None:
    """Remove a requirement. Structural, so the job must be unconfirmed.

    A hard delete: the data model keeps no requirement history by design
    (docs/data-model.md section 12), and inventing an archive flag here would
    contradict the approved schema.
    """
    requirement = db.get(Requirement, requirement_id)
    if requirement is None:
        raise NotFoundError(f"Requirement {requirement_id} does not exist.")

    job = _get_job(db, requirement.job_id)
    _require_unconfirmed(job, "deleting a requirement")

    db.delete(requirement)
    # As in `add_requirement`: normally a no-op, stated for the same reason.
    # The requirement's own match results go with it, by ON DELETE CASCADE.
    invalidation.invalidate_scores_for_job(db, requirement.job_id)
    db.commit()


def confirm_requirements(db: Session, job_id: uuid.UUID) -> Job:
    """Freeze the requirement set. Idempotent.

    Confirming an already-confirmed job keeps the original timestamp rather
    than refreshing it: the timestamp records when the human actually made the
    decision, and a later no-op call did not change that.

    **Refused when any requirement asks about a protected personal
    characteristic** — age, gender, marital status, religion, ethnicity,
    nationality, appearance, or health. Confirmation is the one gate every
    screening stage passes through (ADR-0004), so refusing here is what makes
    "no candidate is ever screened on one of these" a property of the code
    rather than a promise in a prompt. The requirement is named in the error and
    left exactly as the recruiter wrote it: this application does not silently
    rewrite anyone's criteria, and it does not decide anything about a candidate
    here either. See `core/protected_attributes.py`.
    """
    job = _get_job(db, job_id)

    if job.requirements_confirmed_at is not None:
        return job

    rows = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.job_id == job_id)
            .order_by(Requirement.display_order, Requirement.created_at)
        )
    )
    if not rows:
        raise ConflictError(
            "This job has no requirements to confirm. Extract or add at least one first."
        )

    blocked = [(row, scan_for_protected_attributes(row.text)) for row in rows]
    blocked = [(row, flags) for row, flags in blocked if flags]
    if blocked:
        raise ConflictError(
            "This requirement set cannot be confirmed: "
            f"{len(blocked)} {'requirements ask' if len(blocked) > 1 else 'requirement asks'} "
            "about a personal characteristic that must not be used to screen anyone. "
            f"Remove or reword {'them' if len(blocked) > 1 else 'it'}, then confirm again.",
            details={
                "requirements": [
                    {
                        "requirement_id": str(row.id),
                        "text": row.text,
                        "attributes": [flag.label for flag in flags],
                    }
                    for row, flags in blocked
                ]
            },
        )

    job.requirements_confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def unconfirm_requirements(db: Session, job_id: uuid.UUID) -> Job:
    """Release the freeze so the requirement set can be changed again.

    Idempotent, and **not free**: every match result and every score in the job
    is discarded, because both were derived from a requirement set that is now
    editable again (ADR-0004, docs/data-model.md section 7). That cost is the
    point. It is what makes changing a confirmed requirement a deliberate act
    with visible consequences rather than a silent corruption of results a
    recruiter has already seen.

    Only an actual unconfirm invalidates. Calling this on a job that was never
    confirmed changes nothing and destroys nothing.
    """
    job = _get_job(db, job_id)
    if job.requirements_confirmed_at is None:
        return job

    job.requirements_confirmed_at = None
    invalidation.invalidate_match_results_for_job(db, job_id)
    db.commit()
    db.refresh(job)
    return job


def _default_weight(must_have: bool) -> Decimal:
    return MUST_HAVE_DEFAULT_WEIGHT if must_have else NICE_TO_HAVE_DEFAULT_WEIGHT


def _validate_weight(weight: Decimal | None) -> None:
    """Weight is non-negative and bounded.

    The lower bound matches the database's own CHECK constraint; the upper
    bound has no database equivalent and exists so one requirement cannot be
    given a weight that makes every other requirement arithmetically irrelevant
    by accident.
    """
    if weight is None:
        return
    if weight < 0:
        raise ConflictError("Weight cannot be negative.")
    if weight > MAX_WEIGHT:
        raise ConflictError(f"Weight cannot exceed {MAX_WEIGHT}.")
