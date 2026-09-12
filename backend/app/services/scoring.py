"""Stage 11: verdicts and weights -> a score a recruiter can check by hand.

The formula is fixed by docs/product-spec.md section 11 and ADR-0008:

    match_value(MATCHED)     = 1.0
    match_value(PARTIAL)     = 0.5
    match_value(NO_EVIDENCE) = 0.0

    score_raw = SUM(weight x match_value) / SUM(weight)
    score     = round(score_raw x 100)            # 0-100

**No model call sits anywhere in this path.** ``compute_score`` is a pure
function of stored rows: no database, no network, no clock, no randomness. That
is the whole point of ADR-0001 -- if a number can change without a document
changing, that is a bug -- and it is what makes re-weighting a job free and
instant, because a weight change never touches a verdict.

Three details the formula alone does not settle, decided here:

* **Rounding is half-up.** Python's built-in ``round`` is banker's rounding, so
  it would turn 64.5 into 64 and 65.5 into 66 -- defensible, and impossible for
  a recruiter checking the arithmetic on paper to predict. ``Decimal`` with
  ``ROUND_HALF_UP`` matches what someone doing the sum by hand expects.
* **An undefined score is not zero.** A job with no requirements, or with every
  weight at zero, yields ``status = UNDEFINED_NO_WEIGHT`` and NULL numbers. A 0
  would read as "this candidate is terrible" when the truth is "nothing was
  asked of them" -- the same evidence-first distinction as `NO_EVIDENCE`,
  applied to arithmetic. It also removes the division-by-zero case entirely.
* **The must-have guard caps a label, never a candidate.** If any `must_have`
  requirement has no evidence, the *displayed* band is capped at `REVIEW`,
  because a weighted average can return 91 while a hard requirement is missing
  entirely. The score is unchanged and still shown, `band_raw` keeps the
  uncapped band, `capped_by_requirement_id` names what triggered it, and the
  candidate is never hidden, filtered or rejected. Nothing in this module
  removes anyone from anything.

Bands are heuristic reading aids with no empirical backing (product-spec
section 12). They are not hiring decisions, not rejection decisions, and not
probabilities of anything.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    CandidateStatus,
    MatchVerdict,
    RecommendationBand,
    RequirementCategory,
    ScoreStatus,
)
from app.core.errors import ConflictError
from app.models.evaluation import MatchResult, Score
from app.models.job import Requirement
from app.services import candidates as candidates_service
from app.services import requirements as requirements_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoringConfig:
    """The value and threshold bundle a stored score was computed under.

    Persisted by name on every ``score`` row, so a score stays interpretable
    against the rules that produced it even after these constants change.
    Bump ``version`` whenever any value below changes.
    """

    version: str

    #: What each verdict is worth. A product decision expressed in code, never
    #: something the model supplies.
    matched: Decimal
    partial: Decimal
    no_evidence: Decimal

    #: Band thresholds, inclusive lower bounds on the 0-100 score.
    strong_match_min: int
    good_match_min: int
    review_min: int

    #: When true, an unevidenced must-have caps the displayed band at REVIEW.
    must_have_guard: bool

    def value_of(self, verdict: MatchVerdict) -> Decimal:
        if verdict is MatchVerdict.MATCHED:
            return self.matched
        if verdict is MatchVerdict.PARTIAL:
            return self.partial
        if verdict is MatchVerdict.NO_EVIDENCE:
            return self.no_evidence
        # NEEDS_REVIEW has no value, by construction. It is excluded from both
        # sides of the average (ADR-0012), so anything that reaches here is
        # asking the wrong question and would silently score it as a zero.
        raise ValueError(f"{verdict.value} is not a scoreable verdict")


#: The verdicts that participate in the average. NEEDS_REVIEW is absent on
#: purpose: it says the engine could not read the document, not that the
#: document is empty, and averaging the two together would erase the difference.
SCOREABLE_VERDICTS = (
    MatchVerdict.MATCHED,
    MatchVerdict.PARTIAL,
    MatchVerdict.NO_EVIDENCE,
)


#: `PARTIAL = 0.5` and the 90/75/60 thresholds are conventions, not
#: measurements (product-spec section 11). They are named and versioned here so
#: that is visible rather than buried in an expression.
DEFAULT_CONFIG = ScoringConfig(
    version="scoring-v1",
    matched=Decimal("1.0"),
    partial=Decimal("0.5"),
    no_evidence=Decimal("0"),
    strong_match_min=90,
    good_match_min=75,
    review_min=60,
    must_have_guard=True,
)

#: Precision of the stored ratios. Matches `NUMERIC(9,8)` on `score.score_raw`
#: and `score.must_have_coverage`.
RATIO_PLACES = Decimal("0.00000001")


@dataclass(frozen=True)
class Contribution:
    """What one requirement contributed, with the arithmetic laid out.

    Everything needed to check the line by hand: the weight the recruiter set,
    the verdict the evidence supported, what that verdict is worth, and the
    product of the two.
    """

    requirement_id: uuid.UUID
    requirement_text: str
    category: RequirementCategory
    must_have: bool
    display_order: int
    weight: Decimal
    verdict: MatchVerdict

    #: Both are None for NEEDS_REVIEW. A 0 here would be indistinguishable from
    #: a genuine NO_EVIDENCE in every table and export that reads a breakdown.
    verdict_value: Decimal | None
    points: Decimal | None

    @property
    def is_scoreable(self) -> bool:
        return self.verdict in SCOREABLE_VERDICTS


@dataclass(frozen=True)
class ScoreBreakdown:
    """A computed score and every input that produced it."""

    status: ScoreStatus
    contributions: list[Contribution]
    weighted_sum: Decimal | None
    total_weight: Decimal | None
    score_raw: Decimal | None
    score: int | None
    must_have_coverage: Decimal | None
    band_raw: RecommendationBand | None
    band: RecommendationBand | None
    capped: bool
    capped_by_requirement_id: uuid.UUID | None
    config_version: str

    @property
    def is_defined(self) -> bool:
        return self.status is ScoreStatus.COMPUTED

    @property
    def needs_review(self) -> list[Contribution]:
        """Requirements the engine could not resolve. Derived, never stored.

        Recomputed from the same verdicts a stored score was built from, so a
        review flag survives a reload without a column of its own.
        """
        return [item for item in self.contributions if not item.is_scoreable]

    @property
    def review_flag(self) -> bool:
        """Whether the recruiter must be shown that something was unresolved."""
        return bool(self.needs_review)

    @property
    def must_have_needs_review(self) -> list[Contribution]:
        return [item for item in self.needs_review if item.must_have]


def _round_half_up(value: Decimal) -> int:
    """Round to a whole number the way a person doing the sum would."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def band_for(score: int, config: ScoringConfig = DEFAULT_CONFIG) -> RecommendationBand:
    """Map a 0-100 score to its heuristic band.

    A reading aid, and nothing more. The thresholds were not derived from
    hiring-outcome data -- no such data exists in this project.
    """
    if score >= config.strong_match_min:
        return RecommendationBand.STRONG_MATCH
    if score >= config.good_match_min:
        return RecommendationBand.GOOD_MATCH
    if score >= config.review_min:
        return RecommendationBand.REVIEW
    return RecommendationBand.LOW_MATCH


