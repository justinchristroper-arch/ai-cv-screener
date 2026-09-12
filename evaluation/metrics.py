"""Compute the measurements, each carrying its own numerator and denominator.

Every metric here answers a question about **this application's deterministic
code**. None of them says anything about how good a language model is at reading
a CV, and the report is written so that distinction cannot be lost: metrics that
would need a live model are emitted as `not_measurable_offline` with the reason,
rather than being approximated from recorded replies.

The reason that matters: a recorded reply is something a human wrote. Scoring it
against a label the same human wrote measures their consistency and nothing
else. The deterministic layer is different — its verdicts are computed from the
profile by code, so comparing them against an independent reading of the CV is a
real test, and it is the one that finds real defects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from app.core.enums import MatchMethod, MatchVerdict
from app.services import ranking, scoring
from app.services.matching import Decision, decide_deterministically, load_alias_map
from evaluation.loader import Dataset


@dataclass
class Metric:
    """One measurement, stated so it cannot be quoted without its denominator."""

    name: str
    definition: str
    measures: str
    kind: str
    numerator: int | None = None
    denominator: int | None = None
    value: float | None = None
    limitations: str = ""
    reason_not_measurable: str | None = None
    detail: list[str] = field(default_factory=list)

    @classmethod
    def counted(
        cls,
        name: str,
        *,
        definition: str,
        measures: str,
        numerator: int,
        denominator: int,
        limitations: str,
        detail: list[str] | None = None,
    ) -> Metric:
        return cls(
            name=name,
            definition=definition,
            measures=measures,
            kind="deterministic",
            numerator=numerator,
            denominator=denominator,
            value=(numerator / denominator) if denominator else None,
            limitations=limitations,
            detail=detail or [],
        )

    @classmethod
    def unmeasurable(cls, name: str, *, definition: str, measures: str, reason: str) -> Metric:
        return cls(
            name=name,
            definition=definition,
            measures=measures,
            kind="llm_dependent",
            reason_not_measurable=reason,
            limitations="Requires a live provider. Replay fixtures cannot answer it.",
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PairOutcome:
    """What happened to one (candidate, requirement) pair, against its label."""

    candidate_id: str
    requirement_id: str
    requirement_text: str
    expected_verdict: MatchVerdict
    expected_layer: str
    actual_layer: str
    actual_verdict: MatchVerdict | None
    decided_by: MatchMethod | None
    why: str

    @property
    def layer_agrees(self) -> bool:
        return self.expected_layer == self.actual_layer

    @property
    def is_routing_false_positive(self) -> bool:
        """Code settled a pair only a reader should have settled."""
        return self.expected_layer == "SEMANTIC" and self.actual_layer == "DETERMINISTIC"

    @property
    def is_routing_miss(self) -> bool:
        """Code left a pair undecided that it had the facts to settle."""
        return self.expected_layer == "DETERMINISTIC" and self.actual_layer == "SEMANTIC"

    @property
    def verdict_agrees(self) -> bool:
        return self.actual_verdict is not None and self.actual_verdict == self.expected_verdict


def run_pairs(dataset: Dataset, alias_map: dict[str, str]) -> list[PairOutcome]:
    """Run the deterministic matcher over every candidate and compare to labels."""
    outcomes: list[PairOutcome] = []
    dataset_ids = dataset.requirement_dataset_ids

    for candidate in dataset.candidates:
        job = dataset.jobs[candidate.job_id]
        decided, _undecided = decide_deterministically(
            job.requirements,
            candidate.skills,
            candidate.roles,
            alias_map,
            candidate.usable_span_ids,
            as_of=dataset.as_of,
        )
        by_requirement: dict[str, Decision] = {
            str(decision.requirement_id): decision for decision in decided
        }

        for requirement in job.requirements:
            key = dataset_ids[job.id][str(requirement.id)]
            label = candidate.labels[key]
            decision = by_requirement.get(str(requirement.id))
            outcomes.append(
                PairOutcome(
                    candidate_id=candidate.id,
                    requirement_id=key,
                    requirement_text=requirement.text,
                    expected_verdict=label.verdict,
                    expected_layer=label.layer,
                    actual_layer="DETERMINISTIC" if decision else "SEMANTIC",
                    actual_verdict=decision.verdict if decision else None,
                    decided_by=decision.decided_by if decision else None,
                    why=label.why,
                )
            )
    return outcomes


def _describe(outcome: PairOutcome) -> str:
    actual = outcome.actual_verdict.value if outcome.actual_verdict else "undecided"
    method = outcome.decided_by.value if outcome.decided_by else "routed to model"
    return (
        f"{outcome.candidate_id} / {outcome.requirement_id} "
        f"({outcome.requirement_text[:56]}) — expected {outcome.expected_verdict.value} "
        f"via {outcome.expected_layer}, got {actual} via {method}"
    )


def routing_metrics(outcomes: list[PairOutcome]) -> list[Metric]:
    """How well code judges which pairs it is entitled to settle."""
    semantic_expected = [o for o in outcomes if o.expected_layer == "SEMANTIC"]
    deterministic_expected = [o for o in outcomes if o.expected_layer == "DETERMINISTIC"]
    false_positives = [o for o in semantic_expected if o.is_routing_false_positive]
    misses = [o for o in deterministic_expected if o.is_routing_miss]

    return [
        Metric.counted(
            "routing_restraint",
            definition=(
                "Of the pairs a reader should settle, the share the deterministic "
                "matchers correctly left undecided."
            ),
            measures=(
                "Whether ordinary code knows when it does not have enough to decide. "
                "A failure here is the costliest error the system makes: a confident "
                "verdict with evidence attached, on a question code cannot answer."
            ),
            numerator=len(semantic_expected) - len(false_positives),
            denominator=len(semantic_expected),
            limitations="Bounded by the eight synthetic candidates in this dataset.",
            detail=[_describe(o) for o in false_positives],
        ),
        Metric.counted(
            "deterministic_recall",
            definition=(
                "Of the pairs ordinary code has the facts to settle, the share it did settle."
            ),
            measures=(
                "How much work is kept away from the model. A low value costs money "
                "and reproducibility; it is not a correctness failure."
            ),
            numerator=len(deterministic_expected) - len(misses),
            denominator=len(deterministic_expected),
            limitations="Bounded by the eight synthetic candidates in this dataset.",
            detail=[_describe(o) for o in misses],
        ),
    ]


def verdict_metrics(outcomes: list[PairOutcome]) -> list[Metric]:
    """Whether the verdicts code produced are the right ones."""
    decided = [o for o in outcomes if o.actual_layer == "DETERMINISTIC"]
    correct = [o for o in decided if o.verdict_agrees and o.layer_agrees]
    wrong = [o for o in decided if not (o.verdict_agrees and o.layer_agrees)]

    over_credit = [
        o
        for o in decided
        if o.actual_verdict is not None and _rank(o.actual_verdict) > _rank(o.expected_verdict)
    ]
    under_credit = [
        o
        for o in decided
        if o.actual_verdict is not None and _rank(o.actual_verdict) < _rank(o.expected_verdict)
    ]

    return [
        Metric.counted(
            "deterministic_verdict_precision",
            definition=(
                "Of the pairs the deterministic matchers decided, the share whose "
                "verdict matches the reference answer and which they were entitled to decide."
            ),
            measures="Correctness of the exact, alias and duration matchers.",
            numerator=len(correct),
            denominator=len(decided),
            limitations="Bounded by the eight synthetic candidates in this dataset.",
            detail=[_describe(o) for o in wrong],
        ),
        Metric.counted(
            "over_crediting_rate",
            definition=(
                "Of the pairs the deterministic matchers decided, the share given a more "
                "generous verdict than the reference answer."
            ),
            measures=(
                "Errors in the direction that flatters a candidate. These matter more "
                "than the opposite, because a recruiter is less likely to question them."
            ),
            numerator=len(over_credit),
            denominator=len(decided),
            limitations="Bounded by the eight synthetic candidates in this dataset.",
            detail=[_describe(o) for o in over_credit],
        ),
        Metric.counted(
            "under_crediting_rate",
            definition=(
                "Of the pairs the deterministic matchers decided, the share given a less "
                "generous verdict than the reference answer."
            ),
            measures="Errors in the direction that penalises a candidate.",
            numerator=len(under_credit),
            denominator=len(decided),
            limitations="Bounded by the eight synthetic candidates in this dataset.",
            detail=[_describe(o) for o in under_credit],
        ),
    ]


def _rank(verdict: MatchVerdict) -> int:
    return {MatchVerdict.NO_EVIDENCE: 0, MatchVerdict.PARTIAL: 1, MatchVerdict.MATCHED: 2}[verdict]


def evidence_metrics(dataset: Dataset) -> list[Metric]:
    """Whether the verifier locates real quotes and refuses the rest."""
    checks = [check for candidate in dataset.candidates for check in candidate.evidence]
    located = [c for c in checks if c.verified]
    instruction_like = [c for c in checks if c.instruction_like]
    usable = [c for c in checks if c.usable]

    return [
        Metric.counted(
            "evidence_located_rate",
            definition=(
                "Of the declared evidence quotes, the share the verifier found in the CV text."
            ),
            measures=(
                "The verifier's ability to locate a genuine quotation. Every quote in this "
                "dataset is copied from its own CV text, so anything below 100% is a "
                "verifier defect rather than a dataset property."
            ),
            numerator=len(located),
            denominator=len(checks),
            limitations="Says nothing about a model's tendency to invent quotes.",
            detail=[f"not located: {c.owner} — {c.quote[:60]}" for c in checks if not c.verified],
        ),
        Metric.counted(
            "instruction_evidence_refused",
            definition=(
                "Of the declared evidence quotes that read as instructions rather than as "
                "CV content, the share the system refused to treat as usable evidence."
            ),
            measures=(
                "The control that catches an injected instruction being cited as proof of a "
                "skill. Verification alone cannot catch it — the sentence really is in the "
                "document — so this is a separate check."
            ),
            numerator=len([c for c in instruction_like if not c.usable]),
            denominator=len(instruction_like),
            limitations=(
                "Measures refusal of the patterns the scanner knows. It is not evidence of "
                "resistance to prompt injection in general."
            ),
            detail=[f"flagged: {c.owner} — {c.quote[:60]}" for c in instruction_like],
        ),
        Metric.counted(
            "usable_evidence_rate",
            definition="Of the declared evidence quotes, the share usable to support a verdict.",
            measures="How much of the profile can support a positive verdict at all.",
            numerator=len(usable),
            denominator=len(checks),
            limitations="A dataset property as much as a system property; read with the two above.",
        ),
    ]


def scoring_metrics(dataset: Dataset, outcomes: list[PairOutcome]) -> list[Metric]:
    """Whether the scorer is reproducible and reconstructible from its inputs."""
    reproducible = 0
    reconstructible = 0
    scored = 0
    mismatches: list[str] = []

    dataset_ids = dataset.requirement_dataset_ids
    by_candidate = {c.id: c for c in dataset.candidates}

    for candidate_id, pairs in _group(outcomes).items():
        candidate = by_candidate[candidate_id]
        job = dataset.jobs[candidate.job_id]
        expected = {p.requirement_id: p.expected_verdict for p in pairs}
        graded = [
            (requirement, expected[dataset_ids[job.id][str(requirement.id)]])
            for requirement in job.requirements
        ]

        first = scoring.compute_score(graded)
        second = scoring.compute_score(graded)
        scored += 1
        if (first.score, first.score_raw, first.weighted_sum) == (
            second.score,
            second.score_raw,
            second.weighted_sum,
        ):
            reproducible += 1

        total = sum((item.points for item in first.contributions), Decimal("0"))
        if total == first.weighted_sum:
            reconstructible += 1
        else:
            mismatches.append(
                f"{candidate_id}: contributions {total} != weighted_sum {first.weighted_sum}"
            )

    return [
        Metric.counted(
            "score_reproducibility",
            definition=(
                "Of the candidates scored, the share whose score is identical when recomputed."
            ),
            measures="That scoring is a pure function of its inputs, with no hidden state.",
            numerator=reproducible,
            denominator=scored,
            limitations=(
                "Reproducibility is guaranteed by construction; this checks the guarantee holds."
            ),
        ),
        Metric.counted(
            "score_reconstructibility",
            definition=(
                "Of the candidates scored, the share whose per-requirement contributions sum "
                "exactly to the stored weighted total."
            ),
            measures="That the arithmetic shown to a recruiter is the arithmetic used.",
            numerator=reconstructible,
            denominator=scored,
            limitations="Exact decimal comparison; no tolerance is applied.",
            detail=mismatches,
        ),
    ]


def ranking_metrics(dataset: Dataset, outcomes: list[PairOutcome]) -> list[Metric]:
    """Whether ranking is a total order and blind to the candidate's name."""
    dataset_ids = dataset.requirement_dataset_ids
    by_candidate = {c.id: c for c in dataset.candidates}
    grouped = _group(outcomes)

    scores: dict[str, Any] = {}
    for candidate_id, pairs in grouped.items():
        candidate = by_candidate[candidate_id]
        job = dataset.jobs[candidate.job_id]
        expected = {p.requirement_id: p.expected_verdict for p in pairs}
        graded = [
            (requirement, expected[dataset_ids[job.id][str(requirement.id)]])
            for requirement in job.requirements
        ]
        scores[candidate_id] = scoring.compute_score(graded)

    stable = 0
    jobs_ranked = 0
    for job_id in dataset.jobs:
        members = [c for c in dataset.candidates if c.job_id == job_id]
        if not members:
            continue
        jobs_ranked += 1
        inputs = [
            ranking.RankingInputs(
                candidate_id=uuid_of(candidate.id),
                created_at=_fixed_time(index),
                score=scores[candidate.id].score,
                must_have_coverage=scores[candidate.id].must_have_coverage,
                matched_count=sum(
                    1 for p in grouped[candidate.id] if p.expected_verdict is MatchVerdict.MATCHED
                ),
            )
            for index, candidate in enumerate(members)
        ]
        first = [item.candidate_id for item in ranking.order_candidates(inputs)]
        second = [item.candidate_id for item in ranking.order_candidates(list(reversed(inputs)))]
        if first == second:
            stable += 1

    twins = [c for c in dataset.candidates if c.counterfactual_of]
    identical = 0
    twin_detail: list[str] = []
    for twin in twins:
        original = scores[twin.counterfactual_of]
        copy = scores[twin.id]
        if (original.score, original.must_have_coverage, original.band) == (
            copy.score,
            copy.must_have_coverage,
            copy.band,
        ):
            identical += 1
        else:
            twin_detail.append(
                f"{twin.id}: {copy.score} vs {twin.counterfactual_of}: {original.score}"
            )

    return [
        Metric.counted(
            "ranking_total_order",
            definition=(
                "Of the jobs ranked, the share whose order is identical when the same "
                "candidates are supplied in reverse."
            ),
            measures=(
                "That the tie-break produces a total order, so a recruiter refreshing the "
                "page never sees two candidates swap."
            ),
            numerator=stable,
            denominator=jobs_ranked,
            limitations="Two jobs only.",
        ),
        Metric.counted(
            "counterfactual_name_invariance",
            definition=(
                "Of the name-only twin pairs, the share scoring identically to their original."
            ),
            measures=(
                "That this application's own arithmetic cannot read a name. It is a "
                "sensitivity test of deterministic code, NOT a bias audit, and it says "
                "nothing about how a language model would treat the same two CVs."
            ),
            numerator=identical,
            denominator=len(twins),
            limitations=(
                "One twin pair, and only the deterministic half of the pipeline. Model "
                "sensitivity to names is not measured here and is not measurable offline."
            ),
            detail=twin_detail,
        ),
    ]


