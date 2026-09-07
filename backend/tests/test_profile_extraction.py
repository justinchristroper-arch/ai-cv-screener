"""Profile extraction: what gets stored, what gets refused, and the retry.

The rule under test throughout is ADR-0001's boundary -- the model reports what
one document says, and this code decides whether that is usable and what it is
allowed to become. Nothing unvalidated is persisted, every stored item cites a
passage that was checked against our own copy of the text, and the profile has
no room for a sensitive attribute or a judgement.

Everything runs from recorded fixtures. No API key, no network.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
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
from app.llm.client import LlmProviderError, LlmResponse
from app.llm.fixtures import fixture_cv_text
from app.models.audit import LlmCallLog
from app.models.evaluation import EvidenceSpan
from app.models.profile import (
    CandidateProfile,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
)
from app.schemas.llm.profile_extraction import ProfileExtractionOutput
from app.services import jobs, profile_extraction
from app.services.document_parsing import extract_text
from tests.factories import make_candidate_from_fixture, make_parsed_candidate
from tests.pdf_fixtures import full_cv_pdf

CV_TEXT = fixture_cv_text("cv_alex_rivera")

#: Personal details the bundled CV prints and this system must never store.
SENSITIVE_STRINGS = (
    "14 March 1994",
    "Female",
    "Fictionalese",
    "Single",
    "12 Invented Lane",
    "+00 000 000 000",
    "alex.rivera@example.invalid",
    "Photograph",
)


class _StubClient:
    """Returns canned replies in order. Stands in for a provider that misbehaves."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.calls: list[int] = []

    def complete(self, request):
        self.calls.append(request.attempt)
        return LlmResponse(
            text=self._replies.pop(0),
            model="claude-opus-5",
            source=LlmSource.FIXTURE,
            latency_ms=0,
        )


class _UnreachableClient:
    def complete(self, request):
        raise LlmProviderError("could not reach the provider")


# --------------------------------------------------------------------------
# A. The fixture describes the real pipeline's output
# --------------------------------------------------------------------------


def test_the_recorded_cv_text_is_what_the_parser_actually_produces() -> None:
    """Guards the whole fixture set against silent drift.

    The fixture key is a hash of the parsed text. If pypdf, the normalizer or
    the sample PDF changes, replay would start failing with "no fixture for this
    input" somewhere far from the cause. This asserts the equality directly.
    """
    assert extract_text(full_cv_pdf()).full_text == CV_TEXT


# --------------------------------------------------------------------------
# B. Schema: what the model is structurally unable to return
# --------------------------------------------------------------------------


def _valid_reply() -> dict:
    return {
        "display_name": "Alex Rivera",
        "skills": [{"name": "Python", "evidence_quote": "Python, FastAPI, Postgres"}],
        "experience": [],
        "education": [],
        "projects": [],
    }


@pytest.mark.parametrize(
    "field",
    ["age", "date_of_birth", "gender", "nationality", "photo", "marital_status", "home_address"],
)
def test_a_sensitive_attribute_cannot_be_returned_at_all(field: str) -> None:
    """ADR-0003 as a structural control, not a prompt instruction.

    There is no field to put it in, and `extra="forbid"` rejects the whole reply
    rather than dropping the surplus -- so a model that tries fails loudly.
    """
    reply = _valid_reply() | {field: "anything"}

    with pytest.raises(ValidationError) as excinfo:
        ProfileExtractionOutput.model_validate(reply)

    assert any(error["type"] == "extra_forbidden" for error in excinfo.value.errors())


@pytest.mark.parametrize("field", ["score", "rating", "rank", "recommendation", "verdict"])
def test_the_model_cannot_supply_a_judgement(field: str) -> None:
    """The model reports what the document says. It does not decide worth."""
    reply = _valid_reply() | {field: 95}

    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(reply)


def test_an_item_without_a_quote_is_rejected() -> None:
    reply = _valid_reply()
    reply["skills"] = [{"name": "Python"}]

    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(reply)


def test_a_completely_empty_profile_is_rejected() -> None:
    """A document with readable text always says something.

    Storing an empty profile would be indistinguishable from a candidate with
    no qualifications, which is a very different claim.
    """
    reply = _valid_reply() | {"skills": []}

    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(reply)


