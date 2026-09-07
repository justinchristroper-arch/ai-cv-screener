"""Requirement matching end to end: the gate, the routing, and the verdicts.

Four claims are under test here, and they are the ones the whole milestone
rests on:

1. **The confirmation gate is server-side.** Matching against a requirement set
   no human has confirmed is refused in the service, not merely hidden in a UI
   (ADR-0004).
2. **Deterministic rules run first** and record that they did, so the split
   between code and model is measurable rather than asserted.
3. **A positive verdict always carries verified evidence.** A quote that is not
   in the document, or that reads as an instruction rather than as CV content,
   cannot produce one (ADR-0002).
4. **Absence is reported as absence of evidence** -- never as a claim that the
   candidate lacks something.

Everything runs from recorded fixtures and stubs. No API key, no network.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    LlmSource,
    LlmStatus,
    MatchMethod,
    MatchVerdict,
)
from app.core.errors import (
    ConflictError,
    ExtractionFailedError,
    RequirementsNotConfirmedError,
)
from app.models.audit import LlmCallLog
from app.models.evaluation import EvidenceSpan, MatchResult
from app.models.job import Requirement
from app.services import matching, profile_extraction, requirements
from tests.factories import make_candidate_from_fixture, make_job_with_requirements

pytestmark = pytest.mark.requires_db


class _StubClient:
    """Returns canned replies in order, recording the requests it saw."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.requests: list = []

    def complete(self, request):
        from app.llm.client import LlmResponse

        self.requests.append(request)
        return LlmResponse(
            text=self._replies.pop(0),
            model="claude-opus-5",
            source=LlmSource.FIXTURE,
            latency_ms=0,
        )


def _prepare(db: Session, replay_client, *, fixture: str = "cv_alex_rivera", confirm: bool = True):
    """A confirmed job and a candidate whose profile has been extracted."""
    job = make_job_with_requirements(db, replay_client, confirm=confirm)
    candidate, parsed = make_candidate_from_fixture(db, job.id, fixture)
    profile_extraction.extract_profile(db, candidate.id, replay_client)
    return job, candidate, parsed


def _by_text(rows: list[matching.MatchRow]) -> dict[str, matching.MatchRow]:
    return {row.requirement.text: row for row in rows}


# --------------------------------------------------------------------------
# A. The confirmation gate
# --------------------------------------------------------------------------


def test_matching_against_an_unconfirmed_requirement_set_is_refused(
    db_session: Session, replay_client
) -> None:
    """The gate, enforced where every caller must pass through it."""
    _, candidate, _ = _prepare(db_session, replay_client, confirm=False)

    with pytest.raises(RequirementsNotConfirmedError):
        matching.run_matching(db_session, candidate.id, _StubClient())

    assert (
        db_session.scalars(
            select(MatchResult).where(MatchResult.candidate_id == candidate.id)
        ).all()
        == []
    )


def test_unconfirming_a_job_closes_the_gate_again(db_session: Session, replay_client) -> None:
    job, candidate, _ = _prepare(db_session, replay_client)
    matching.run_matching(db_session, candidate.id, replay_client)

    requirements.unconfirm_requirements(db_session, job.id)

    with pytest.raises(RequirementsNotConfirmedError):
        matching.run_matching(db_session, candidate.id, _StubClient())


def test_matching_needs_an_extracted_profile(db_session: Session, replay_client) -> None:
    job = make_job_with_requirements(db_session, replay_client)
    candidate, _ = make_candidate_from_fixture(db_session, job.id, "cv_alex_rivera")

    with pytest.raises(ConflictError):
        matching.run_matching(db_session, candidate.id, _StubClient())


# --------------------------------------------------------------------------
# B. A full run
# --------------------------------------------------------------------------


@pytest.fixture()
def matched(db_session: Session, replay_client):
    job, candidate, parsed = _prepare(db_session, replay_client)
    outcome = matching.run_matching(db_session, candidate.id, replay_client)
    return job, candidate, parsed, outcome


