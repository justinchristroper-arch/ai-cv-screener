"""Stage 10: ask the model about the pairs deterministic rules could not settle.

This module's job ends where judgement about *policy* begins. It renders one
batched call for one candidate's undecided requirements, validates the reply
against ``schemas/llm/semantic_match``, retries once on a schema failure, and
returns the model's claims as plain data.

It deliberately does **not** verify evidence, downgrade a verdict, or write a
``match_result``. Those are policy decisions and they live in
``services/matching.py``, so that "what the model said" and "what the
application decided" are separate, separately testable steps -- which is the
whole point of ADR-0001.

Batching is per candidate rather than per pair: one call carrying every
undecided requirement costs one round trip instead of a dozen, and the model
sees the CV once. The verdicts come back keyed by list position, and this
module checks that the reply covers exactly the positions it asked about.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.enums import LlmPurpose, LlmSource, LlmStatus, MatchVerdict
from app.core.errors import ExtractionFailedError
from app.llm.client import LlmClient, LlmProviderError, LlmResponse
from app.llm.prompts.semantic_match import PROMPT_VERSION, RequirementPrompt, build_request
from app.models.audit import LlmCallLog
from app.models.candidate import Candidate, ParsedDocument
from app.models.job import Requirement
from app.schemas.llm.semantic_match import SemanticMatchOutput

logger = logging.getLogger(__name__)

RAW_RESPONSE_EXCERPT_LIMIT = 2000

#: The single retry permitted by the architecture.
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class SemanticProposal:
    """What the model claims about one requirement. Not yet a verdict."""

    requirement_id: uuid.UUID
    verdict: MatchVerdict
    evidence_quote: str | None
    reason: str


@dataclass(frozen=True)
class SemanticEvaluation:
    """One batched call's outcome."""

    proposals: list[SemanticProposal]
    llm_call_id: uuid.UUID
    source: LlmSource
    attempts: int


def _record_call(
    db: Session,
    *,
    candidate_id: uuid.UUID,
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
    """Write one audit row and commit it, for the same reason Phase 4 does."""
    log = LlmCallLog(
        purpose=LlmPurpose.SEMANTIC_MATCH,
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
        candidate_id=candidate_id,
        job_id=job_id,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _validate(
    raw_text: str, expected_indices: set[int]
) -> tuple[SemanticMatchOutput | None, str | None]:
    """Parse, validate, and check that the reply answers exactly what was asked.

    Coverage is checked here rather than in the Pydantic model because it
    depends on the request: the schema cannot know how many requirements this
    particular call carried.
    """
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return None, f"The reply was not valid JSON: {exc.msg} (at position {exc.pos})."

    try:
        output = SemanticMatchOutput.model_validate(payload)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "(root)"
            lines.append(f"- {location}: {error['msg']}")
        return None, "The reply did not match the required structure:\n" + "\n".join(lines)

    returned = [item.index for item in output.verdicts]
    duplicates = sorted({index for index in returned if returned.count(index) > 1})
    if duplicates:
        return None, (
            f"- verdicts: index {duplicates} appears more than once. "
            "Return exactly one verdict per requirement."
        )

    unknown = sorted(set(returned) - expected_indices)
    if unknown:
        return None, (
            f"- verdicts: index {unknown} does not match any requirement in the list. "
            f"Valid indices are 0 to {max(expected_indices)}."
        )

    missing = sorted(expected_indices - set(returned))
    if missing:
        return None, (
            f"- verdicts: no verdict was returned for index {missing}. "
            "Every requirement in the list needs one."
        )

    return output, None


def evaluate(
    db: Session,
    *,
    candidate: Candidate,
    parsed_document: ParsedDocument,
    requirements: list[Requirement],
    client: LlmClient,
) -> SemanticEvaluation:
    """Run one batched semantic-matching call for `requirements`.

    `requirements` must be non-empty; callers skip this stage entirely when the
    deterministic matchers settled everything.
    """
    if not requirements:  # pragma: no cover - guarded by the caller
        raise ValueError("semantic evaluation needs at least one requirement")

    prompts = [
        RequirementPrompt(index=index, text=item.text, category=item.category.value)
        for index, item in enumerate(requirements)
    ]
    expected = {prompt.index for prompt in prompts}

    validation_errors: str | None = None
    last_error = "unknown"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = build_request(
            parsed_document.full_text,
            prompts,
            attempt=attempt,
            validation_errors=validation_errors,
        )

        try:
            response = client.complete(request)
        except LlmProviderError as exc:
            _record_call(
                db,
                candidate_id=candidate.id,
                job_id=candidate.job_id,
                input_sha256=request.input_sha256,
                model="unknown",
                source=LlmSource.LIVE,
                status=LlmStatus.PROVIDER_ERROR,
                attempt=attempt,
                error_detail=str(exc),
            )
            raise ExtractionFailedError(
                "The language model could not be reached. No match results were written."
            ) from exc

        output, error = _validate(response.text, expected)

        if output is None:
            last_error = error or "unknown validation failure"
            _record_call(
                db,
                candidate_id=candidate.id,
                job_id=candidate.job_id,
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
                "Semantic matching attempt %s/%s failed validation for candidate %s",
                attempt,
                MAX_ATTEMPTS,
                candidate.id,
            )
            validation_errors = last_error
            continue

        call_log = _record_call(
            db,
            candidate_id=candidate.id,
            job_id=candidate.job_id,
            input_sha256=request.input_sha256,
            model=response.model,
            source=response.source,
            status=LlmStatus.SUCCESS,
            attempt=attempt,
            response=response,
        )

        by_index = {item.index: item for item in output.verdicts}
        proposals = [
            SemanticProposal(
                requirement_id=requirements[index].id,
                verdict=by_index[index].verdict,
                evidence_quote=by_index[index].evidence_quote,
                reason=by_index[index].reason,
            )
            for index in sorted(expected)
        ]
        return SemanticEvaluation(
            proposals=proposals,
            llm_call_id=call_log.id,
            source=response.source,
            attempts=attempt,
        )

    raise ExtractionFailedError(
        "The language model returned output this application could not use, twice. "
        "No match results were written.",
        details={"attempts": MAX_ATTEMPTS, "last_error": last_error},
    )