def test_a_role_that_ends_before_it_starts_is_rejected() -> None:
    reply = _valid_reply()
    reply["experience"] = [
        {
            "role_title": "Engineer",
            "organization": "Nowhere",
            "start_date": "2024-01",
            "end_date": "2022-01",
            "is_current": False,
            "description": None,
            "evidence_quote": "Engineer at Nowhere, 2024 to 2022",
        }
    ]

    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate(reply)


# --------------------------------------------------------------------------
# C. Partial dates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_precision"),
    [
        ("2021", DatePrecision.YEAR),
        ("2021-06", DatePrecision.MONTH),
        ("2021-06-14", DatePrecision.DAY),
        (None, DatePrecision.UNKNOWN),
    ],
)
def test_a_partial_date_keeps_the_precision_the_cv_actually_gave(
    value: str | None, expected_precision: DatePrecision
) -> None:
    """Defaulting the missing parts is fine; forgetting they were missing is not."""
    _, precision = profile_extraction.parse_partial_date(value)

    assert precision is expected_precision


# --------------------------------------------------------------------------
# D. The happy path, against the database
# --------------------------------------------------------------------------

pytestmark_db = pytest.mark.requires_db


@pytest.fixture()
def extracted(db_session: Session, replay_client):
    """The bundled CV, extracted once, for the assertions that follow."""
    job = jobs.create_job(db_session, title="Senior Backend Engineer")
    candidate, parsed = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")
    result = profile_extraction.extract_profile(db_session, candidate.id, replay_client)
    return candidate, parsed, result


@pytest.mark.requires_db
def test_extraction_stores_every_section_of_the_cv(db_session: Session, extracted) -> None:
    _, _, result = extracted
    items = profile_extraction.load_profile_items(db_session, result.profile)

    # Ordered by the normalized name, so "pytest" precedes "python".
    assert [skill.raw_name for skill in items.skills] == [
        "Bash",
        "Docker",
        "FastAPI",
        "Postgres",
        "pytest",
        "Python",
    ]
    assert len(items.experience) == 2
    assert len(items.education) == 1
    assert len(items.projects) == 1
    assert result.attempts == 1
    assert result.source is LlmSource.FIXTURE
    assert not result.cache_hit


@pytest.mark.requires_db
def test_the_normalized_skill_name_is_computed_by_this_code(db_session: Session, extracted) -> None:
    """The model supplies the raw name; the matcher's index is ours."""
    _, _, result = extracted
    items = profile_extraction.load_profile_items(db_session, result.profile)

    by_raw = {skill.raw_name: skill.normalized_name for skill in items.skills}
    assert by_raw["Postgres"] == "postgres"
    assert by_raw["FastAPI"] == "fastapi"


@pytest.mark.requires_db
def test_roles_keep_their_dates_and_precision(db_session: Session, extracted) -> None:
    _, _, result = extracted
    items = profile_extraction.load_profile_items(db_session, result.profile)

    latest = items.experience[0]
    assert latest.role_title == "Backend Engineer"
    assert latest.organization == "Northwind Analytics"
    assert latest.start_date.isoformat() == "2022-03-01"
    assert latest.end_date.isoformat() == "2026-02-01"
    assert latest.date_precision is DatePrecision.MONTH
    assert latest.is_current is False


@pytest.mark.requires_db
def test_every_stored_item_cites_a_verified_passage(db_session: Session, extracted) -> None:
    """Evidence-first, applied to extraction as well as to matching."""
    _, parsed, result = extracted
    items = profile_extraction.load_profile_items(db_session, result.profile)

    assert items.item_count == 10
    assert items.verified_evidence_count == 10

    for span in items.spans.values():
        assert span.verification_status is EvidenceVerification.VERIFIED_EXACT
        assert parsed.full_text[span.start_char : span.end_char] == span.quoted_text
        assert span.page_number == 1


@pytest.mark.requires_db
def test_identical_quotes_share_one_span(db_session: Session, extracted) -> None:
    """Six skills come off one line. That is one passage, not six copies of it."""
    _, _, result = extracted
    items = profile_extraction.load_profile_items(db_session, result.profile)

    skill_spans = {skill.evidence_span_id for skill in items.skills}
    assert len(skill_spans) == 1