def test_every_confirmed_requirement_gets_exactly_one_verdict(db_session: Session, matched) -> None:
    job, candidate, _, outcome = matched
    stored = requirements.list_requirements(db_session, job.id)

    assert len(outcome.results) == len(stored) == 13
    rows = matching.list_match_results(db_session, candidate.id)
    assert len({row.requirement.id for row in rows}) == 13


def test_the_verdicts_span_all_three_values(db_session: Session, matched) -> None:
    """A screening that can only say one thing is not a screening."""
    _, candidate, _, _ = matched
    rows = matching.list_match_results(db_session, candidate.id)
    verdicts = [row.result.verdict for row in rows]

    assert verdicts.count(MatchVerdict.MATCHED) == 8
    assert verdicts.count(MatchVerdict.PARTIAL) == 3
    assert verdicts.count(MatchVerdict.NO_EVIDENCE) == 2


def test_the_deterministic_and_model_split_is_recorded(db_session: Session, matched) -> None:
    """`decided_by` is what makes the split measurable instead of a belief."""
    _, candidate, _, outcome = matched
    rows = matching.list_match_results(db_session, candidate.id)
    methods = [row.result.decided_by for row in rows]

    assert methods.count(MatchMethod.DETERMINISTIC_EXACT) == 3
    assert methods.count(MatchMethod.DETERMINISTIC_ALIAS) == 1
    assert methods.count(MatchMethod.DETERMINISTIC_DURATION) == 1
    assert methods.count(MatchMethod.LLM_SEMANTIC) == 8
    assert outcome.deterministic_count == 5
    assert outcome.llm_count == 8


def test_only_the_undecided_pairs_reach_the_model(db_session: Session, replay_client) -> None:
    """Deterministic-first is a cost and reproducibility decision, so measure it."""
    _, candidate, _ = _prepare(db_session, replay_client)
    outcome = matching.run_matching(db_session, candidate.id, replay_client)

    assert outcome.attempts == 1
    assert outcome.source is LlmSource.FIXTURE
    assert outcome.llm_count == 8, "five of thirteen pairs never cost a model call"


def test_an_exact_skill_match_cites_the_line_it_was_read_from(db_session: Session, matched) -> None:
    _, candidate, parsed, _ = matched
    row = _by_text(matching.list_match_results(db_session, candidate.id))[
        "Strong experience with Python"
    ]

    assert row.result.verdict is MatchVerdict.MATCHED
    assert row.result.decided_by is MatchMethod.DETERMINISTIC_EXACT
    assert row.span is not None
    assert parsed.full_text[row.span.start_char : row.span.end_char] == row.span.quoted_text
    assert "Python" in row.result.reason


def test_an_alias_match_is_recorded_as_an_alias_match(db_session: Session, matched) -> None:
    """The CV says "Postgres"; the job description says "PostgreSQL"."""
    _, candidate, _, _ = matched
    row = _by_text(matching.list_match_results(db_session, candidate.id))[
        "Strong experience with PostgreSQL"
    ]

    assert row.result.verdict is MatchVerdict.MATCHED
    assert row.result.decided_by is MatchMethod.DETERMINISTIC_ALIAS
    assert "Postgres" in row.result.reason


def test_a_duration_shortfall_is_partial_and_shows_its_arithmetic(
    db_session: Session, matched
) -> None:
    """Fifty-five months of listed roles against a five-year minimum."""
    _, candidate, _, _ = matched
    row = _by_text(matching.list_match_results(db_session, candidate.id))[
        "At least 5 years of professional experience building backend services"
    ]

    assert row.result.verdict is MatchVerdict.PARTIAL
    assert row.result.decided_by is MatchMethod.DETERMINISTIC_DURATION
    assert "4 years 7 months" in row.result.reason
    assert "at least 5 years" in row.result.reason
    assert row.span is not None


