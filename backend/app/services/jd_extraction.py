"""Stage 2: job description -> structured requirements.

The first LLM-backed stage. The division of labour is the project's central
rule (ADR-0001): **the model decides what the text says; this code decides
whether that is usable.** Nothing the model returns is persisted until it has
passed local validation, and the model never assigns a score, a rank, or a
recommendation — only text, a category, and a must-have flag.

Retry policy is exactly one retry on schema failure (docs/architecture.md
section 8). The retry is not a blind repeat: it carries the specific validation
errors so the second attempt can correct the first. Two failures end the stage;
nothing partial is written.

Every attempt — success or failure — is written to `LlmCallLog` and committed,
so the audit trail survives the exception that ends a failed extraction.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.enums import (
    LlmPurpose,
    LlmSource,
    LlmStatus,
    RequirementCategory,
    RequirementOrigin,
)
from app.core.errors import ConflictError, ExtractionFailedError, NotFoundError
from app.llm.client import LlmClient, LlmProviderError, LlmResponse
from app.llm.prompts.jd_extraction import PROMPT_VERSION, build_request
from app.models.audit import LlmCallLog
from app.models.job import Job, JobDescription, Requirement
from app.schemas.llm.jd_extraction import RequirementExtractionOutput

logger = logging.getLogger(__name__)

#: Default weights, confirmed in Phase 1 (docs/product-spec.md section 9).
#: HR edits these per requirement; they are a starting point, not a judgement.
MUST_HAVE_DEFAULT_WEIGHT = Decimal("3")
NICE_TO_HAVE_DEFAULT_WEIGHT = Decimal("1")

#: How much of a malformed reply to keep for debugging. Enough to see the
#: shape of the problem, bounded so a runaway reply cannot bloat the table.
RAW_RESPONSE_EXCERPT_LIMIT = 2000

#: The single retry permitted by the architecture.
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ExtractionResult:
    """What the caller needs to report the outcome."""

    requirements: list[Requirement]
    llm_call_id: uuid.UUID
    source: LlmSource
    attempts: int


def _record_call(
    db: Session,
    *,
    job_id: uuid.UUID,
    input_sha256: str,
    model: str,
    source: LlmSource,
    status: LlmStatus,
    attempt: int,
    response: LlmResponse | None = None,
    error_detail: str | None = None,
    raw_response_excerpt: str | None = None,
) -> LlmCallLog:
    """Write one audit row and commit it.

    Committed immediately, on its own, because a failed extraction ends in an
    exception: if these rows shared the caller's transaction they would roll
    back with it and the failure would leave no trace.

    The input text is never stored — only its hash. See the `LlmCallLog`
    docstring for why.
    """
    log = LlmCallLog(
        purpose=LlmPurpose.JD_EXTRACTION,
        model=model,
        prompt_version=PROMPT_VERSION,
        input_sha256=input_sha256,
        source=source,
        status=status,
        attempt=attempt,
        input_tokens=response.input_tokens if response else None,
        output_tokens=response.output_tokens if response else None,
        latency_ms=response.latency_ms if response else None,
        error_detail=error_detail,
        raw_response_excerpt=(
            raw_response_excerpt[:RAW_RESPONSE_EXCERPT_LIMIT] if raw_response_excerpt else None
        ),
        job_id=job_id,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _validate(raw_text: str) -> tuple[RequirementExtractionOutput | None, str | None]:
    """Parse and validate a raw reply.

    Returns `(output, None)` on success or `(None, error_description)` on
    failure. The error description is written back to the model on the retry,
    so it is phrased to be actionable rather than merely diagnostic.
    """
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return None, f"The reply was not valid JSON: {exc.msg} (at position {exc.pos})."

    try:
        output = RequirementExtractionOutput.model_validate(payload)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "(root)"
            lines.append(f"- {location}: {error['msg']}")
        return None, "The reply did not match the required structure:\n" + "\n".join(lines)

    duplicate = _first_duplicate(output)
    if duplicate is not None:
        return None, (
            f"- requirements: '{duplicate}' appears more than once. "
            "Each requirement must be distinct."
        )

    return output, None


def _first_duplicate(output: RequirementExtractionOutput) -> str | None:
    """Return the first requirement text that repeats, ignoring case.

    Duplicates are rejected rather than silently de-duplicated: a reply that
    repeats itself has misread the description, and quietly collapsing the
    repeat would hide that from the recruiter reviewing the result.
    """
    seen: set[str] = set()
    for requirement in output.requirements:
        normalized = " ".join(requirement.text.lower().split())
        if normalized in seen:
            return requirement.text
        seen.add(normalized)
    return None


def extract_requirements(db: Session, job_id: uuid.UUID, client: LlmClient) -> ExtractionResult:
    """Run extraction for a job and replace its LLM-extracted requirements.

    Preconditions, enforced here rather than in the route so no caller can
    bypass them:

    * the job exists and has a job description;
    * the job is **not** confirmed. Re-extraction replaces the requirement set,
      and confirmation freezes that set (ADR-0004), so replacing it has to be a
      deliberate act: unconfirm first.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} does not exist.")

    description = db.scalar(select(JobDescription).where(JobDescription.job_id == job_id))
    if description is None:
        raise ConflictError(
            "This job has no job description yet. Attach one before extracting requirements."
        )

    if job.requirements_confirmed_at is not None:
        raise ConflictError(
            "This job's requirements are confirmed. Extraction would replace the "
            "confirmed set, so unconfirm the job first."
        )

    validation_errors: str | None = None
    last_error: str = "unknown"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = build_request(
            description.raw_text, attempt=attempt, validation_errors=validation_errors
        )

        try:
            response = client.complete(request)
        except LlmProviderError as exc:
            # No reply at all: there is nothing to validate and a retry against
            # the same prompt would not help. Record and stop.
            _record_call(
                db,
                job_id=job_id,
                input_sha256=request.input_sha256,
                model="unknown",
                source=LlmSource.LIVE,
                status=LlmStatus.PROVIDER_ERROR,
                attempt=attempt,
                error_detail=str(exc),
            )
            raise ExtractionFailedError(
                "The language model could not be reached. No requirements were changed."
            ) from exc

        output, error = _validate(response.text)

        if output is None:
            last_error = error or "unknown validation failure"
            _record_call(
                db,
                job_id=job_id,
                input_sha256=request.input_sha256,
                model=response.model,
                source=response.source,
                status=LlmStatus.SCHEMA_INVALID,
                attempt=attempt,
                response=response,
                error_detail=last_error,
                raw_response_excerpt=response.text,
            )
            logger.warning(
                "JD extraction attempt %s/%s failed validation for job %s",
                attempt,
                MAX_ATTEMPTS,
                job_id,
            )
            validation_errors = last_error
            continue

        call_log = _record_call(
            db,
            job_id=job_id,
            input_sha256=request.input_sha256,
            model=response.model,
            source=response.source,
            status=LlmStatus.SUCCESS,
            attempt=attempt,
            response=response,
        )
        requirements = _replace_llm_requirements(db, job_id, output, call_log.id)
        return ExtractionResult(
            requirements=requirements,
            llm_call_id=call_log.id,
            source=response.source,
            attempts=attempt,
        )

    # Both attempts failed validation. Nothing was written to `requirement`;
    # the two SCHEMA_INVALID rows in `llm_call_log` are the failure record.
    raise ExtractionFailedError(
        "The language model returned output this application could not use, twice. "
        "No requirements were changed.",
        details={"attempts": MAX_ATTEMPTS, "last_error": last_error},
    )