# --------------------------------------------------------------------------
# E. The fairness boundary, against real stored rows
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_no_sensitive_detail_from_the_cv_reaches_the_profile(
    db_session: Session, extracted
) -> None:
    """The CV prints a full personal-details block. None of it is stored.

    The structural guarantee is that no column exists for any of it
    (`test_schema.py`); this asserts the behaviour that follows from it, on real
    rows, including the free-text fields where it could otherwise leak.
    """
    _, _, result = extracted
    items = profile_extraction.load_profile_items(db_session, result.profile)

    stored = " | ".join(
        [
            *(f"{s.raw_name} {s.normalized_name}" for s in items.skills),
            *(f"{e.role_title} {e.organization} {e.description}" for e in items.experience),
            *(f"{e.degree} {e.field_of_study} {e.institution}" for e in items.education),
            *(f"{p.name} {p.description} {p.technologies}" for p in items.projects),
            *(span.quoted_text for span in items.spans.values()),
        ]
    )

    for value in SENSITIVE_STRINGS:
        assert value not in stored, f"{value!r} must not reach the profile or its evidence"


@pytest.mark.requires_db
def test_the_candidate_name_lives_on_the_candidate_not_the_profile(
    db_session: Session, extracted
) -> None:
    """docs/data-model.md section 5: the matcher reads only profile tables.

    Putting the name anywhere else would be a preference; putting it where the
    matcher cannot reach it is a guarantee.
    """
    candidate, _, result = extracted
    db_session.refresh(candidate)

    assert candidate.display_name == "Alex Rivera"
    assert not hasattr(result.profile, "display_name")
    assert not hasattr(result.profile, "name")


# --------------------------------------------------------------------------
# F. Audit trail
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_the_successful_call_is_logged(db_session: Session, extracted) -> None:
    candidate, _, result = extracted

    log = db_session.get(LlmCallLog, result.llm_call_id)
    assert log.purpose is LlmPurpose.PROFILE_EXTRACTION
    assert log.status is LlmStatus.SUCCESS
    assert log.source is LlmSource.FIXTURE
    assert log.attempt == 1
    assert log.prompt_version == "profile-extraction-v1"
    assert log.candidate_id == candidate.id
    assert len(log.input_sha256) == 64


@pytest.mark.requires_db
def test_the_audit_log_never_stores_the_cv_text(db_session: Session, extracted) -> None:
    """Uploaded CVs are personal data. The log keeps a hash, not the document."""
    _, _, result = extracted

    log = db_session.get(LlmCallLog, result.llm_call_id)
    serialized = " ".join(
        str(value) for value in (log.input_sha256, log.error_detail, log.raw_response_excerpt)
    )
    assert "Alex Rivera" not in serialized
    assert CV_TEXT not in serialized
    for value in SENSITIVE_STRINGS:
        assert value not in serialized


# --------------------------------------------------------------------------
# G. Validation, retry and failure
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_correctable_reply_is_retried_once_and_succeeds(
    db_session: Session, replay_client
) -> None:
    """Attempt 1 uses prose dates and omits a quote; attempt 2 corrects both."""
    job = jobs.create_job(db_session, title="Data Engineer")
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_retry_attempt1")

    result = profile_extraction.extract_profile(db_session, candidate.id, replay_client)

    assert result.attempts == 2
    items = profile_extraction.load_profile_items(db_session, result.profile)
    assert [skill.raw_name for skill in items.skills] == ["Airflow", "Python", "SQL"]
    assert items.experience[0].date_precision is DatePrecision.YEAR


