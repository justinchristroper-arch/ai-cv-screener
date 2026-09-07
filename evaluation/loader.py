"""Read the gold dataset and turn it into the objects the pipeline works on.

The dataset declares jobs, requirements, candidate profiles and expected
outcomes as plain data. This module builds the ORM objects the real services
take, **without a database** — every object here is unsaved, because the
deterministic parts of the pipeline are pure functions over rows rather than
queries against them, and evaluating them should not need Postgres running.

Two things are built rather than declared, on purpose:

* `normalized_name` on each skill comes from the real `normalize_skill_name`,
  not from the dataset. A dataset that pre-normalized its own skills would be
  grading the matcher against its own assumptions.
* every evidence quote is checked against the candidate's CV text with the real
  verifier. A quote that cannot be located, or that reads as an instruction
  rather than as CV content, yields an unusable span — which is exactly what the
  adversarial candidate is there to exercise.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.core.enums import DatePrecision, MatchVerdict, RequirementCategory, RequirementOrigin
from app.models.job import Requirement
from app.models.profile import ProfileExperience, ProfileSkill
from app.services.evidence import verify_quote
from app.services.matching import normalize_skill_name
from app.services.profile_extraction import parse_partial_date

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class Label:
    """The reference answer for one (candidate, requirement) pair."""

    verdict: MatchVerdict
    layer: str
    why: str

    @property
    def is_deterministic(self) -> bool:
        return self.layer == "DETERMINISTIC"


@dataclass(frozen=True)
class EvidenceCheck:
    """What the verifier made of one declared quote."""

    owner: str
    quote: str
    verified: bool
    instruction_like: bool

    @property
    def usable(self) -> bool:
        return self.verified and not self.instruction_like


@dataclass
class Job:
    id: str
    title: str
    note: str
    requirements: list[Requirement]

    def requirement_by_dataset_id(self) -> dict[str, Requirement]:
        return {str(item.id): item for item in self.requirements}


@dataclass
class Candidate:
    id: str
    job_id: str
    display_name: str
    purpose: str
    cv_text: str
    skills: list[ProfileSkill]
    roles: list[ProfileExperience]
    evidence: list[EvidenceCheck]
    usable_span_ids: set[uuid.UUID]
    counterfactual_of: str | None = None
    labels: dict[str, Label] = field(default_factory=dict)


@dataclass
class Dataset:
    as_of: date
    jobs: dict[str, Job]
    candidates: list[Candidate]

    def candidate(self, candidate_id: str) -> Candidate:
        for item in self.candidates:
            if item.id == candidate_id:
                return item
        raise KeyError(candidate_id)


def _read(name: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _join(lines: Any) -> str:
    return "\n".join(lines) if isinstance(lines, list) else str(lines)


def _requirement(spec: dict[str, Any], order: int, job_uuid: uuid.UUID) -> Requirement:
    """An unsaved requirement whose id carries the dataset's own identifier.

    The dataset id is stored as the primary key so results can name `a3` rather
    than a random UUID; nothing in the matcher parses it.
    """
    return Requirement(
        id=uuid.uuid5(job_uuid, spec["id"]),
        job_id=job_uuid,
        text=spec["text"],
        category=RequirementCategory(spec["category"]),
        must_have=bool(spec["must_have"]),
        weight=spec["weight"],
        display_order=order,
        origin=RequirementOrigin.HR_ADDED,
    )


def load_dataset() -> Dataset:
    """Load jobs, candidates and labels, and verify every declared quote."""
    jobs_raw = _read("jobs.json")
    candidates_raw = _read("candidates.json")
    labels_raw = _read("labels.json")

    jobs: dict[str, Job] = {}
    dataset_ids: dict[str, dict[str, str]] = {}
    for spec in jobs_raw["jobs"]:
        job_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"eval-job/{spec['id']}")
        requirements = [
            _requirement(item, order, job_uuid) for order, item in enumerate(spec["requirements"])
        ]
        jobs[spec["id"]] = Job(
            id=spec["id"],
            title=spec["title"],
            note=spec.get("note", ""),
            requirements=requirements,
        )
        dataset_ids[spec["id"]] = {
            str(requirement.id): item["id"]
            for requirement, item in zip(requirements, spec["requirements"], strict=True)
        }

    candidates: list[Candidate] = []
    for spec in candidates_raw["candidates"]:
        candidates.append(_build_candidate(spec, labels_raw["candidates"][spec["id"]]))

    dataset = Dataset(
        as_of=date.fromisoformat(jobs_raw["as_of"]),
        jobs=jobs,
        candidates=candidates,
    )
    dataset.requirement_dataset_ids = dataset_ids  # type: ignore[attr-defined]
    return dataset


def _build_candidate(spec: dict[str, Any], label_spec: dict[str, Any]) -> Candidate:
    cv_text = _join(spec["cv_text"])
    evidence: list[EvidenceCheck] = []
    usable: set[uuid.UUID] = set()

    def span_for(owner: str, quote: str) -> uuid.UUID:
        """Verify a declared quote and mint a span id for it."""
        result = verify_quote(quote, cv_text, [{"page": 1, "start": 0, "end": len(cv_text)}])
        span_id = uuid.uuid4()
        evidence.append(
            EvidenceCheck(
                owner=owner,
                quote=quote,
                verified=result.is_verified,
                instruction_like=result.instruction_like,
            )
        )
        if result.is_usable:
            usable.add(span_id)
        return span_id

    skills = [
        ProfileSkill(
            id=uuid.uuid4(),
            raw_name=item["name"],
            # Computed by the application, never declared by the dataset.
            normalized_name=normalize_skill_name(item["name"]),
            evidence_span_id=span_for(f"skill:{item['name']}", item["evidence"]),
        )
        for item in spec.get("skills", [])
    ]

    roles: list[ProfileExperience] = []
    for item in spec.get("experience", []):
        start, start_precision = parse_partial_date(item.get("start_date"))
        end, end_precision = parse_partial_date(item.get("end_date"))
        order = [DatePrecision.DAY, DatePrecision.MONTH, DatePrecision.YEAR, DatePrecision.UNKNOWN]
        known = [p for p in (start_precision, end_precision) if p is not DatePrecision.UNKNOWN]
        roles.append(
            ProfileExperience(
                id=uuid.uuid4(),
                role_title=item["role_title"],
                organization=item.get("organization"),
                start_date=start,
                end_date=end,
                date_precision=max(known, key=order.index) if known else DatePrecision.UNKNOWN,
                is_current=bool(item.get("is_current", False)) and end is None,
                description=None,
                evidence_span_id=span_for(f"role:{item['role_title']}", item["evidence"]),
            )
        )

    labels = {
        key: Label(verdict=MatchVerdict(value["verdict"]), layer=value["layer"], why=value["why"])
        for key, value in label_spec.items()
    }

    return Candidate(
        id=spec["id"],
        job_id=spec["job_id"],
        display_name=spec["display_name"],
        purpose=spec.get("purpose", ""),
        cv_text=cv_text,
        skills=skills,
        roles=roles,
        evidence=evidence,
        usable_span_ids=usable,
        counterfactual_of=spec.get("counterfactual_of"),
        labels=labels,
    )
