"""Measure the engine that answers the product's default path.

`metrics.py` measures `decide_deterministically` — the profile-and-model path
that ADR-0012 demoted to an exception. Nothing measured the replacement, so the
numbers `RESULTS.md` published described code most screening runs no longer
touch. This module closes that gap: it runs `cv_facts` and `structured_match`
over the eight synthetic CVs the legacy harness uses, plus the CVs
`data/structured.json` declares itself, against the hand-written labels there.

The eight legacy CVs are clean, English and single-column, and on them this
engine agreed with every label. The CVs added afterwards have the shapes real
uploads arrive in -- a two-column flattening, Indonesian headings, organisational
sections, dates on a line of their own, a degree still being read, misspellings --
and on their first run they found three defects the eight had no way to reveal.

Read the boundary before the numbers, as everywhere else in this harness. What
is measured is **this application's deterministic reading of a small set of
invented CVs**. It is not real-world screening accuracy, it is not a bias audit,
and the sample is small enough that a single disagreement moves a percentage by
about a point.

Four of the seven metrics below need no labels at all — a cited quote either is
or is not in the source text — which is why they are the ones worth trusting
most.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.enums import MatchVerdict
from app.core.protected_attributes import names_a_protected_attribute
from app.services import cv_facts, structured_match
from evaluation.loader import Dataset
from evaluation.metrics import Metric

DATA = pathlib.Path(__file__).resolve().parent / "data" / "structured.json"

#: Only the three verdicts that carry a judgement are ordered. NEEDS_REVIEW is
#: deliberately outside it: it is not a weaker MATCHED, and treating it as a
#: point on the scale is exactly the conflation ADR-0012 added it to prevent.
_RANK = {
    MatchVerdict.NO_EVIDENCE: 0,
    MatchVerdict.PARTIAL: 1,
    MatchVerdict.MATCHED: 2,
}


def _sample_caveat(documents: int, pairs: int) -> str:
    """Counted from the data, so the caveat cannot drift from what was measured."""
    return (
        f"{documents} invented CVs and {pairs} labelled pairs, all written by this "
        "repository's author. Measures this application's deterministic reading of "
        "those documents, not real-world screening accuracy."
    )


@dataclass(frozen=True)
class StructuredOutcome:
    """One criterion put to one CV, beside what the document actually supports."""

    candidate_id: str
    criterion_id: str
    spec_type: str
    expected: MatchVerdict
    actual: MatchVerdict
    why: str
    quote: str | None
    #: The document this criterion was put to. Carried here rather than looked
    #: up, because this dataset holds CVs the legacy dataset does not.
    cv_text: str

    @property
    def agrees(self) -> bool:
        return self.expected is self.actual

    @property
    def over_credits(self) -> bool:
        """Engine more favourable than the document supports.

        Only comparable where both verdicts sit on the ordered scale; a
        disagreement involving NEEDS_REVIEW is a disagreement, not a
        mis-credit in either direction.
        """
        if self.expected not in _RANK or self.actual not in _RANK:
            return False
        return _RANK[self.actual] > _RANK[self.expected]

    @property
    def under_credits(self) -> bool:
        if self.expected not in _RANK or self.actual not in _RANK:
            return False
        return _RANK[self.actual] < _RANK[self.expected]


def _spec(raw: dict[str, Any]) -> structured_match.RequirementSpec:
    def dec(key: str) -> Decimal | None:
        value = raw.get(key)
        return None if value is None else Decimal(value)

    return structured_match.RequirementSpec(
        spec_type=raw["spec_type"],
        subject=raw.get("subject"),
        threshold_value=dec("threshold_value"),
        scale=dec("threshold_scale"),
    )


@dataclass(frozen=True)
class StructuredCv:
    """One document in the structured corpus, whatever file declared it."""

    id: str
    job_id: str
    cv_text: str


def corpus(dataset: Dataset) -> list[StructuredCv]:
    """The eight legacy CVs, plus the ones this dataset declares itself.

    The legacy dataset couples a candidate to a full set of profile items and
    thirteen model-path labels, all of which measure the path ADR-0012 demoted.
    A CV added only to exercise *reading* -- a two-column flattening, an
    Indonesian section heading, a misspelling -- should not have to carry any of
    that, so `structured.json` declares its own documents and this walks both.
    """
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    own = [
        StructuredCv(item["id"], item["job_id"], "\n".join(item["cv_text"]))
        for item in raw.get("candidates", [])
    ]
    inherited = [
        StructuredCv(candidate.id, candidate.job_id, candidate.cv_text)
        for candidate in dataset.candidates
    ]
    return inherited + own


def run_structured(dataset: Dataset) -> list[StructuredOutcome]:
    """Every labelled (candidate, structured criterion) pair, screened."""
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    criteria = raw["criteria"]
    labels = raw["labels"]

    outcomes: list[StructuredOutcome] = []
    for candidate in corpus(dataset):
        facts = cv_facts.extract_facts(
            candidate.cv_text,
            as_of_year=dataset.as_of.year,
            as_of_month=dataset.as_of.month,
        )
        for item in criteria[candidate.job_id]:
            label = labels[candidate.id][item["id"]]
            result = structured_match.match(_spec(item), facts)
            outcomes.append(
                StructuredOutcome(
                    candidate_id=candidate.id,
                    criterion_id=item["id"],
                    spec_type=item["spec_type"],
                    expected=MatchVerdict(label["verdict"]),
                    actual=result.verdict,
                    why=label["why"],
                    quote=result.span.text if result.span else None,
                    cv_text=candidate.cv_text,
                )
            )
    return outcomes


def _describe(outcome: StructuredOutcome) -> str:
    return (
        f"{outcome.candidate_id} / {outcome.criterion_id} ({outcome.spec_type}): "
        f"expected {outcome.expected.value}, got {outcome.actual.value} - {outcome.why}"
    )


def structured_metrics(dataset: Dataset) -> list[Metric]:
    outcomes = run_structured(dataset)
    caveat = _sample_caveat(len(corpus(dataset)), len(outcomes))

    agreed = [item for item in outcomes if item.agrees]
    over = [item for item in outcomes if item.over_credits]
    under = [item for item in outcomes if item.under_credits]

    # Evidence. Needs no label: a quote is either in the document or it is not.
    cited = [item for item in outcomes if item.quote]
    located = [item for item in cited if item.quote in item.cv_text]
    sensitive = [item for item in cited if names_a_protected_attribute(item.quote or "")]

    # Every positive verdict must cite something.
    positive = [
        item for item in outcomes if item.actual in (MatchVerdict.MATCHED, MatchVerdict.PARTIAL)
    ]
    positive_cited = [item for item in positive if item.quote]

    # Repeatability: the same text and the same criteria, twice.
    again = run_structured(dataset)
    identical = sum(
        1
        for first, second in zip(outcomes, again, strict=True)
        if first.actual is second.actual and first.quote == second.quote
    )

    return [
        Metric.counted(
            "structured_verdict_agreement",
            definition=(
                "Pairs where the structured engine's verdict equals the hand-written "
                "label, over all labelled pairs."
            ),
            measures="services/structured_match over services/cv_facts. No model involved.",
            numerator=len(agreed),
            denominator=len(outcomes),
            limitations=caveat,
            detail=[_describe(item) for item in outcomes if not item.agrees],
        ),
        Metric.counted(
            "structured_over_crediting",
            definition=(
                "Pairs where the engine returned a more favourable verdict than the "
                "document supports, over pairs comparable on the MATCHED/PARTIAL/"
                "NO_EVIDENCE scale. The costlier direction of error."
            ),
            measures="Whether the engine credits a candidate for something the CV does not say.",
            numerator=len(over),
            denominator=len([item for item in outcomes if item.expected in _RANK]),
            limitations=(
                caveat + " NEEDS_REVIEW is outside the scale, so a disagreement involving it is "
                "counted in agreement but in neither credit direction."
            ),
            detail=[_describe(item) for item in over],
        ),
        Metric.counted(
            "structured_under_crediting",
            definition="The same comparison in the other direction.",
            measures="Whether the engine withholds credit the CV does support.",
            numerator=len(under),
            denominator=len([item for item in outcomes if item.expected in _RANK]),
            limitations=caveat,
            detail=[_describe(item) for item in under],
        ),
        Metric.counted(
            "structured_evidence_located",
            definition=(
                "Cited quotes found verbatim in the candidate's own CV text, over all "
                "quotes the engine cited."
            ),
            measures=(
                "Whether a verdict's quotation is real. Needs no label: the quote is "
                "either in the document or it is not."
            ),
            numerator=len(located),
            denominator=len(cited),
            limitations="Locating a quote says it exists, not that it supports the verdict.",
            detail=[
                f"{item.candidate_id} / {item.criterion_id}: {item.quote!r} not in the CV"
                for item in cited
                if item not in located
            ],
        ),
        Metric.counted(
            "structured_positive_verdicts_cite_evidence",
            definition="MATCHED or PARTIAL verdicts carrying a quote, over all such verdicts.",
            measures="ADR-0002 as an engine property rather than a database constraint.",
            numerator=len(positive_cited),
            denominator=len(positive),
            limitations=caveat,
            detail=[
                f"{item.candidate_id} / {item.criterion_id}: {item.actual.value} with no quote"
                for item in positive
                if not item.quote
            ],
        ),
        Metric.counted(
            "structured_spans_free_of_protected_attributes",
            definition=("Cited quotes naming no protected characteristic, over all quotes cited."),
            measures=(
                "ADR-0003 at the point it is most easily broken. A real CV put a place of "
                "worship into an evidence quote by having a volunteering line read as a job."
            ),
            numerator=len(cited) - len(sensitive),
            denominator=len(cited),
            limitations=(
                "The scanner reads Indonesian and English patterns and will miss a "
                "paraphrase or a venue name it does not know."
            ),
            detail=[
                f"{item.candidate_id} / {item.criterion_id}: {item.quote!r}" for item in sensitive
            ],
        ),
        Metric.counted(
            "structured_repeatability",
            definition="Pairs whose verdict and quote are identical on a second run.",
            measures="That the engine reads no clock and holds no state.",
            numerator=identical,
            denominator=len(outcomes),
            limitations="Two runs in one process. Says nothing about a different build.",
        ),
    ]


__all__ = ["StructuredOutcome", "run_structured", "structured_metrics"]