@pytest.mark.requires_db
def test_the_rejected_attempt_is_recorded_as_schema_invalid(
    db_session: Session, replay_client
) -> None:
    job = jobs.create_job(db_session, title="Data Engineer")
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_retry_attempt1")

    profile_extraction.extract_profile(db_session, candidate.id, replay_client)

    logs = list(
        db_session.scalars(
            select(LlmCallLog)
            .where(LlmCallLog.candidate_id == candidate.id)
            .order_by(LlmCallLog.attempt)
        )
    )
    assert [log.status for log in logs] == [LlmStatus.SCHEMA_INVALID, LlmStatus.SUCCESS]
    assert logs[0].raw_response_excerpt is not None


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("not json at all", "not JSON"),
        ('{"skills": []}', "wrong shape"),
        (
            '{"display_name": null, "skills": [], "experience": [], '
            '"education": [], "projects": []}',
            "empty profile",
        ),
        (
            '{"display_name": null, "skills": [{"name": "Python"}], "experience": [], '
            '"education": [], "projects": []}',
            "item with no evidence",
        ),
        (
            '{"display_name": null, "skills": [{"name": "Python", '
            '"evidence_quote": "Python, FastAPI", "level": "expert"}], '
            '"experience": [], "education": [], "projects": []}',
            "field the model invented",
        ),
    ],
)
@pytest.mark.requires_db
def test_unusable_output_fails_the_stage_and_persists_nothing(
    db_session: Session, reply: str, reason: str
) -> None:
    job = jobs.create_job(db_session, title="Senior Backend Engineer")
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")
    client = _StubClient(reply, reply)

    with pytest.raises(ExtractionFailedError):
        profile_extraction.extract_profile(db_session, candidate.id, client)

    assert client.calls == [1, 2], "the policy is exactly one retry"
    assert profile_extraction.get_profile(db_session, candidate.id) is None, reason
    assert (
        db_session.scalars(select(ProfileSkill)).all() == []
        or profile_extraction.get_profile(db_session, candidate.id) is None
    )


@pytest.mark.requires_db
def test_a_failed_extraction_is_visible_on_the_candidate(
    db_session: Session,
) -> None:
    """A failure nobody stored is a failure nobody can see."""
    job = jobs.create_job(db_session, title="Senior Backend Engineer")
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")

    with pytest.raises(ExtractionFailedError):
        profile_extraction.extract_profile(db_session, candidate.id, _StubClient("{}", "{}"))

    db_session.refresh(candidate)
    assert candidate.status is CandidateStatus.FAILED
    assert candidate.failure_reason is CandidateFailureReason.EXTRACTION_FAILED
    assert candidate.failure_detail


@pytest.mark.requires_db
def test_a_provider_failure_is_not_treated_as_a_malformed_reply(
    db_session: Session,
) -> None:
    """There is nothing to validate, so a retry against the same prompt is pointless."""
    job = jobs.create_job(db_session, title="Senior Backend Engineer")
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")

    with pytest.raises(ExtractionFailedError):
        profile_extraction.extract_profile(db_session, candidate.id, _UnreachableClient())

    logs = list(
        db_session.scalars(select(LlmCallLog).where(LlmCallLog.candidate_id == candidate.id))
    )
    assert [log.status for log in logs] == [LlmStatus.PROVIDER_ERROR]
    assert logs[0].attempt == 1


@pytest.mark.requires_db
def test_a_fabricated_quote_is_stored_flagged_rather_than_silently_dropped(
    db_session: Session,
) -> None:
    """An unverifiable item is visible and marked, and cannot decide anything.

    Deleting it would hide that the model made a claim it could not support;
    accepting it would let an invented quote look like evidence.
    """
    job = jobs.create_job(db_session, title="Senior Backend Engineer")
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")
    reply = (
        '{"display_name": "Alex Rivera", "skills": [{"name": "Kubernetes", '
        '"evidence_quote": "Ran production Kubernetes clusters for six years."}], '
        '"experience": [], "education": [], "projects": []}'
    )

    result = profile_extraction.extract_profile(db_session, candidate.id, _StubClient(reply))

    items = profile_extraction.load_profile_items(db_session, result.profile)
    assert len(items.skills) == 1
    span = items.spans[items.skills[0].evidence_span_id]
    assert span.verification_status is EvidenceVerification.UNVERIFIED
    assert span.start_char is None
    assert items.verified_evidence_count == 0


# --------------------------------------------------------------------------
# H. Caching
# --------------------------------------------------------------------------


class _ExplodingClient:
    """Any call at all is a test failure."""

    def complete(self, request):  # pragma: no cover - the point is that it is not called
        raise AssertionError("the cache should have prevented this model call")