def test_a_semantic_match_keeps_the_models_own_wording(db_session: Session, matched) -> None:
    _, candidate, _, _ = matched
    row = _by_text(matching.list_match_results(db_session, candidate.id))[
        "Practical experience designing and operating REST APIs in production"
    ]

    assert row.result.verdict is MatchVerdict.MATCHED
    assert row.result.decided_by is MatchMethod.LLM_SEMANTIC
    assert row.result.reason.startswith("The CV describes")
    assert row.result.raw_verdict is MatchVerdict.MATCHED
    assert row.result.downgraded is False


# --------------------------------------------------------------------------
# C. Evidence discipline
# --------------------------------------------------------------------------


def test_every_positive_verdict_carries_a_verified_quote(db_session: Session, matched) -> None:
    """The database enforces the span; this asserts it was actually located."""
    from app.core.enums import EvidenceVerification

    _, candidate, parsed, _ = matched

    for row in matching.list_match_results(db_session, candidate.id):
        if row.result.verdict is MatchVerdict.NO_EVIDENCE:
            continue
        assert row.span is not None, row.requirement.text
        assert row.span.verification_status is not EvidenceVerification.UNVERIFIED
        assert parsed.full_text[row.span.start_char : row.span.end_char] == row.span.quoted_text


def test_absence_is_reported_as_absence_of_evidence(db_session: Session, matched) -> None:
    """Never "the candidate does not have X" -- the wording is ours, not the model's."""
    _, candidate, _, _ = matched
    row = _by_text(matching.list_match_results(db_session, candidate.id))[
        "Experience with Kubernetes"
    ]

    assert row.result.verdict is MatchVerdict.NO_EVIDENCE
    assert row.result.reason == matching.NO_EVIDENCE_REASON
    assert "No evidence found in the CV" in row.result.reason
    assert "not about the candidate" in row.result.reason

    lowered = row.result.reason.lower()
    for forbidden in ("does not have", "lacks", "is not qualified", "cannot"):
        assert forbidden not in lowered


def test_a_fabricated_quote_cannot_produce_a_positive_verdict(
    db_session: Session, replay_client
) -> None:
    """The downgrade rule, on a reply whose evidence is invented."""
    _, candidate, _ = _prepare(db_session, replay_client)
    reply = _all_matched_reply(8, "Ran production Kubernetes clusters for six years.")

    outcome = matching.run_matching(db_session, candidate.id, _StubClient(reply))

    rows = matching.list_match_results(db_session, candidate.id)
    downgraded = [row for row in rows if row.result.downgraded]
    assert len(downgraded) == 8
    assert outcome.downgraded_count == 8

    for row in downgraded:
        assert row.result.verdict is MatchVerdict.NO_EVIDENCE
        assert row.result.raw_verdict is MatchVerdict.MATCHED
        assert row.result.decided_by is MatchMethod.DOWNGRADED_UNVERIFIED
        assert row.result.reason == matching.UNVERIFIED_EVIDENCE_REASON


def test_a_downgraded_verdict_keeps_the_refused_quote_for_audit(
    db_session: Session, replay_client
) -> None:
    """`raw_verdict` plus the stored span is what makes fabrication countable."""
    from app.core.enums import EvidenceVerification

    _, candidate, _ = _prepare(db_session, replay_client)
    invented = "Ran production Kubernetes clusters for six years."

    matching.run_matching(db_session, candidate.id, _StubClient(_all_matched_reply(8, invented)))

    row = _by_text(matching.list_match_results(db_session, candidate.id))[
        "Experience with Kubernetes"
    ]
    assert row.span is not None
    assert row.span.quoted_text == invented
    assert row.span.verification_status is EvidenceVerification.UNVERIFIED
    assert row.span.start_char is None


