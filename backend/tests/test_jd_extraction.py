"""Requirement extraction: what gets accepted, what gets rejected, and the retry.

The rule under test throughout is ADR-0001's boundary — the model proposes text,
a category and a must-have flag; this code decides whether that is usable, and
nothing unvalidated is ever persisted.

Everything here runs from recorded fixtures. No API key, no network.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    JdSourceType,
    LlmSource,
    LlmStatus,
    RequirementCategory,
    RequirementOrigin,
)
from app.core.errors import ConflictError, ExtractionFailedError, NotFoundError
from app.llm.client import LlmProviderError, LlmResponse
from app.llm.fixtures import fixture_jd_text
from app.models.audit import LlmCallLog
from app.models.job import Requirement
from app.schemas.llm.jd_extraction import RequirementExtractionOutput
from app.services import jd_extraction, jobs

pytestmark = pytest.mark.requires_db


def _job_with_description(db: Session, fixture_name: str):
    job = jobs.create_job(db, title="Test job")
    jobs.set_description(
        db,
        job.id,
        raw_text=fixture_jd_text(fixture_name),
        source_type=JdSourceType.PASTED,
    )
    return job


# --------------------------------------------------------------------------
# A. Happy path
# --------------------------------------------------------------------------


def test_extraction_produces_validated_requirements(db_session: Session, replay_client) -> None:
    job = _job_with_description(db_session, "jd_backend_engineer")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)

    assert result.attempts == 1
    assert result.source is LlmSource.FIXTURE
    assert len(result.requirements) == 13
    assert all(item.origin is RequirementOrigin.LLM_EXTRACTED for item in result.requirements)
    assert [item.display_order for item in result.requirements] == list(range(13))


def test_extraction_records_the_proposal_for_later_comparison(
    db_session: Session, replay_client
) -> None:
    """`proposed_*` is the seed of the human-correction dataset (ADR-0004)."""
    job = _job_with_description(db_session, "jd_backend_engineer")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)

    for item in result.requirements:
        assert item.proposed_text == item.text
        assert item.proposed_category == item.category
        assert item.proposed_must_have == item.must_have


def test_extraction_applies_the_confirmed_default_weights(
    db_session: Session, replay_client
) -> None:
    job = _job_with_description(db_session, "jd_backend_engineer")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)

    for item in result.requirements:
        expected = Decimal("3") if item.must_have else Decimal("1")
        assert item.weight == expected


def test_extraction_logs_a_successful_call(db_session: Session, replay_client) -> None:
    job = _job_with_description(db_session, "jd_backend_engineer")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)

    log = db_session.get(LlmCallLog, result.llm_call_id)
    assert log is not None
    assert log.status is LlmStatus.SUCCESS
    assert log.source is LlmSource.FIXTURE
    assert log.attempt == 1
    assert log.prompt_version == "jd-extraction-v2"
    assert len(log.input_sha256) == 64
    assert log.job_id == job.id


def test_the_audit_log_never_stores_the_job_description_text(
    db_session: Session, replay_client
) -> None:
    """Only the hash. The input itself must not reach the log table."""
    job = _job_with_description(db_session, "jd_backend_engineer")
    jd_text = fixture_jd_text("jd_backend_engineer")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)

    log = db_session.get(LlmCallLog, result.llm_call_id)
    serialized = " ".join(
        str(value) for value in (log.input_sha256, log.error_detail, log.raw_response_excerpt)
    )
    assert "Northwind Analytics" not in serialized
    assert jd_text not in serialized


# --------------------------------------------------------------------------
# E. Categories  /  F. Must-have  /  G. Atomicity
# --------------------------------------------------------------------------


def test_extraction_covers_every_requirement_category(db_session: Session, replay_client) -> None:
    job = _job_with_description(db_session, "jd_backend_engineer")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)

    categories = {item.category for item in result.requirements}
    assert categories == {
        RequirementCategory.EDUCATION,
        RequirementCategory.TECHNICAL_SKILL,
        RequirementCategory.EXPERIENCE,
        RequirementCategory.PROJECT,
        RequirementCategory.SOFT_SKILL_OTHER,
    }


def test_extraction_preserves_the_must_have_distinction(db_session: Session, replay_client) -> None:
    """The "Nice to have" section must not become hard requirements."""
    job = _job_with_description(db_session, "jd_backend_engineer")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)
    by_text = {item.text: item.must_have for item in result.requirements}

    assert by_text["At least 5 years of professional experience building backend services"] is True
    assert by_text["Experience with Kubernetes"] is False
    assert by_text["Master's degree in a quantitative discipline"] is False
    assert {True, False} == set(by_text.values())


def test_compound_skill_sentences_become_separate_requirements(
    db_session: Session, replay_client
) -> None:
    """ "Python, FastAPI, PostgreSQL and Docker" is four requirements, not one.

    Atomicity is what makes each requirement independently checkable against a
    CV later (docs/product-spec.md F2). A single collapsed entry could only
    ever be matched all-or-nothing.
    """
    job = _job_with_description(db_session, "jd_backend_engineer")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)
    texts = [item.text for item in result.requirements]

    for skill in ("Python", "FastAPI", "PostgreSQL", "Docker"):
        matching = [text for text in texts if skill in text]
        assert len(matching) == 1, f"expected exactly one requirement naming {skill}"

    # And none of them smuggles the whole compound sentence back in.
    assert not any("Python, FastAPI" in text for text in texts)


# --------------------------------------------------------------------------
# B. Schema validation
# --------------------------------------------------------------------------


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


VALID_REPLY = (
    '{"requirements": [{"text": "Advanced SQL", "category": "TECHNICAL_SKILL", "must_have": true}]}'
)


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("not json at all", "not JSON"),
        ('{"requirements": []}', "empty list"),
        ('{"requirements": [{"text": "x", "category": "NOPE", "must_have": true}]}', "bad enum"),
        ('{"requirements": [{"text": "Advanced SQL", "must_have": true}]}', "missing category"),
        (
            '{"requirements": [{"text": "   ", "category": "EDUCATION", "must_have": true}]}',
            "blank text",
        ),
        (
            '{"requirements": [{"text": "Advanced SQL", "category": "EDUCATION"}]}',
            "missing must_have",
        ),
        ('{"wrong_key": []}', "wrong shape"),
        (
            '{"requirements": [{"text": "Advanced SQL", "category": "EDUCATION", '
            '"must_have": true, "score": 95}]}',
            "extra field the model invented",
        ),
    ],
)
def test_invalid_output_is_rejected(db_session: Session, reply: str, reason: str) -> None:
    """Every one of these must fail twice and persist nothing."""
    job = _job_with_description(db_session, "jd_backend_engineer")
    client = _StubClient(reply, reply)

    with pytest.raises(ExtractionFailedError):
        jd_extraction.extract_requirements(db_session, job.id, client)

    stored = db_session.scalars(select(Requirement).where(Requirement.job_id == job.id)).all()
    assert stored == [], f"malformed output ({reason}) must not be persisted"


def test_a_reply_that_repeats_itself_is_rejected(db_session: Session) -> None:
    """A duplicate is a misread, not something to quietly de-duplicate."""
    job = _job_with_description(db_session, "jd_backend_engineer")
    duplicated = (
        '{"requirements": ['
        '{"text": "Advanced SQL", "category": "TECHNICAL_SKILL", "must_have": true},'
        '{"text": "advanced   sql", "category": "TECHNICAL_SKILL", "must_have": true}]}'
    )
    client = _StubClient(duplicated, duplicated)

    with pytest.raises(ExtractionFailedError):
        jd_extraction.extract_requirements(db_session, job.id, client)


def test_the_model_cannot_supply_a_score_or_a_verdict() -> None:
    """`extra="forbid"` is what keeps the model inside its lane (ADR-0001).

    A reply that tries to attach a score, a rank, or a recommendation is
    rejected outright rather than having the surplus silently dropped.
    """
    for field in ("score", "rank", "recommendation", "verdict"):
        payload = {
            "requirements": [
                {
                    "text": "Advanced SQL",
                    "category": "TECHNICAL_SKILL",
                    "must_have": True,
                    field: 99,
                }
            ]
        }
        with pytest.raises(ValidationError) as excinfo:
            RequirementExtractionOutput.model_validate(payload)
        assert any(error["type"] == "extra_forbidden" for error in excinfo.value.errors())


# --------------------------------------------------------------------------
# C. Retry succeeds  /  D. Retry fails
# --------------------------------------------------------------------------


def test_retry_recovers_from_an_invalid_first_reply(db_session: Session, replay_client) -> None:
    """Attempt 1 has a category outside the taxonomy; attempt 2 corrects it."""
    job = _job_with_description(db_session, "jd_data_analyst_retry_attempt1")

    result = jd_extraction.extract_requirements(db_session, job.id, replay_client)

    assert result.attempts == 2
    assert len(result.requirements) == 5
    assert {item.category for item in result.requirements} >= {RequirementCategory.SOFT_SKILL_OTHER}


def test_both_attempts_are_logged_when_the_retry_succeeds(
    db_session: Session, replay_client
) -> None:
    job = _job_with_description(db_session, "jd_data_analyst_retry_attempt1")

    jd_extraction.extract_requirements(db_session, job.id, replay_client)

    logs = db_session.scalars(
        select(LlmCallLog).where(LlmCallLog.job_id == job.id).order_by(LlmCallLog.attempt)
    ).all()
    assert [log.attempt for log in logs] == [1, 2]
    assert [log.status for log in logs] == [LlmStatus.SCHEMA_INVALID, LlmStatus.SUCCESS]
    # Same input, tried twice: the hash identifies the document, not the prompt.
    assert len({log.input_sha256 for log in logs}) == 1


def test_the_rejected_reply_is_kept_for_debugging(db_session: Session, replay_client) -> None:
    job = _job_with_description(db_session, "jd_data_analyst_retry_attempt1")

    jd_extraction.extract_requirements(db_session, job.id, replay_client)

    failed = db_session.scalar(
        select(LlmCallLog).where(
            LlmCallLog.job_id == job.id, LlmCallLog.status == LlmStatus.SCHEMA_INVALID
        )
    )
    assert failed is not None
    assert failed.raw_response_excerpt is not None
    assert "SOFT_SKILLS" in failed.raw_response_excerpt
    assert failed.error_detail is not None


def test_two_failures_end_the_extraction_cleanly(db_session: Session, replay_client) -> None:
    """Attempt 1 is prose, attempt 2 is JSON that still breaks the schema."""
    job = _job_with_description(db_session, "jd_unrecoverable_attempt1")

    with pytest.raises(ExtractionFailedError) as excinfo:
        jd_extraction.extract_requirements(db_session, job.id, replay_client)

    assert excinfo.value.details["attempts"] == 2
    assert "No requirements were changed" in excinfo.value.message

    stored = db_session.scalars(select(Requirement).where(Requirement.job_id == job.id)).all()
    assert stored == []


def test_a_failed_extraction_still_records_why(db_session: Session, replay_client) -> None:
    """The failure reason has to survive the exception that ends the run."""
    job = _job_with_description(db_session, "jd_unrecoverable_attempt1")

    with pytest.raises(ExtractionFailedError):
        jd_extraction.extract_requirements(db_session, job.id, replay_client)

    logs = db_session.scalars(
        select(LlmCallLog).where(LlmCallLog.job_id == job.id).order_by(LlmCallLog.attempt)
    ).all()
    assert len(logs) == 2
    assert all(log.status is LlmStatus.SCHEMA_INVALID for log in logs)
    assert all(log.error_detail for log in logs)
    assert "not valid JSON" in logs[0].error_detail


def test_exactly_one_retry_is_attempted(db_session: Session) -> None:
    """Not zero, not unlimited — the architecture allows exactly one."""
    job = _job_with_description(db_session, "jd_backend_engineer")
    client = _StubClient("garbage", "still garbage")

    with pytest.raises(ExtractionFailedError):
        jd_extraction.extract_requirements(db_session, job.id, client)

    assert client.calls == [1, 2]


def test_the_retry_is_told_what_was_wrong(db_session: Session) -> None:
    """A blind repeat would be a waste; the retry carries the validation errors."""
    job = _job_with_description(db_session, "jd_backend_engineer")
    seen: list[str] = []

    class _CapturingClient:
        def complete(self, request):
            seen.append(request.user_content)
            return LlmResponse(
                text=VALID_REPLY if request.attempt == 2 else "not json",
                model="claude-opus-5",
                source=LlmSource.FIXTURE,
                latency_ms=0,
            )

    jd_extraction.extract_requirements(db_session, job.id, _CapturingClient())

    assert len(seen) == 2
    assert "not valid JSON" in seen[1]
    assert "previous reply" in seen[1].lower()
    # The document itself is still present in the retry, not just the complaint.
    assert "Northwind Analytics" in seen[1]


def test_a_provider_failure_is_not_retried(db_session: Session) -> None:
    """No reply means nothing to correct; retrying the same prompt is pointless."""
    job = _job_with_description(db_session, "jd_backend_engineer")
    attempts: list[int] = []

    class _FailingClient:
        def complete(self, request):
            attempts.append(request.attempt)
            raise LlmProviderError("provider returned HTTP 503")

    with pytest.raises(ExtractionFailedError):
        jd_extraction.extract_requirements(db_session, job.id, _FailingClient())

    assert attempts == [1]
    log = db_session.scalar(select(LlmCallLog).where(LlmCallLog.job_id == job.id))
    assert log is not None
    assert log.status is LlmStatus.PROVIDER_ERROR


# --------------------------------------------------------------------------
# Preconditions and re-run behaviour
# --------------------------------------------------------------------------


def test_extraction_requires_a_job_description(db_session: Session, replay_client) -> None:
    job = jobs.create_job(db_session, title="No description yet")

    with pytest.raises(ConflictError) as excinfo:
        jd_extraction.extract_requirements(db_session, job.id, replay_client)

    assert "no job description" in excinfo.value.message.lower()


def test_extraction_requires_an_existing_job(db_session: Session, replay_client) -> None:
    with pytest.raises(NotFoundError):
        jd_extraction.extract_requirements(db_session, uuid.uuid4(), replay_client)


def test_re_extraction_replaces_rather_than_duplicates(db_session: Session, replay_client) -> None:
    job = _job_with_description(db_session, "jd_backend_engineer")

    jd_extraction.extract_requirements(db_session, job.id, replay_client)
    jd_extraction.extract_requirements(db_session, job.id, replay_client)

    stored = db_session.scalars(select(Requirement).where(Requirement.job_id == job.id)).all()
    assert len(stored) == 13, "a second run must replace, not append"


def test_re_extraction_keeps_hand_added_requirements(db_session: Session, replay_client) -> None:
    """Only model-derived rows are replaced; a human's typing is not destroyed."""
    from app.services import requirements as requirements_service

    job = _job_with_description(db_session, "jd_backend_engineer")
    jd_extraction.extract_requirements(db_session, job.id, replay_client)
    manual = requirements_service.add_requirement(
        db_session,
        job.id,
        text="Willing to travel to the Berlin office quarterly",
        category=RequirementCategory.SOFT_SKILL_OTHER,
        must_have=False,
    )

    jd_extraction.extract_requirements(db_session, job.id, replay_client)

    surviving = db_session.get(Requirement, manual.id)
    assert surviving is not None
    assert surviving.origin is RequirementOrigin.HR_ADDED