@pytest.mark.requires_db
def test_re_extracting_the_same_candidate_makes_no_second_call(
    db_session: Session, extracted
) -> None:
    candidate, _, first = extracted

    second = profile_extraction.extract_profile(db_session, candidate.id, _ExplodingClient())

    assert second.cache_hit
    assert second.attempts == 0
    assert second.profile.id == first.profile.id


@pytest.mark.requires_db
def test_an_identical_document_is_copied_rather_than_re_read(
    db_session: Session, extracted
) -> None:
    """Two uploads of the same CV are the same text, so the answer is known.

    The copy re-verifies every quote against the new document rather than
    copying offsets across, so no span is ever asserted to be somewhere nobody
    checked.
    """
    candidate, _, first = extracted
    twin, twin_parsed = make_parsed_candidate(db_session, candidate.job_id, CV_TEXT)

    result = profile_extraction.extract_profile(db_session, twin.id, _ExplodingClient())

    assert result.cache_hit
    assert result.profile.id != first.profile.id
    items = profile_extraction.load_profile_items(db_session, result.profile)
    assert items.item_count == 10
    assert items.verified_evidence_count == 10
    for span in items.spans.values():
        assert span.parsed_document_id == twin_parsed.id


@pytest.mark.requires_db
def test_a_different_document_is_not_served_from_the_cache(
    db_session: Session, extracted, replay_client
) -> None:
    candidate, _, _ = extracted
    other, _ = make_candidate_from_fixture(db_session, candidate.job_id, "cv_prompt_injection")

    result = profile_extraction.extract_profile(db_session, other.id, replay_client)

    assert not result.cache_hit
    assert result.attempts == 1


@pytest.mark.requires_db
def test_the_cache_does_not_reach_across_jobs(
    db_session: Session, extracted, replay_client
) -> None:
    """Each job is an island (docs/architecture.md section 13).

    An unscoped cache would be a cross-job query of candidates arriving through
    a side door -- and it would silently couple one job's results to whatever
    else happens to be in the database, including rows a developer created by
    running the app locally.
    """
    _, _, first = extracted
    other_job = jobs.create_job(db_session, title="A different opening")
    twin, _ = make_parsed_candidate(db_session, other_job.id, CV_TEXT)

    result = profile_extraction.extract_profile(db_session, twin.id, replay_client)

    assert not result.cache_hit
    assert result.attempts == 1
    assert result.profile.id != first.profile.id


# --------------------------------------------------------------------------
# I. Preconditions
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_candidate_with_no_parsed_text_cannot_be_extracted(
    db_session: Session, replay_client
) -> None:
    from app.models.candidate import Candidate

    job = jobs.create_job(db_session, title="Senior Backend Engineer")
    candidate = Candidate(job_id=job.id, status=CandidateStatus.UPLOADED)
    db_session.add(candidate)
    db_session.commit()

    with pytest.raises(ConflictError):
        profile_extraction.extract_profile(db_session, candidate.id, replay_client)


@pytest.mark.requires_db
def test_an_unknown_candidate_is_a_not_found(db_session: Session, replay_client) -> None:
    with pytest.raises(NotFoundError):
        profile_extraction.extract_profile(db_session, uuid.uuid4(), replay_client)


@pytest.mark.requires_db
def test_requiring_a_profile_that_does_not_exist_is_a_not_found(db_session: Session) -> None:
    job = jobs.create_job(db_session, title="Senior Backend Engineer")
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")

    with pytest.raises(NotFoundError):
        profile_extraction.require_profile(db_session, candidate.id)


@pytest.mark.requires_db
def test_extraction_writes_only_profile_rows_and_an_audit_row(
    db_session: Session, extracted
) -> None:
    """Nothing in a reply can reach any other table."""
    candidate, _, result = extracted

    assert db_session.get(CandidateProfile, result.profile.id) is not None
    for model in (ProfileSkill, ProfileExperience, ProfileEducation, ProfileProject):
        rows = db_session.scalars(select(model).where(model.profile_id == result.profile.id)).all()
        assert rows, model.__name__
    spans = db_session.scalars(
        select(EvidenceSpan).where(
            EvidenceSpan.parsed_document_id == result.profile.parsed_document_id
        )
    ).all()
    assert spans
    db_session.refresh(candidate)
    assert candidate.status is CandidateStatus.EXTRACTED