def _all_matched_reply(count: int, quote: str) -> str:
    import json

    return json.dumps(
        {
            "verdicts": [
                {
                    "index": index,
                    "verdict": "MATCHED",
                    "evidence_quote": quote,
                    "reason": "The CV supports this requirement.",
                }
                for index in range(count)
            ]
        }
    )


# --------------------------------------------------------------------------
# D. Validation, retry and failure
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("not json at all", "not JSON"),
        ('{"verdicts": []}', "empty"),
        (
            '{"verdicts": [{"index": 0, "verdict": "GREAT", "evidence_quote": null, '
            '"reason": "ok"}]}',
            "verdict outside the enum",
        ),
        (
            '{"verdicts": [{"index": 0, "verdict": "MATCHED", "evidence_quote": null, '
            '"reason": "ok"}]}',
            "positive verdict with no quote",
        ),
        (
            '{"verdicts": [{"index": 0, "verdict": "NO_EVIDENCE", '
            '"evidence_quote": "Python, FastAPI, Postgres, Docker, Bash, pytest", '
            '"reason": "ok"}]}',
            "NO_EVIDENCE carrying a quote",
        ),
        (
            '{"verdicts": [{"index": 0, "verdict": "NO_EVIDENCE", "evidence_quote": null, '
            '"reason": "ok", "score": 90}]}',
            "a score the model invented",
        ),
        (
            '{"verdicts": [{"index": 99, "verdict": "NO_EVIDENCE", "evidence_quote": null, '
            '"reason": "ok"}]}',
            "an index that was never asked about",
        ),
    ],
)
def test_unusable_matching_output_fails_the_stage_and_persists_nothing(
    db_session: Session, replay_client, reply: str, reason: str
) -> None:
    _, candidate, _ = _prepare(db_session, replay_client)
    client = _StubClient(reply, reply)

    with pytest.raises(ExtractionFailedError):
        matching.run_matching(db_session, candidate.id, client)

    assert [request.attempt for request in client.requests] == [1, 2], "exactly one retry"
    stored = db_session.scalars(
        select(MatchResult).where(MatchResult.candidate_id == candidate.id)
    ).all()
    assert stored == [], reason


def test_a_reply_that_answers_only_some_requirements_is_rejected(
    db_session: Session, replay_client
) -> None:
    """A partial answer would leave requirements silently unjudged."""
    partial = _all_matched_reply(3, "Python, FastAPI, Postgres, Docker, Bash, pytest")
    _, candidate, _ = _prepare(db_session, replay_client)

    with pytest.raises(ExtractionFailedError):
        matching.run_matching(db_session, candidate.id, _StubClient(partial, partial))


def test_a_correctable_reply_is_retried_once_and_succeeds(
    db_session: Session, replay_client
) -> None:
    _, candidate, _ = _prepare(db_session, replay_client)
    broken = (
        '{"verdicts": [{"index": 0, "verdict": "MAYBE", "evidence_quote": null, "reason": "x"}]}'
    )
    good = _all_matched_reply(8, "Python, FastAPI, Postgres, Docker, Bash, pytest")

    outcome = matching.run_matching(db_session, candidate.id, _StubClient(broken, good))

    assert outcome.attempts == 2
    assert len(outcome.results) == 13

    logs = list(
        db_session.scalars(
            select(LlmCallLog)
            .where(
                LlmCallLog.candidate_id == candidate.id,
                LlmCallLog.prompt_version == "semantic-match-v1",
            )
            .order_by(LlmCallLog.attempt)
        )
    )
    assert [log.status for log in logs] == [LlmStatus.SCHEMA_INVALID, LlmStatus.SUCCESS]


def test_a_failed_semantic_call_leaves_no_partial_verdict_set(
    db_session: Session, replay_client
) -> None:
    """A half-finished screening looks exactly like a finished one. So: none."""
    _, candidate, _ = _prepare(db_session, replay_client)

    with pytest.raises(ExtractionFailedError):
        matching.run_matching(db_session, candidate.id, _StubClient("{}", "{}"))

    assert (
        db_session.scalars(
            select(MatchResult).where(MatchResult.candidate_id == candidate.id)
        ).all()
        == []
    )