def _replace_llm_requirements(
    db: Session,
    job_id: uuid.UUID,
    output: RequirementExtractionOutput,
    llm_call_id: uuid.UUID,
) -> list[Requirement]:
    """Swap in a fresh set of extracted requirements.

    Only `LLM_EXTRACTED` rows are removed. Requirements a human added by hand
    are not derived from the model's reply, and destroying someone's typing
    because they re-ran an extraction would be a poor trade.

    The data model deliberately keeps no requirement revision history
    (docs/data-model.md section 12); what it keeps is the `proposed_*` snapshot
    of what the model said, so a later human edit is measurable against it.
    """
    db.execute(
        delete(Requirement).where(
            Requirement.job_id == job_id,
            Requirement.origin == RequirementOrigin.LLM_EXTRACTED,
        )
    )

    # Hand-added requirements keep their positions at the end of the list.
    existing_manual = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.job_id == job_id)
            .order_by(Requirement.display_order)
        )
    )

    created: list[Requirement] = []
    for index, item in enumerate(output.requirements):
        weight = MUST_HAVE_DEFAULT_WEIGHT if item.must_have else NICE_TO_HAVE_DEFAULT_WEIGHT
        requirement = Requirement(
            job_id=job_id,
            text=item.text,
            category=item.category,
            must_have=item.must_have,
            weight=weight,
            display_order=index,
            origin=RequirementOrigin.LLM_EXTRACTED,
            # Written once, never updated. The gap between these and the
            # current values is the human-correction signal (ADR-0004).
            proposed_text=item.text,
            proposed_category=item.category,
            proposed_must_have=item.must_have,
            llm_call_id=llm_call_id,
        )
        db.add(requirement)
        created.append(requirement)

    for offset, manual in enumerate(existing_manual):
        manual.display_order = len(created) + offset

    db.commit()
    for requirement in created:
        db.refresh(requirement)
    return created


__all__ = [
    "MUST_HAVE_DEFAULT_WEIGHT",
    "NICE_TO_HAVE_DEFAULT_WEIGHT",
    "ExtractionResult",
    "RequirementCategory",
    "extract_requirements",
]
