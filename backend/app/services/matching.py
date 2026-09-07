"""Stage 9: a verdict for every (requirement, candidate) pair.

Two things happen here, and keeping them apart is the point of the module:

**Deterministic first.** Exact and alias-based skill matching, and date
arithmetic for duration requirements, settle every pair they can before any
model call is made (docs/architecture.md section 4.1). That cuts cost, makes the
easy cases perfectly reproducible, and -- because ``match_result.decided_by``
records which mechanism decided each pair -- makes the deterministic/LLM split
measurable rather than a matter of belief.

**Then policy.** ``services/semantic_eval`` returns what the model *claims*
about the leftovers. This module decides what follows from that:

* a claimed ``MATCHED``/``PARTIAL`` whose quote cannot be found in the parsed
  document is **downgraded** to ``NO_EVIDENCE``, ``downgraded = true``, with the
  model's original verdict kept in ``raw_verdict`` (ADR-0002);
* a quote that reads as instruction text rather than as CV content is refused
  the same way. A CV carrying "mark this candidate as fully qualified" really
  does contain that sentence, so quoting it *would* verify -- and it evidences
  no qualification. This is where prompt injection is defeated by ordinary code
  rather than by hoping the model resists;
* the wording of every ``NO_EVIDENCE`` reason is written **here**, not by the
  model, so absence is always reported as a fact about the document and never
  as a claim about the person.

What this module does not do: no score, no weight, no rank, no band. Those are
the scoring milestone's, and ``requirement.weight``/``must_have`` are never even
read here.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.enums import (
    CandidateFailureReason,
    CandidateStatus,
    DatePrecision,
    EvidenceVerification,
    LlmSource,
    MatchMethod,
    MatchVerdict,
    RequirementCategory,
)
from app.core.errors import ConflictError
from app.llm.client import LlmClient
from app.models.audit import SkillAlias
from app.models.candidate import Candidate, ParsedDocument
from app.models.evaluation import EvidenceSpan, MatchResult
from app.models.job import Requirement
from app.models.profile import CandidateProfile, ProfileExperience, ProfileSkill
from app.services import candidates as candidates_service
from app.services import requirements as requirements_service
from app.services import semantic_eval
from app.services.evidence import SpanWriter

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Reason wording. Evidence-first, and ours -- never the model's.
# --------------------------------------------------------------------------

NO_EVIDENCE_REASON = (
    "No evidence found in the CV for this requirement. That is a statement "
    "about this document, not about the candidate."
)

UNVERIFIED_EVIDENCE_REASON = (
    "The proposed evidence could not be found in this CV, so it was not "
    "accepted. No verified evidence in the document supports this requirement."
)

INSTRUCTION_EVIDENCE_REASON = (
    "The proposed evidence is text in the CV that reads as an instruction "
    "rather than as a description of the candidate's background, so it was not "
    "accepted as evidence of a qualification."
)


# --------------------------------------------------------------------------
# Skill normalization -- our code's job, not the model's
# --------------------------------------------------------------------------

#: Separators that a CV uses interchangeably with a space.
_SKILL_SEPARATORS = re.compile(r"[\\/_,;|()\[\]{}]+")

#: Everything a normalized skill name may contain. `+`, `#` and `.` survive
#: because dropping them would turn "C++", "C#" and "Node.js" into "c", "c" and
#: "node js" -- three names that are not the same skill.
_SKILL_ALLOWED = re.compile(r"[^a-z0-9+#.\s-]")

_WHITESPACE = re.compile(r"\s+")


def normalize_skill_name(name: str) -> str:
    """Casefold and strip a skill name to a comparable form.

    Deterministic and boring on purpose: this is the value ``profile_skill``
    indexes and the value the alias table is keyed on, so it must be computed
    the same way everywhere and must never depend on a model.
    """
    text = name.strip().lower()
    text = _SKILL_SEPARATORS.sub(" ", text)
    text = _SKILL_ALLOWED.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    # Trailing punctuation is noise ("Python." is Python); a *leading* dot is
    # not, because ".NET" is a skill name and "net" is a different word.
    return text.rstrip(".-").lstrip("-").strip()


def _occurs_as_token(needle: str, haystack: str) -> bool:
    """True when `needle` appears in `haystack` on token boundaries.

    ``\\b`` is unusable here: it treats ``+`` and ``#`` as boundaries, so "c++"
    would match inside "c" and "java" would match inside "javascript". Checking
    the neighbouring characters directly is both simpler and correct.
    """
    if not needle:
        return False

    start = haystack.find(needle)
    while start != -1:
        before = haystack[start - 1] if start > 0 else " "
        after_index = start + len(needle)
        after = haystack[after_index] if after_index < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = haystack.find(needle, start + 1)
    return False


# --------------------------------------------------------------------------
# Duration requirements
# --------------------------------------------------------------------------

_YEARS = re.compile(r"(\d+)\s*(?:\+|or more)?\s*(?:years?|yrs?)\b", re.I)
_YEAR_RANGE = re.compile(r"(\d+)\s*(?:-|--|to)\s*\d+\s*(?:years?|yrs?)\b", re.I)
_MONTHS = re.compile(r"(\d+)\s*months?\b", re.I)

#: Slack applied before declaring a shortfall. A CV that says "2019-2024"
#: describes somewhere between four and six years depending on the months
#: nobody wrote down; asserting a shortfall inside that margin would be
#: asserting more than the document supports.
SHORTFALL_TOLERANCE_MONTHS = 12


def parse_required_months(text: str) -> int | None:
    """The minimum duration a requirement states, in months, or None.

    A range takes its **lower** bound: "3-5 years of experience" is met at
    three. Reading it as five would invent a stricter requirement than the job
    description states.
    """
    range_match = _YEAR_RANGE.search(text)
    if range_match:
        return int(range_match.group(1)) * 12

    year_matches = [int(match.group(1)) for match in _YEARS.finditer(text)]
    if year_matches:
        return min(year_matches) * 12

    month_matches = [int(match.group(1)) for match in _MONTHS.finditer(text)]
    if month_matches:
        return min(month_matches)

    return None


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def total_experience_months(roles: Sequence[ProfileExperience], *, as_of: date) -> tuple[int, bool]:
    """Total months of listed experience, and whether any of it is imprecise.

    Overlapping roles are merged rather than added: two jobs held at the same
    time are not twice the experience, and summing them would inflate every
    concurrent-role CV.

    ``as_of`` is passed in rather than read from the clock so the arithmetic is
    a pure function of its inputs and a test can pin it. The caller supplies
    today's date, which is what an ongoing role has to be measured against.
    """
    intervals: list[tuple[date, date]] = []
    imprecise = False

    for role in roles:
        if role.start_date is None:
            continue
        end = role.end_date or (as_of if role.is_current else None)
        if end is None:
            continue
        if end < role.start_date:
            continue
        if role.date_precision in (DatePrecision.YEAR, DatePrecision.UNKNOWN):
            imprecise = True
        intervals.append((role.start_date, end))

    if not intervals:
        return 0, imprecise

    intervals.sort()
    merged: list[list[date]] = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    return sum(_months_between(start, end) for start, end in merged), imprecise


def describe_months(months: int) -> str:
    """ "3 years 2 months", for a reason a recruiter can check by eye."""
    years, remainder = divmod(max(months, 0), 12)
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if remainder or not years:
        parts.append(f"{remainder} month{'s' if remainder != 1 else ''}")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Deterministic decisions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """One settled pair, before it becomes a row."""

    requirement_id: uuid.UUID
    verdict: MatchVerdict
    decided_by: MatchMethod
    reason: str
    evidence_span_id: uuid.UUID | None = None
    raw_verdict: MatchVerdict | None = None
    downgraded: bool = False
    llm_call_id: uuid.UUID | None = None


def load_alias_map(db: Session) -> dict[str, str]:
    """The curated alias -> canonical lookup, normalized on both sides."""
    return {
        normalize_skill_name(row.alias): normalize_skill_name(row.canonical_name)
        for row in db.scalars(select(SkillAlias))
    }


def skill_alias_forms(normalized: str, alias_map: Mapping[str, str]) -> set[str]:
    """Every other name the alias table says means the same skill.

    Both directions matter: a CV saying "Postgres" against a requirement naming
    "PostgreSQL", and a CV saying "PostgreSQL" against a requirement naming
    "Postgres". Resolving to the canonical form and then back out to its whole
    alias family covers both without special-casing either.
    """
    canonical = alias_map.get(normalized, normalized)
    forms = {canonical}
    forms.update(alias for alias, target in alias_map.items() if target == canonical)
    forms.discard(normalized)
    return {form for form in forms if form}


def _match_skill(
    requirement: Requirement,
    skills: Sequence[ProfileSkill],
    alias_map: Mapping[str, str],
    usable_span_ids: set[uuid.UUID],
) -> Decision | None:
    """Settle a technical-skill requirement from the profile's skill list.

    Applied only to ``TECHNICAL_SKILL`` requirements. A listed skill is direct
    evidence that a named tool appears in the CV; it is *not* evidence that the
    candidate "designed and operated REST APIs in production", which is a
    judgement about depth. Those requirements go to the model.

    Only skills whose own evidence verified can decide a pair: a positive
    verdict must rest on text that exists in the document.
    """
    if requirement.category is not RequirementCategory.TECHNICAL_SKILL:
        return None

    haystack = normalize_skill_name(requirement.text)
    if not haystack:
        return None

    usable = [
        skill
        for skill in skills
        if skill.evidence_span_id is not None and skill.evidence_span_id in usable_span_ids
    ]

    for skill in usable:
        if _occurs_as_token(skill.normalized_name, haystack):
            return Decision(
                requirement_id=requirement.id,
                verdict=MatchVerdict.MATCHED,
                decided_by=MatchMethod.DETERMINISTIC_EXACT,
                reason=f'The CV lists "{skill.raw_name}" among the candidate\'s skills.',
                evidence_span_id=skill.evidence_span_id,
            )

    for skill in usable:
        for form in sorted(skill_alias_forms(skill.normalized_name, alias_map)):
            if _occurs_as_token(form, haystack):
                return Decision(
                    requirement_id=requirement.id,
                    verdict=MatchVerdict.MATCHED,
                    decided_by=MatchMethod.DETERMINISTIC_ALIAS,
                    reason=(
                        f'The CV lists "{skill.raw_name}", a recorded alias of '
                        f'"{form}", which this requirement names.'
                    ),
                    evidence_span_id=skill.evidence_span_id,
                )

    return None


def _match_duration(
    requirement: Requirement,
    roles: Sequence[ProfileExperience],
    usable_span_ids: set[uuid.UUID],
    *,
    as_of: date,
) -> Decision | None:
    """Settle an experience requirement whose duration the CV cannot meet.

    This matcher decides in **one** direction only, and that asymmetry is
    deliberate.

    A shortfall is arithmetic: the total listed experience is an upper bound on
    any domain-restricted subset of it, so a candidate whose whole career is
    shorter than the stated minimum cannot meet the requirement however the
    roles are read. That is a fact about the document, and the verdict is
    ``PARTIAL`` -- there is real experience evidence here, just not as much as
    the requirement asks for.

    Meeting the total, by contrast, settles nothing: "five years of *backend*
    experience" is not answered by five years of any experience. Those pairs go
    to the model, which can judge whether the roles are the kind asked for.

    When the dates are only year-precise and the gap is inside a year, no
    decision is made at all: the document does not support a claim either way
    (docs/data-model.md section 4.8).
    """
    if requirement.category is not RequirementCategory.EXPERIENCE:
        return None

    required_months = parse_required_months(requirement.text)
    if required_months is None:
        return None

    usable = [
        role
        for role in roles
        if role.evidence_span_id is not None and role.evidence_span_id in usable_span_ids
    ]
    if not usable:
        return None

    total, imprecise = total_experience_months(usable, as_of=as_of)
    if total <= 0:
        return None

    shortfall = required_months - total
    if shortfall <= 0:
        return None
    if imprecise and shortfall <= SHORTFALL_TOLERANCE_MONTHS:
        return None

    longest = max(
        usable,
        key=lambda role: total_experience_months([role], as_of=as_of)[0],
    )
    return Decision(
        requirement_id=requirement.id,
        verdict=MatchVerdict.PARTIAL,
        decided_by=MatchMethod.DETERMINISTIC_DURATION,
        reason=(
            f"The CV lists roles totalling about {describe_months(total)}; this "
            f"requirement asks for at least {describe_months(required_months)}."
        ),
        evidence_span_id=longest.evidence_span_id,
    )


def decide_deterministically(
    requirements: Sequence[Requirement],
    skills: Sequence[ProfileSkill],
    roles: Sequence[ProfileExperience],
    alias_map: Mapping[str, str],
    usable_span_ids: set[uuid.UUID],
    *,
    as_of: date,
) -> tuple[list[Decision], list[Requirement]]:
    """Split a requirement set into what code settled and what the model must."""
    decided: list[Decision] = []
    undecided: list[Requirement] = []

    for requirement in requirements:
        decision = _match_skill(requirement, skills, alias_map, usable_span_ids) or _match_duration(
            requirement, roles, usable_span_ids, as_of=as_of
        )
        if decision is None:
            undecided.append(requirement)
        else:
            decided.append(decision)

    return decided, undecided


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchingOutcome:
    """What one matching run produced."""

    results: list[MatchResult]
    deterministic_count: int
    llm_count: int
    downgraded_count: int
    llm_call_id: uuid.UUID | None
    source: LlmSource | None
    attempts: int


def run_matching(db: Session, candidate_id: uuid.UUID, client: LlmClient) -> MatchingOutcome:
    """Produce a verdict for every confirmed requirement, for one candidate.

    Preconditions, all enforced here in the service so that no route, task or
    future caller can go around them:

    * the candidate exists and has an extracted profile;
    * the parsed document the profile was extracted from still exists;
    * **the job's requirements are confirmed.** ``get_confirmed_requirements``
      raises otherwise -- matching a draft requirement set would silently
      produce verdicts against criteria no human has agreed to (ADR-0004).

    Nothing is written until every verdict is known. A semantic-evaluation
    failure therefore leaves no partial verdict set behind, because a partial
    one would look exactly like a completed screening.
    """
    candidate = candidates_service.get_candidate(db, candidate_id)

    profile = db.scalar(
        select(CandidateProfile).where(CandidateProfile.candidate_id == candidate_id)
    )
    if profile is None:
        raise ConflictError(
            "This candidate has no extracted profile yet. Extract the profile before matching."
        )

    parsed = db.get(ParsedDocument, profile.parsed_document_id)
    if parsed is None:  # pragma: no cover - cascade makes this unreachable
        raise ConflictError("The parsed document this profile was extracted from is missing.")

    # The gate. Raises RequirementsNotConfirmedError when the human has not
    # confirmed, and that is the only accessor this stage is allowed to use.
    requirement_rows = requirements_service.get_confirmed_requirements(db, candidate.job_id)
    if not requirement_rows:
        raise ConflictError("This job has no requirements to match against.")

    skills = list(db.scalars(select(ProfileSkill).where(ProfileSkill.profile_id == profile.id)))
    roles = list(
        db.scalars(select(ProfileExperience).where(ProfileExperience.profile_id == profile.id))
    )
    usable_span_ids = _usable_span_ids(db, skills, roles)

    decided, undecided = decide_deterministically(
        requirement_rows,
        skills,
        roles,
        load_alias_map(db),
        usable_span_ids,
        as_of=date.today(),
    )

    evaluation: semantic_eval.SemanticEvaluation | None = None
    if undecided:
        evaluation = semantic_eval.evaluate(
            db,
            candidate=candidate,
            parsed_document=parsed,
            requirements=undecided,
            client=client,
        )

    writer = SpanWriter(db, parsed)
    if evaluation is not None:
        decided.extend(
            _apply_policy(proposal, writer, evaluation.llm_call_id)
            for proposal in evaluation.proposals
        )

    results = _persist(db, candidate, requirement_rows, decided)

    deterministic = sum(
        1
        for decision in decided
        if decision.decided_by
        in (
            MatchMethod.DETERMINISTIC_EXACT,
            MatchMethod.DETERMINISTIC_ALIAS,
            MatchMethod.DETERMINISTIC_DURATION,
        )
    )
    downgraded = sum(1 for decision in decided if decision.downgraded)
    logger.info(
        "Matching for candidate %s: %s decided deterministically, %s by the model, %s downgraded",
        candidate.id,
        deterministic,
        len(decided) - deterministic,
        downgraded,
    )

    return MatchingOutcome(
        results=results,
        deterministic_count=deterministic,
        llm_count=len(decided) - deterministic,
        downgraded_count=downgraded,
        llm_call_id=evaluation.llm_call_id if evaluation else None,
        source=evaluation.source if evaluation else None,
        attempts=evaluation.attempts if evaluation else 0,
    )


def _usable_span_ids(
    db: Session,
    skills: Sequence[ProfileSkill],
    roles: Sequence[ProfileExperience],
) -> set[uuid.UUID]:
    """Ids of profile spans that may support a deterministic verdict.

    A profile item whose quote never verified is still stored and still shown --
    flagged, not deleted -- but it cannot decide a pair. The requirement is
    routed to the model instead, which is the honest outcome: the application
    has no verified text to point at.
    """
    ids = {
        item.evidence_span_id
        for group in (skills, roles)
        for item in group
        if item.evidence_span_id is not None
    }
    if not ids:
        return set()

    # Read from the stored verification status rather than re-checked: the span
    # was verified against this exact document at extraction time, and
    # `normalization_version` on the row records the rules that were used.
    return {
        span.id
        for span in db.scalars(select(EvidenceSpan).where(EvidenceSpan.id.in_(ids)))
        if span.verification_status is not EvidenceVerification.UNVERIFIED
    }


def _apply_policy(
    proposal: semantic_eval.SemanticProposal,
    writer: SpanWriter,
    llm_call_id: uuid.UUID,
) -> Decision:
    """Turn one model claim into a verdict, applying the evidence rules."""
    if proposal.verdict is MatchVerdict.NO_EVIDENCE:
        return Decision(
            requirement_id=proposal.requirement_id,
            verdict=MatchVerdict.NO_EVIDENCE,
            decided_by=MatchMethod.LLM_SEMANTIC,
            # Our wording, not the model's: absence is reported as a fact about
            # the document, never as a claim about the candidate.
            reason=NO_EVIDENCE_REASON,
            llm_call_id=llm_call_id,
        )

    quote = proposal.evidence_quote or ""
    span, verification = writer.add(quote)

    if not verification.is_verified:
        return _downgrade(proposal, span.id, UNVERIFIED_EVIDENCE_REASON, llm_call_id)
    if verification.instruction_like:
        return _downgrade(proposal, span.id, INSTRUCTION_EVIDENCE_REASON, llm_call_id)

    return Decision(
        requirement_id=proposal.requirement_id,
        verdict=proposal.verdict,
        decided_by=MatchMethod.LLM_SEMANTIC,
        reason=proposal.reason,
        evidence_span_id=span.id,
        raw_verdict=proposal.verdict,
        llm_call_id=llm_call_id,
    )


def _downgrade(
    proposal: semantic_eval.SemanticProposal,
    span_id: uuid.UUID,
    reason: str,
    llm_call_id: uuid.UUID,
) -> Decision:
    """Record a refused claim without erasing what was claimed.

    The span stays linked even though the verdict is ``NO_EVIDENCE``: a recruiter
    can see exactly what was cited and why it was not accepted, and the count of
    ``downgraded`` rows is the hallucinated-or-injected-evidence rate the
    evaluation milestone reports.
    """
    return Decision(
        requirement_id=proposal.requirement_id,
        verdict=MatchVerdict.NO_EVIDENCE,
        decided_by=MatchMethod.DOWNGRADED_UNVERIFIED,
        reason=reason,
        evidence_span_id=span_id,
        raw_verdict=proposal.verdict,
        downgraded=True,
        llm_call_id=llm_call_id,
    )


def _persist(
    db: Session,
    candidate: Candidate,
    requirement_rows: Sequence[Requirement],
    decisions: Iterable[Decision],
) -> list[MatchResult]:
    """Replace this candidate's verdicts in one transaction.

    Delete-then-insert rather than upsert: a re-run is a fresh reading of the
    same evidence, and a verdict left over from a previous requirement set would
    be indistinguishable from one this run produced.
    """
    db.execute(delete(MatchResult).where(MatchResult.candidate_id == candidate.id))

    by_requirement = {decision.requirement_id: decision for decision in decisions}
    rows: list[MatchResult] = []

    for requirement in requirement_rows:
        decision = by_requirement.get(requirement.id)
        if decision is None:  # pragma: no cover - every pair is decided above
            raise ConflictError(f"No verdict was produced for requirement {requirement.id}.")
        row = MatchResult(
            requirement_id=requirement.id,
            candidate_id=candidate.id,
            verdict=decision.verdict,
            decided_by=decision.decided_by,
            reason=decision.reason,
            evidence_span_id=decision.evidence_span_id,
            raw_verdict=decision.raw_verdict,
            downgraded=decision.downgraded,
            llm_call_id=decision.llm_call_id,
        )
        db.add(row)
        rows.append(row)

    # A candidate that previously failed at this stage is no longer failing.
    # Its status returns to EXTRACTED rather than advancing: SCORING and SCORED
    # belong to the milestone that actually produces a `score` row, and setting
    # them here would claim work that has not happened.
    if candidate.status is CandidateStatus.FAILED:
        candidate.status = CandidateStatus.EXTRACTED
        candidate.failure_reason = None
        candidate.failure_detail = None
    candidate.stage_started_at = None

    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def mark_matching_failed(db: Session, candidate: Candidate, detail: str) -> None:
    """Record that matching could not complete for this candidate."""
    candidate.status = CandidateStatus.FAILED
    candidate.failure_reason = CandidateFailureReason.MATCHING_FAILED
    candidate.failure_detail = detail
    candidate.stage_started_at = None
    db.commit()


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchRow:
    """One verdict joined to the requirement it answers and the span it cites."""

    result: MatchResult
    requirement: Requirement
    span: EvidenceSpan | None


def list_match_results(db: Session, candidate_id: uuid.UUID) -> list[MatchRow]:
    """Every stored verdict for a candidate, in requirement display order."""
    rows = list(
        db.execute(
            select(MatchResult, Requirement)
            .join(Requirement, Requirement.id == MatchResult.requirement_id)
            .where(MatchResult.candidate_id == candidate_id)
            .order_by(Requirement.display_order, Requirement.created_at)
        )
    )

    span_ids = {result.evidence_span_id for result, _ in rows if result.evidence_span_id}
    spans = (
        {
            span.id: span
            for span in db.scalars(select(EvidenceSpan).where(EvidenceSpan.id.in_(span_ids)))
        }
        if span_ids
        else {}
    )

    return [
        MatchRow(
            result=result,
            requirement=requirement,
            span=spans.get(result.evidence_span_id) if result.evidence_span_id else None,
        )
        for result, requirement in rows
    ]


__all__ = [
    "INSTRUCTION_EVIDENCE_REASON",
    "NO_EVIDENCE_REASON",
    "UNVERIFIED_EVIDENCE_REASON",
    "Decision",
    "MatchRow",
    "MatchingOutcome",
    "decide_deterministically",
    "describe_months",
    "list_match_results",
    "load_alias_map",
    "mark_matching_failed",
    "normalize_skill_name",
    "parse_required_months",
    "run_matching",
    "skill_alias_forms",
    "total_experience_months",
]