# --------------------------------------------------------------------------
# E. Re-running
# --------------------------------------------------------------------------


def test_re_running_replaces_the_previous_verdicts(db_session: Session, matched) -> None:
    """One verdict per pair, always -- the unique key says so."""
    _, candidate, _, _ = matched

    from app.llm.client import ReplayLlmClient

    second = matching.run_matching(db_session, candidate.id, ReplayLlmClient(model="claude-opus-5"))

    assert len(second.results) == 13
    stored = db_session.scalars(
        select(MatchResult).where(MatchResult.candidate_id == candidate.id)
    ).all()
    assert len(stored) == 13


def test_matching_is_reproducible_on_identical_input(db_session: Session, matched) -> None:
    """Same document, same requirements, same recorded reply, same verdicts."""
    from app.llm.client import ReplayLlmClient

    _, candidate, _, _ = matched
    first = {
        row.requirement.text: (row.result.verdict, row.result.decided_by)
        for row in matching.list_match_results(db_session, candidate.id)
    }

    matching.run_matching(db_session, candidate.id, ReplayLlmClient(model="claude-opus-5"))
    second = {
        row.requirement.text: (row.result.verdict, row.result.decided_by)
        for row in matching.list_match_results(db_session, candidate.id)
    }

    assert first == second


# --------------------------------------------------------------------------
# F. What matching must not do
# --------------------------------------------------------------------------


def test_the_model_is_never_told_which_requirements_matter(
    db_session: Session, replay_client
) -> None:
    """Weight and must-have are the recruiter's judgement, not an input to evidence."""
    job, candidate, _ = _prepare(db_session, replay_client)
    client = _StubClient(_all_matched_reply(8, "Python, FastAPI, Postgres, Docker, Bash, pytest"))

    matching.run_matching(db_session, candidate.id, client)

    sent = client.requests[0].user_content + client.requests[0].system_prompt
    assert "must_have" not in sent
    assert "must-have" not in sent
    assert "weight" not in sent.lower()


def test_matching_produces_no_score_and_no_ranking(db_session: Session, matched) -> None:
    """This milestone stops at evidence-backed verdicts, on purpose."""
    from app.models.evaluation import Score

    _, candidate, _, outcome = matched

    assert db_session.scalars(select(Score).where(Score.candidate_id == candidate.id)).all() == []
    assert not hasattr(outcome, "score")
    assert not hasattr(outcome, "band")
    for row in matching.list_match_results(db_session, candidate.id):
        assert not hasattr(row.result, "score")


def test_an_unknown_candidate_is_a_not_found(db_session: Session) -> None:
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        matching.run_matching(db_session, uuid.uuid4(), _StubClient())


def test_a_confirmed_job_with_no_requirements_left_cannot_be_matched(
    db_session: Session, replay_client
) -> None:
    """The gate passes but there is nothing to match against. Refuse, do not
    return an empty verdict set that would read as a completed screening."""
    job, candidate, _ = _prepare(db_session, replay_client)
    db_session.execute(Requirement.__table__.delete().where(Requirement.job_id == job.id))
    db_session.commit()

    with pytest.raises(ConflictError):
        matching.run_matching(db_session, candidate.id, _StubClient())


def test_evidence_spans_are_never_shared_across_documents(db_session: Session, matched) -> None:
    """A quote is evidence in the document it was found in, and nowhere else."""
    _, candidate, parsed, _ = matched

    for row in matching.list_match_results(db_session, candidate.id):
        if row.span is not None:
            assert row.span.parsed_document_id == parsed.id

    spans = db_session.scalars(
        select(EvidenceSpan).where(EvidenceSpan.parsed_document_id == parsed.id)
    ).all()
    assert spans