def llm_dependent_metrics() -> list[Metric]:
    """The measurements this configuration cannot honestly make."""
    circular = (
        "The only model output available offline is a recording written by the same "
        "author as the labels. Scoring one against the other would measure that author's "
        "consistency, not the model's quality."
    )
    return [
        Metric.unmeasurable(
            "requirement_extraction_precision_recall",
            definition="Agreement between extracted requirements and a reference requirement set.",
            measures="How well the model reads a job description.",
            reason=circular,
        ),
        Metric.unmeasurable(
            "semantic_verdict_agreement",
            definition=(
                "Agreement between model verdicts and the reference answer, as a confusion matrix."
            ),
            measures="How well the model judges a requirement against a CV.",
            reason=circular,
        ),
        Metric.unmeasurable(
            "run_to_run_stability",
            definition="Share of verdicts that change across repeated runs on identical input.",
            measures="Model nondeterminism.",
            reason=(
                "Replay is deterministic by construction, so this would report 100% "
                "stability and mean nothing."
            ),
        ),
        Metric.unmeasurable(
            "counterfactual_model_sensitivity",
            definition="Whether model verdicts change when only the candidate's name changes.",
            measures="Model sensitivity to a name.",
            reason=(
                "A fixture is keyed by a hash of its input, so a renamed CV has no "
                "recording. Hand-writing one would be writing the answer."
            ),
        ),
        Metric.unmeasurable(
            "ranking_correlation",
            definition=(
                "Spearman correlation and top-k overlap between the produced ranking and "
                "a human reference ranking per job."
            ),
            measures="Whether the order a recruiter is shown agrees with a human's order.",
            reason=(
                "A ranking is downstream of every verdict, and most verdicts here belong "
                "to the model. Offline, the only verdicts available are the reference "
                "labels themselves — so the produced ranking would be computed from the "
                "reference ranking's own inputs and correlate perfectly by construction. "
                "What can be measured without that circularity is measured instead, as "
                "`ranking_total_order`: that the ordering is total and stable."
            ),
        ),
        Metric.unmeasurable(
            "cost_and_latency_per_cv",
            definition="Tokens and wall-clock time per candidate screened.",
            measures="What the pipeline costs to run.",
            reason=(
                "Replay records no token usage and zero latency, deliberately: recording "
                "zeros would misstate cost."
            ),
        ),
    ]


def _group(outcomes: list[PairOutcome]) -> dict[str, list[PairOutcome]]:
    grouped: dict[str, list[PairOutcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.candidate_id, []).append(outcome)
    return grouped


def uuid_of(candidate_id: str) -> Any:
    import uuid as _uuid

    return _uuid.uuid5(_uuid.NAMESPACE_URL, f"eval-candidate/{candidate_id}")


def _fixed_time(index: int) -> Any:
    from datetime import datetime, timedelta, timezone

    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)


def collect(dataset: Dataset, alias_map: dict[str, str]) -> tuple[list[PairOutcome], list[Metric]]:
    """Run everything and return the pair-level outcomes plus every metric."""
    outcomes = run_pairs(dataset, alias_map)
    metrics = [
        *routing_metrics(outcomes),
        *verdict_metrics(outcomes),
        *evidence_metrics(dataset),
        *scoring_metrics(dataset, outcomes),
        *ranking_metrics(dataset, outcomes),
        *llm_dependent_metrics(),
    ]
    return outcomes, metrics


__all__ = ["Metric", "PairOutcome", "collect", "load_alias_map", "run_pairs"]