def compute_score(
    pairs: Sequence[tuple[Requirement, MatchVerdict]],
    *,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> ScoreBreakdown:
    """Score one candidate from their (requirement, verdict) pairs.

    Pure: the same pairs and the same config always produce the same result, and
    nothing here reads a clock, a database or a network. `pairs` carries plain
    requirement rows, so a test can build them without a database at all.
    """
    contributions = [_contribution(requirement, verdict, config) for requirement, verdict in pairs]
    contributions.sort(key=lambda item: (item.display_order, str(item.requirement_id)))

    # NEEDS_REVIEW is excluded from BOTH sides of the average (ADR-0012): its
    # weight never reaches the denominator, and the weight is NOT redistributed
    # across the others -- doing that would quietly change what the remaining
    # requirements are worth relative to what the recruiter set.
    scoreable = [item for item in contributions if item.is_scoreable]

    if contributions and not scoreable:
        # Everything came back unresolved. There is no average to take, and a 0
        # would report a failure the evaluation never actually reached.
        return ScoreBreakdown(
            status=ScoreStatus.UNDEFINED_NO_DECIDABLE,
            contributions=contributions,
            weighted_sum=None,
            total_weight=None,
            score_raw=None,
            score=None,
            must_have_coverage=None,
            band_raw=None,
            band=None,
            capped=False,
            capped_by_requirement_id=None,
            config_version=config.version,
        )

    total_weight = sum((item.weight for item in scoreable), Decimal("0"))

    if total_weight <= 0:
        # No requirements at all, or every weight is zero. Not a score of 0:
        # nothing was asked of this candidate, and saying "0" would claim they
        # failed an evaluation that never happened.
        return ScoreBreakdown(
            status=ScoreStatus.UNDEFINED_NO_WEIGHT,
            contributions=contributions,
            weighted_sum=None,
            total_weight=None,
            score_raw=None,
            score=None,
            must_have_coverage=None,
            band_raw=None,
            band=None,
            capped=False,
            capped_by_requirement_id=None,
            config_version=config.version,
        )

    weighted_sum = sum((item.points for item in scoreable), Decimal("0"))
    score_raw = (weighted_sum / total_weight).quantize(RATIO_PLACES, rounding=ROUND_HALF_UP)
    # Scaled from the *stored* `score_raw`, not from the unrounded division, so
    # the 0-100 figure follows from the number the recruiter can see rather than
    # from an intermediate value nobody kept.
    score = _round_half_up(score_raw * 100)

    band_raw = band_for(score, config)
    band, capped, capped_by = _apply_must_have_guard(contributions, band_raw, config)

    return ScoreBreakdown(
        status=ScoreStatus.COMPUTED,
        contributions=contributions,
        weighted_sum=weighted_sum,
        total_weight=total_weight,
        score_raw=score_raw,
        score=score,
        must_have_coverage=_must_have_coverage(contributions),
        band_raw=band_raw,
        band=band,
        capped=capped,
        capped_by_requirement_id=capped_by,
        config_version=config.version,
    )


def _contribution(
    requirement: Requirement, verdict: MatchVerdict, config: ScoringConfig
) -> Contribution:
    """One requirement's line of the arithmetic, or an unresolved placeholder."""
    weight = Decimal(requirement.weight)
    scoreable = verdict in SCOREABLE_VERDICTS
    value = config.value_of(verdict) if scoreable else None
    return Contribution(
        requirement_id=requirement.id,
        requirement_text=requirement.text,
        category=requirement.category,
        must_have=requirement.must_have,
        display_order=requirement.display_order,
        weight=weight,
        verdict=verdict,
        verdict_value=value,
        points=None if value is None else weight * value,
    )


def _must_have_coverage(contributions: Sequence[Contribution]) -> Decimal | None:
    """The same weighted average, restricted to must-have requirements.

    Reported **alongside** the score rather than folded into it: a high average
    can conceal one missing hard requirement, which is the most misleading
    failure mode a weighted-average screener has.

    NULL when the job has no must-haves, and also when every must-have weight is
    zero -- in both cases there is no ratio to report, and inventing one would
    be worse than saying so.
    """
    must_haves = [item for item in contributions if item.must_have and item.is_scoreable]
    if not must_haves:
        return None

    weight = sum((item.weight for item in must_haves), Decimal("0"))
    if weight <= 0:
        return None

    points = sum((item.points for item in must_haves), Decimal("0"))
    return (points / weight).quantize(RATIO_PLACES, rounding=ROUND_HALF_UP)


def _apply_must_have_guard(
    contributions: Sequence[Contribution],
    band_raw: RecommendationBand,
    config: ScoringConfig,
) -> tuple[RecommendationBand, bool, uuid.UUID | None]:
    """Cap the displayed band when a hard requirement has no evidence.

    Deliberately keyed on the ``must_have`` flag rather than on weight: the flag
    and the weight are two separate effects of the same recruiter judgement
    (product-spec section 9), and a must-have with a low weight is still a
    must-have.

    The cap only ever lowers a band. Applying it to a band already at REVIEW or
    LOW_MATCH would raise the label, which would be the opposite of a guard.
    """
    if not config.must_have_guard:
        return band_raw, False, None
    if band_raw not in (RecommendationBand.STRONG_MATCH, RecommendationBand.GOOD_MATCH):
        return band_raw, False, None

    trigger = next(
        (
            item
            for item in contributions
            if item.must_have and item.verdict is MatchVerdict.NO_EVIDENCE
        ),
        None,
    )
    if trigger is not None:
        return RecommendationBand.REVIEW, True, trigger.requirement_id

    # A must-have the engine could not resolve is NOT missing evidence, so it
    # does not trip the guard above -- but the label must not read as a clean
    # pass either. The band is capped and the requirement named, while the
    # verdict itself stays NEEDS_REVIEW so the reason is recoverable from the
    # stored row rather than from a second column (ADR-0012).
    unresolved = next(
        (
            item
            for item in contributions
            if item.must_have and item.verdict is MatchVerdict.NEEDS_REVIEW
        ),
        None,
    )
    if unresolved is not None:
        return RecommendationBand.REVIEW, True, unresolved.requirement_id

    return band_raw, False, None


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def score_candidate(
    db: Session,
    candidate_id: uuid.UUID,
    *,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> tuple[Score, ScoreBreakdown]:
    """Compute and store one candidate's score.

    Preconditions, enforced here in the service so no route, task or future
    caller can go around them:

    * **the job's requirements are confirmed** -- ``get_confirmed_requirements``
      raises otherwise. Scoring a draft requirement set would produce a number
      against criteria no human has agreed to (ADR-0004);
    * **every confirmed requirement already has a verdict.** A score computed
      over a partial verdict set would silently under-count, and would look
      exactly like a complete one.

    Recomputation is free and expected: a recruiter changing a weight should see
    the number move immediately, with no model call.
    """
    candidate = candidates_service.get_candidate(db, candidate_id)

    # The gate.
    requirement_rows = requirements_service.get_confirmed_requirements(db, candidate.job_id)

    verdicts = _verdicts_for(db, candidate_id)
    missing = [item for item in requirement_rows if item.id not in verdicts]
    if missing:
        raise ConflictError(
            f"{len(missing)} of this job's {len(requirement_rows)} confirmed requirements "
            "have no match result for this candidate. Run matching before scoring.",
            details={"requirements_without_a_verdict": len(missing)},
        )

    breakdown = compute_score(
        [(item, verdicts[item.id]) for item in requirement_rows], config=config
    )
    row = _persist(db, candidate_id, breakdown)

    logger.info(
        "Scored candidate %s: status=%s score=%s band=%s capped=%s",
        candidate_id,
        breakdown.status.value,
        breakdown.score,
        breakdown.band.value if breakdown.band else None,
        breakdown.capped,
    )
    return row, breakdown


def _verdicts_for(db: Session, candidate_id: uuid.UUID) -> dict[uuid.UUID, MatchVerdict]:
    """This candidate's current verdicts, keyed by requirement.

    ``match_result`` is unique per (requirement, candidate) and a re-run replaces
    the whole set, so these rows *are* the current matching run. There is no
    separate run identifier to reconcile, and none is invented here.
    """
    return {
        row.requirement_id: row.verdict
        for row in db.scalars(select(MatchResult).where(MatchResult.candidate_id == candidate_id))
    }


def _persist(db: Session, candidate_id: uuid.UUID, breakdown: ScoreBreakdown) -> Score:
    """Replace this candidate's score row.

    Delete-then-insert rather than an in-place update: a recomputation is a new
    computation, and ``computed_at`` should say when it actually happened rather
    than when the first one did.
    """
    existing = get_score(db, candidate_id)
    if existing is not None:
        db.delete(existing)
        db.flush()

    row = Score(
        candidate_id=candidate_id,
        status=breakdown.status,
        weighted_sum=breakdown.weighted_sum,
        total_weight=breakdown.total_weight,
        score_raw=breakdown.score_raw,
        score=breakdown.score,
        must_have_coverage=breakdown.must_have_coverage,
        band_raw=breakdown.band_raw,
        band=breakdown.band,
        capped=breakdown.capped,
        capped_by_requirement_id=breakdown.capped_by_requirement_id,
        scoring_config_version=breakdown.config_version,
    )
    db.add(row)

    # SCORED is honest now that a `score` row exists. The transient SCORING
    # state is skipped deliberately: scoring is a synchronous arithmetic pass
    # with no I/O to be interrupted, so a status nobody could ever observe would
    # be noise rather than progress.
    candidate = candidates_service.get_candidate(db, candidate_id)
    if candidate.status is not CandidateStatus.FAILED:
        candidate.status = CandidateStatus.SCORED
        candidate.stage_started_at = None

    db.commit()
    db.refresh(row)
    return row


def get_score(db: Session, candidate_id: uuid.UUID) -> Score | None:
    return db.scalar(select(Score).where(Score.candidate_id == candidate_id))


def load_breakdown(db: Session, candidate_id: uuid.UUID) -> ScoreBreakdown | None:
    """Rebuild a stored score's per-requirement breakdown from its inputs.

    The contributions are **recomputed** from the requirements and verdicts that
    are still in the database, under the config version the stored row names.
    Nothing about the breakdown is duplicated into its own table: every input is
    already persisted, which is exactly what docs/data-model.md section 6 means
    by reconstructible.

    Returns None when the candidate has no stored score.
    """
    row = get_score(db, candidate_id)
    if row is None:
        return None

    candidate = candidates_service.get_candidate(db, candidate_id)
    requirement_rows = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.job_id == candidate.job_id)
            .order_by(Requirement.display_order, Requirement.created_at)
        )
    )
    verdicts = _verdicts_for(db, candidate_id)

    config = config_for_version(row.scoring_config_version)
    return compute_score(
        [(item, verdicts[item.id]) for item in requirement_rows if item.id in verdicts],
        config=config,
    )


#: Every scoring configuration this application has ever stored, by version.
#: A stored score names the bundle it was computed under, so a later change to
#: the constants cannot silently reinterpret an old row.
KNOWN_CONFIGS: dict[str, ScoringConfig] = {DEFAULT_CONFIG.version: DEFAULT_CONFIG}


def config_for_version(version: str) -> ScoringConfig:
    """The config a stored score names, or the current one if it is unknown.

    An unknown version means the row predates a config this build knows about.
    Recomputing it under today's rules is the best available reading, and the
    stored `scoring_config_version` still records the discrepancy honestly.
    """
    return KNOWN_CONFIGS.get(version, DEFAULT_CONFIG)


__all__ = [
    "DEFAULT_CONFIG",
    "Contribution",
    "ScoreBreakdown",
    "ScoringConfig",
    "band_for",
    "compute_score",
    "config_for_version",
    "get_score",
    "load_breakdown",
    "score_candidate",
]
