"""Stage 7: parsed CV text -> a typed candidate profile.

The same division of labour as requirement extraction (ADR-0001): **the model
decides what the text says; this code decides whether that is usable.** Nothing
the model returns is persisted until it has passed local validation, and every
item it returns is checked against our own copy of the document before it can
support anything downstream.

Retry policy is the Phase 4 policy, unchanged: exactly one retry on schema
failure, carrying the specific validation errors so the second attempt can
correct the first. A provider failure is *not* a malformed reply -- there is
nothing to validate and nothing a retry would fix -- so it ends the stage
immediately. Two validation failures end it too, and nothing partial is written.

What this stage deliberately does not persist:

* **no sensitive attribute** -- the profile tables have no column for one and
  the LLM schema has no field for one (ADR-0003);
* **no score, rank or recommendation** -- there is no field for those either;
* **no unquoted claim** -- every item carries an evidence span, verified or
  explicitly flagged as unverified.

The candidate's printed name is the one non-job-relevant value extracted, and it
goes to ``candidate.display_name`` -- the display entity -- so that the matcher,
which reads only profile tables, is structurally incapable of seeing it.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.enums import (
    CandidateFailureReason,
    CandidateStatus,
    DatePrecision,
    EvidenceVerification,
    LlmPurpose,
    LlmSource,
    LlmStatus,
)
from app.core.errors import ConflictError, ExtractionFailedError, NotFoundError
from app.llm.client import LlmClient, LlmProviderError, LlmResponse
from app.llm.prompts.profile_extraction import PROMPT_VERSION, build_request
from app.models.audit import LlmCallLog
from app.models.candidate import Candidate, ParsedDocument
from app.models.evaluation import EvidenceSpan
from app.models.profile import (
    CandidateProfile,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
)
from app.schemas.llm.profile_extraction import (
    ExtractedExperience,
    ProfileExtractionOutput,
)
from app.services import candidates as candidates_service
from app.services.evidence import SpanWriter
from app.services.matching import normalize_skill_name

logger = logging.getLogger(__name__)

#: How much of a malformed reply to keep for debugging. Enough to see the shape
#: of the problem, bounded so a runaway reply cannot bloat the table.
RAW_RESPONSE_EXCERPT_LIMIT = 2000

#: The single retry permitted by the architecture.
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ProfileExtractionResult:
    """What the caller needs to report the outcome."""

    profile: CandidateProfile
    llm_call_id: uuid.UUID | None
    source: LlmSource | None
    attempts: int

    #: True when no model call was made: either this candidate already had a
    #: profile, or an identical document had already been extracted.
    cache_hit: bool = False


def _record_call(
    db: Session,
    *,
    candidate_id: uuid.UUID,
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

    Committed on its own because a failed extraction ends in an exception: if
    these rows shared the caller's transaction they would roll back with it and
    the failure would leave no trace.

    The CV text is never stored here -- only its hash. Uploaded CVs are personal
    data and must not end up in a log table that gets exported or backed up.
    """
    log = LlmCallLog(
        purpose=LlmPurpose.PROFILE_EXTRACTION,
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
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _validate(raw_text: str) -> tuple[ProfileExtractionOutput | None, str | None]:
    """Parse and validate a raw reply.

    Returns ``(output, None)`` on success or ``(None, error_description)`` on
    failure. The description is written back to the model on the retry, so it is
    phrased to be actionable rather than merely diagnostic.
    """
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return None, f"The reply was not valid JSON: {exc.msg} (at position {exc.pos})."

    try:
        output = ProfileExtractionOutput.model_validate(payload)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "(root)"
            lines.append(f"- {location}: {error['msg']}")
        return None, "The reply did not match the required structure:\n" + "\n".join(lines)

    return output, None


# --------------------------------------------------------------------------
# Dates: the model reports the precision the CV gives; we store it
# --------------------------------------------------------------------------


def parse_partial_date(value: str | None) -> tuple[date | None, DatePrecision]:
    """Turn ``YYYY``/``YYYY-MM``/``YYYY-MM-DD`` into a DATE and its precision.

    Missing parts default to January and the 1st, which is what
    ``date_precision`` exists to record: without it, a stored DATE would invent
    precision the document never had, and the duration matcher could not state
    its own uncertainty (docs/data-model.md section 4.8).
    """
    if not value:
        return None, DatePrecision.UNKNOWN

    parts = value.split("-")
    year = int(parts[0])
    if len(parts) == 1:
        return date(year, 1, 1), DatePrecision.YEAR
    month = int(parts[1])
    if len(parts) == 2:
        return date(year, month, 1), DatePrecision.MONTH
    return date(year, month, int(parts[2])), DatePrecision.DAY


def _experience_dates(
    item: ExtractedExperience,
) -> tuple[date | None, date | None, DatePrecision, bool]:
    """Resolve one role's dates, precision and ongoing flag.

    The coarser of the two precisions wins: a role known to the month at one end
    and only to the year at the other is a year-precision role, and claiming
    otherwise would overstate what the CV says.

    ``is_current`` is taken from the model *unless* the CV also gave an end
    date. A role with a stated end is not ongoing; preferring the concrete date
    over the flag resolves the contradiction without inventing anything.
    """
    start, start_precision = parse_partial_date(item.start_date)
    end, end_precision = parse_partial_date(item.end_date)

    order = [DatePrecision.DAY, DatePrecision.MONTH, DatePrecision.YEAR, DatePrecision.UNKNOWN]
    known = [p for p in (start_precision, end_precision) if p is not DatePrecision.UNKNOWN]
    precision = max(known, key=order.index) if known else DatePrecision.UNKNOWN

    is_current = item.is_current and end is None
    return start, end, precision, is_current


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def get_profile(db: Session, candidate_id: uuid.UUID) -> CandidateProfile | None:
    return db.scalar(select(CandidateProfile).where(CandidateProfile.candidate_id == candidate_id))


def _require_parsed_document(db: Session, candidate: Candidate) -> ParsedDocument:
    parsed = candidates_service.get_parsed_document(db, candidate.id)
    if parsed is None:
        raise ConflictError(
            "This candidate has no extracted text, so there is nothing to read. "
            f"Its processing status is {candidate.status.value}."
        )
    return parsed


def extract_profile(
    db: Session, candidate_id: uuid.UUID, client: LlmClient
) -> ProfileExtractionResult:
    """Extract and persist one candidate's profile.

    Idempotent by design: a candidate that already has a profile is returned
    unchanged, and a document whose text has already been extracted for another
    candidate is copied rather than re-read. Both paths issue no model call,
    which is what ``parsed_document.text_sha256`` exists for
    (docs/data-model.md section 8).
    """
    candidate = candidates_service.get_candidate(db, candidate_id)
    parsed = _require_parsed_document(db, candidate)

    existing = get_profile(db, candidate_id)
    if existing is not None:
        return ProfileExtractionResult(
            profile=existing,
            llm_call_id=existing.llm_call_id,
            source=None,
            attempts=0,
            cache_hit=True,
        )

    cached = _copy_identical_document_profile(db, candidate, parsed)
    if cached is not None:
        return cached

    return _extract_from_model(db, candidate, parsed, client)


def _extract_from_model(
    db: Session,
    candidate: Candidate,
    parsed: ParsedDocument,
    client: LlmClient,
) -> ProfileExtractionResult:
    candidate.status = CandidateStatus.EXTRACTING
    candidate.stage_started_at = datetime.now(timezone.utc)
    candidate.failure_reason = None
    candidate.failure_detail = None
    db.commit()

    validation_errors: str | None = None
    last_error = "unknown"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = build_request(
            parsed.full_text, attempt=attempt, validation_errors=validation_errors
        )

        try:
            response = client.complete(request)
        except LlmProviderError as exc:
            _record_call(
                db,
                candidate_id=candidate.id,
                input_sha256=request.input_sha256,
                model="unknown",
                source=LlmSource.LIVE,
                status=LlmStatus.PROVIDER_ERROR,
                attempt=attempt,
                error_detail=str(exc),
            )
            _mark_failed(db, candidate, "The language model could not be reached.")
            raise ExtractionFailedError(
                "The language model could not be reached. No profile was written."
            ) from exc

        output, error = _validate(response.text)

        if output is None:
            last_error = error or "unknown validation failure"
            _record_call(
                db,
                candidate_id=candidate.id,
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
                "Profile extraction attempt %s/%s failed validation for candidate %s",
                attempt,
                MAX_ATTEMPTS,
                candidate.id,
            )
            validation_errors = last_error
            continue

        call_log = _record_call(
            db,
            candidate_id=candidate.id,
            input_sha256=request.input_sha256,
            model=response.model,
            source=response.source,
            status=LlmStatus.SUCCESS,
            attempt=attempt,
            response=response,
        )
        profile = _persist(db, candidate, parsed, output, call_log.id)
        return ProfileExtractionResult(
            profile=profile,
            llm_call_id=call_log.id,
            source=response.source,
            attempts=attempt,
        )

    # Both attempts failed validation. Nothing was written to the profile
    # tables; the two SCHEMA_INVALID rows are the failure record.
    _mark_failed(db, candidate, "The language model returned unusable output twice.")
    raise ExtractionFailedError(
        "The language model returned output this application could not use, twice. "
        "No profile was written.",
        details={"attempts": MAX_ATTEMPTS, "last_error": last_error},
    )


def _mark_failed(db: Session, candidate: Candidate, detail: str) -> None:
    """Record an honest failure on the candidate.

    A failed extraction is visible in the candidate's own state, not only in an
    HTTP response that nobody stored. The database enforces that a FAILED
    candidate carries a reason.
    """
    candidate.status = CandidateStatus.FAILED
    candidate.failure_reason = CandidateFailureReason.EXTRACTION_FAILED
    candidate.failure_detail = detail
    candidate.stage_started_at = None
    db.commit()


def _persist(
    db: Session,
    candidate: Candidate,
    parsed: ParsedDocument,
    output: ProfileExtractionOutput,
    llm_call_id: uuid.UUID,
) -> CandidateProfile:
    """Write the profile, its items, and one evidence span per distinct quote."""
    profile = CandidateProfile(
        candidate_id=candidate.id,
        parsed_document_id=parsed.id,
        llm_call_id=llm_call_id,
        prompt_version=PROMPT_VERSION,
    )
    db.add(profile)
    db.flush()

    writer = SpanWriter(db, parsed)

    for item in output.skills:
        span, _ = writer.add(item.evidence_quote)
        db.add(
            ProfileSkill(
                profile_id=profile.id,
                raw_name=item.name,
                # Computed by our code, never by the model: this is what the
                # deterministic matcher indexes.
                normalized_name=normalize_skill_name(item.name),
                evidence_span_id=span.id,
            )
        )

    for item in output.experience:
        span, _ = writer.add(item.evidence_quote)
        start, end, precision, is_current = _experience_dates(item)
        db.add(
            ProfileExperience(
                profile_id=profile.id,
                role_title=item.role_title,
                organization=item.organization,
                start_date=start,
                end_date=end,
                date_precision=precision,
                is_current=is_current,
                description=item.description,
                evidence_span_id=span.id,
            )
        )

    for item in output.education:
        span, _ = writer.add(item.evidence_quote)
        db.add(
            ProfileEducation(
                profile_id=profile.id,
                degree=item.degree,
                field_of_study=item.field_of_study,
                institution=item.institution,
                completion_year=item.completion_year,
                evidence_span_id=span.id,
            )
        )

    for item in output.projects:
        span, _ = writer.add(item.evidence_quote)
        db.add(
            ProfileProject(
                profile_id=profile.id,
                name=item.name,
                description=item.description,
                technologies=list(item.technologies) or None,
                evidence_span_id=span.id,
            )
        )

    # Display only, and on the candidate rather than the profile: the matcher
    # reads profile tables and therefore cannot see this at all.
    if output.display_name:
        candidate.display_name = output.display_name

    candidate.status = CandidateStatus.EXTRACTED
    candidate.stage_started_at = None
    candidate.failure_reason = None
    candidate.failure_detail = None

    db.commit()
    db.refresh(profile)
    return profile


# --------------------------------------------------------------------------
# Cache: an identical document has already been read
# --------------------------------------------------------------------------


def _copy_identical_document_profile(
    db: Session,
    candidate: Candidate,
    parsed: ParsedDocument,
) -> ProfileExtractionResult | None:
    """Reuse an extraction of byte-identical document text, if one exists.

    Two uploads of the same CV produce the same normalized text and the same
    ``text_sha256``, so a second model call would pay again for an answer we
    already have. The items are copied and their quotes **re-verified** against
    this document rather than having offsets copied across: the text is
    identical, so verification is cheap, and re-running it means no span is ever
    asserted to be located somewhere nobody checked.

    **Scoped to the job.** The lookup is deliberately restricted to candidates of
    the same job, even though the hash would match across the whole table.
    docs/architecture.md section 13 states that each job is an island in the MVP
    and that candidates are not queried across jobs; a cache that reached across
    them would be exactly that query, arriving through a side door. The case
    worth optimising is the one this covers anyway -- the same CV submitted twice
    to the same opening. The cost of the restriction is one extra extraction when
    somebody applies to two jobs with an identical file.
    """
    source_profile = db.scalar(
        select(CandidateProfile)
        .join(ParsedDocument, ParsedDocument.id == CandidateProfile.parsed_document_id)
        .join(Candidate, Candidate.id == CandidateProfile.candidate_id)
        .where(
            ParsedDocument.text_sha256 == parsed.text_sha256,
            ParsedDocument.id != parsed.id,
            Candidate.job_id == candidate.job_id,
        )
        .order_by(CandidateProfile.created_at)
        .limit(1)
    )
    if source_profile is None:
        return None

    profile = CandidateProfile(
        candidate_id=candidate.id,
        parsed_document_id=parsed.id,
        llm_call_id=source_profile.llm_call_id,
        prompt_version=source_profile.prompt_version,
    )
    db.add(profile)
    db.flush()

    writer = SpanWriter(db, parsed)

    def quote_of(span_id: uuid.UUID | None) -> str | None:
        if span_id is None:
            return None
        span = db.get(EvidenceSpan, span_id)
        return span.quoted_text if span else None

    def copied_span(span_id: uuid.UUID | None) -> uuid.UUID | None:
        quote = quote_of(span_id)
        if quote is None:
            return None
        span, _ = writer.add(quote)
        return span.id

    for skill in db.scalars(
        select(ProfileSkill).where(ProfileSkill.profile_id == source_profile.id)
    ):
        db.add(
            ProfileSkill(
                profile_id=profile.id,
                raw_name=skill.raw_name,
                normalized_name=skill.normalized_name,
                evidence_span_id=copied_span(skill.evidence_span_id),
            )
        )

    for experience in db.scalars(
        select(ProfileExperience).where(ProfileExperience.profile_id == source_profile.id)
    ):
        db.add(
            ProfileExperience(
                profile_id=profile.id,
                role_title=experience.role_title,
                organization=experience.organization,
                start_date=experience.start_date,
                end_date=experience.end_date,
                date_precision=experience.date_precision,
                is_current=experience.is_current,
                description=experience.description,
                evidence_span_id=copied_span(experience.evidence_span_id),
            )
        )

    for education in db.scalars(
        select(ProfileEducation).where(ProfileEducation.profile_id == source_profile.id)
    ):
        db.add(
            ProfileEducation(
                profile_id=profile.id,
                degree=education.degree,
                field_of_study=education.field_of_study,
                institution=education.institution,
                completion_year=education.completion_year,
                evidence_span_id=copied_span(education.evidence_span_id),
            )
        )

    for project in db.scalars(
        select(ProfileProject).where(ProfileProject.profile_id == source_profile.id)
    ):
        db.add(
            ProfileProject(
                profile_id=profile.id,
                name=project.name,
                description=project.description,
                technologies=list(project.technologies) if project.technologies else None,
                evidence_span_id=copied_span(project.evidence_span_id),
            )
        )

    source_candidate = db.get(Candidate, source_profile.candidate_id)
    if source_candidate is not None and source_candidate.display_name:
        candidate.display_name = source_candidate.display_name

    candidate.status = CandidateStatus.EXTRACTED
    candidate.stage_started_at = None
    candidate.failure_reason = None
    candidate.failure_detail = None

    db.commit()
    db.refresh(profile)
    logger.info(
        "Profile for candidate %s copied from an identical document; no model call made",
        candidate.id,
    )
    return ProfileExtractionResult(
        profile=profile,
        llm_call_id=profile.llm_call_id,
        source=None,
        attempts=0,
        cache_hit=True,
    )


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileItems:
    """A profile and its four item lists, in one round trip."""

    profile: CandidateProfile
    skills: list[ProfileSkill]
    experience: list[ProfileExperience]
    education: list[ProfileEducation]
    projects: list[ProfileProject]
    spans: dict[uuid.UUID, EvidenceSpan]

    @property
    def item_count(self) -> int:
        return len(self.skills) + len(self.experience) + len(self.education) + len(self.projects)

    @property
    def verified_evidence_count(self) -> int:
        """How many items cite a span that was actually located in the document."""
        return sum(
            1
            for span_id in self._cited_span_ids()
            if span_id in self.spans
            and self.spans[span_id].verification_status is not EvidenceVerification.UNVERIFIED
        )

    def _cited_span_ids(self) -> list[uuid.UUID]:
        return [
            item.evidence_span_id
            for group in (self.skills, self.experience, self.education, self.projects)
            for item in group
            if item.evidence_span_id is not None
        ]


def load_profile_items(db: Session, profile: CandidateProfile) -> ProfileItems:
    """Load a profile's items and every span they cite."""
    skills = list(
        db.scalars(
            select(ProfileSkill)
            .where(ProfileSkill.profile_id == profile.id)
            .order_by(ProfileSkill.normalized_name)
        )
    )
    experience = list(
        db.scalars(
            select(ProfileExperience)
            .where(ProfileExperience.profile_id == profile.id)
            .order_by(ProfileExperience.start_date.desc().nullslast())
        )
    )
    education = list(
        db.scalars(
            select(ProfileEducation)
            .where(ProfileEducation.profile_id == profile.id)
            .order_by(ProfileEducation.completion_year.desc().nullslast())
        )
    )
    projects = list(
        db.scalars(
            select(ProfileProject)
            .where(ProfileProject.profile_id == profile.id)
            .order_by(ProfileProject.name)
        )
    )

    span_ids = {
        item.evidence_span_id
        for group in (skills, experience, education, projects)
        for item in group
        if item.evidence_span_id is not None
    }
    spans = (
        {
            span.id: span
            for span in db.scalars(select(EvidenceSpan).where(EvidenceSpan.id.in_(span_ids)))
        }
        if span_ids
        else {}
    )

    return ProfileItems(
        profile=profile,
        skills=skills,
        experience=experience,
        education=education,
        projects=projects,
        spans=spans,
    )


def require_profile(db: Session, candidate_id: uuid.UUID) -> CandidateProfile:
    """The profile for a candidate, or a clear refusal."""
    candidates_service.get_candidate(db, candidate_id)
    profile = get_profile(db, candidate_id)
    if profile is None:
        raise NotFoundError(
            f"Candidate {candidate_id} has no extracted profile yet. Run extraction first."
        )
    return profile


def delete_profile(db: Session, candidate_id: uuid.UUID) -> None:
    """Remove a candidate's profile. Used when a document is replaced."""
    db.execute(delete(CandidateProfile).where(CandidateProfile.candidate_id == candidate_id))
    db.commit()
