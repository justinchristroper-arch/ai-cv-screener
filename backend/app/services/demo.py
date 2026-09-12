"""Seed a ready-to-browse demo job from the synthetic sample data.

The product is meant to be runnable with **no API key and no cost**
(product-spec.md section 15). The pieces for that already exist -- demo mode
serves every model call from a recorded fixture -- but a fixture is keyed by a
hash of its input, so a demo only replays when it is driven by exactly the
documents the fixtures were recorded against. This module drives them.

Two rules it observes:

* **Demo mode only.** Seeding is refused when ``DEMO_MODE`` is false. An
  endpoint that manufactures candidate records has no business existing in a
  deployment handling real applications, and the check lives here in the
  service rather than only in the route.
* **Labelled as synthetic.** The seeded job's title carries a ``[Demo]``
  prefix, so demo data is identifiable everywhere it is displayed without the
  UI having to guess.

Nothing here is a shortcut around the pipeline. The seed uploads real PDF bytes
through the ordinary upload service and then calls the ordinary extraction,
matching and scoring services in order, through the same confirmation gate a
recruiter goes through. What it demonstrates is what actually happens.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT
from app.core.enums import CandidateStatus, JdSourceType
from app.core.errors import (
    ConflictError,
    ExtractionFailedError,
    LlmUnavailableError,
    NotFoundError,
)
from app.core.storage import DocumentStorage
from app.llm.client import LlmClient
from app.llm.fixtures import fixture_jd_text
from app.models.job import Job
from app.services import candidates as candidates_service
from app.services import (
    jd_extraction,
    jobs,
    matching,
    profile_extraction,
    requirements,
    scoring,
    structured_match,
)

logger = logging.getLogger(__name__)

SAMPLE_DIR = REPO_ROOT / "data" / "sample"

#: The fixture the seeded demo job is built from. Read rather than copied, so
#: the demo cannot drift away from the extraction fixture it depends on -- there
#: is exactly one copy of this text in the repository.
SAMPLE_JD_FIXTURE = "jd_backend_engineer"

DEMO_JOB_TITLE = "[Demo] Senior Backend Engineer"

#: The id that selects the structured demo. Not a fixture stem, because the
#: structured path has no recordings to key: it calls no model at all.
STRUCTURED_CRITERIA_ID = "structured"

STRUCTURED_DEMO_TITLE = "[Demo] Backend Engineer"


@dataclass(frozen=True)
class DemoCriterion:
    """One criterion the structured demo screens with, and why it is here.

    The six were chosen against the bundled CVs so the result shows a real
    spread -- a match, a partial and several honest gaps -- rather than six
    green ticks. A demo where everything matches teaches a reader nothing about
    how the product behaves when a document does not say what was asked for.
    """

    spec: structured_match.RequirementSpec
    must_have: bool
    demonstrates: str

    @property
    def text(self) -> str:
        return structured_match.describe(self.spec)


STRUCTURED_CRITERIA: tuple[DemoCriterion, ...] = (
    DemoCriterion(
        spec=structured_match.RequirementSpec(structured_match.EDUCATION_MIN, subject="S1"),
        must_have=True,
        demonstrates=(
            "A degree level read out of the CV's own education section. The "
            "second sample CV states no qualification at all, which comes back "
            "as a gap in the document rather than as a claim about the person."
        ),
    ),
    DemoCriterion(
        spec=structured_match.RequirementSpec(
            structured_match.EXPERIENCE_MIN, threshold_value=Decimal("48")
        ),
        must_have=True,
        demonstrates=(
            "Date arithmetic over the dated entries, with overlapping roles "
            "counted once. Short of the minimum comes back as partial, not as a "
            "failure."
        ),
    ),
    DemoCriterion(
        spec=structured_match.RequirementSpec(structured_match.SKILL, subject="Python"),
        must_have=True,
        demonstrates="A skill from the supported list, matched on the line that states it.",
    ),
    DemoCriterion(
        spec=structured_match.RequirementSpec(structured_match.SKILL, subject="PostgreSQL"),
        must_have=False,
        demonstrates=(
            "The same, through an alias: one CV writes 'Postgres' and is "
            "matched, the other never mentions it and is not."
        ),
    ),
    DemoCriterion(
        spec=structured_match.RequirementSpec(structured_match.INTERNSHIP_MIN),
        must_have=False,
        demonstrates=(
            "Presence alone, with no duration. Neither sample CV has one, so "
            "both report the absence plainly."
        ),
    ),
    DemoCriterion(
        spec=structured_match.RequirementSpec(structured_match.LANGUAGE_PRESENT, subject="English"),
        must_have=False,
        demonstrates=(
            "Presence of a language, never a level. Neither CV lists one, which "
            "is a gap in the document -- both are written in English."
        ),
    ),
)


@dataclass(frozen=True)
class SampleCriteria:
    """One set of screening criteria a visitor can try in demo mode.

    Demo mode replays recordings keyed by a hash of their input, so it can only
    answer for text it has a recording of. That is a real limit, and the honest
    way to live with it is to offer the texts that *do* work rather than to let
    someone discover the limit by pasting their own and getting an error.

    ``full_walkthrough`` says whether the recordings go beyond extraction. The
    two that do can be taken all the way to a ranked list; the others stop at a
    reviewed requirement set, which is still the whole of the feature they
    exist to show.
    """

    #: The fixture file stem, and the identifier the UI passes back.
    id: str
    label: str
    language: str
    demonstrates: str
    full_walkthrough: bool

    @property
    def text(self) -> str:
        return fixture_jd_text(self.id)


SAMPLE_CRITERIA: tuple[SampleCriteria, ...] = (
    SampleCriteria(
        id="jd_backend_engineer",
        label="Formal job description",
        language="English",
        demonstrates=(
            "A full job posting, the traditional input. Thirteen requirements across "
            "all five categories, including a compound skills sentence that has to be "
            "split into separate atomic requirements."
        ),
        full_walkthrough=True,
    ),
    SampleCriteria(
        id="criteria_indonesian",
        label="Informal criteria, Indonesian",
        language="Indonesian",
        demonstrates=(
            "Four criteria typed as one line, in lower case, with local abbreviations "
            "(s1, ipk, ptn/pts). No job description anywhere. The requirements come "
            "back in Indonesian, because the recruiter has to check them."
        ),
        full_walkthrough=True,
    ),
    SampleCriteria(
        id="criteria_mixed_language",
        label="Informal criteria, Indonesian and English mixed",
        language="Indonesian + English",
        demonstrates=(
            "One sentence mixing both languages, with a stated minimum "
            "('minimal 2 tahun') and a preference ('kalau pernah AI/ML lebih bagus') "
            "in the same breath. The two are told apart by the words used."
        ),
        full_walkthrough=True,
    ),
    SampleCriteria(
        id="criteria_english_informal",
        label="Informal criteria, English",
        language="English",
        demonstrates=(
            "Shorthand English with no bullet points: 'python + postgres, 2+ yrs, aws "
            "would be nice'. 'postgres' is kept as written, which is what sends that "
            "requirement through the alias matcher rather than the exact one."
        ),
        full_walkthrough=True,
    ),
)


@dataclass(frozen=True)
class SampleCv:
    """One synthetic CV, and what a reader is meant to learn from it."""

    filename: str
    label: str
    demonstrates: str

    @property
    def path(self) -> Path:
        return SAMPLE_DIR / self.filename

    def read(self) -> bytes:
        return self.path.read_bytes()


SAMPLE_CVS: tuple[SampleCv, ...] = (
    SampleCv(
        filename="alex-rivera-backend-engineer.pdf",
        label="Alex Rivera — strong match",
        demonstrates=(
            "A well-matched CV. Also prints a personal-details block (date of birth, "
            "gender, nationality, marital status, address) that the system must not "
            "extract or use."
        ),
    ),
    SampleCv(
        filename="jordan-blake-injected-instructions.pdf",
        label="Jordan Blake — contains injected instructions",
        demonstrates=(
            "A CV carrying text addressed to the model rather than to a human reader. "
            "The instructions are flagged and cannot become evidence."
        ),
    ),
    SampleCv(
        filename="scanned-no-text-layer.pdf",
        label="Scanned CV — no text layer",
        demonstrates=(
            "A PDF with no extractable text, standing in for a scan. There is no OCR, "
            "so it fails honestly rather than being scored as an empty CV."
        ),
    ),
)


@dataclass(frozen=True)
class DemoSamples:
    """What the UI needs to offer a demo without guessing at file contents."""

    job_title: str
    job_description: str
    criteria: tuple[SampleCriteria, ...]
    structured_criteria: tuple[DemoCriterion, ...]
    cvs: tuple[SampleCv, ...]


def get_samples() -> DemoSamples:
    """Every sample input a demo can be driven with."""
    return DemoSamples(
        job_title=DEMO_JOB_TITLE,
        job_description=fixture_jd_text(SAMPLE_JD_FIXTURE),
        criteria=SAMPLE_CRITERIA,
        structured_criteria=STRUCTURED_CRITERIA,
        cvs=SAMPLE_CVS,
    )


def get_criteria(criteria_id: str | None) -> SampleCriteria:
    """One free-text sample by id, or the formal job description by default."""
    if criteria_id is None:
        criteria_id = SAMPLE_JD_FIXTURE
    for item in SAMPLE_CRITERIA:
        if item.id == criteria_id:
            return item
    raise NotFoundError(
        f"There is no sample criteria set called {criteria_id!r}.",
        details={"available": [item.id for item in SAMPLE_CRITERIA]},
    )


def require_demo_mode(demo_mode: bool) -> None:
    """Refuse to manufacture data outside demo mode."""
    if not demo_mode:
        raise ConflictError(
            "Demo seeding is only available when the server runs with DEMO_MODE "
            "enabled. In live mode this endpoint would create candidate records "
            "that no one applied with."
        )


@dataclass(frozen=True)
class SeedResult:
    """What the seed produced, so the caller can report it honestly."""

    job: Job
    uploaded: int
    rejected: int
    screened: int
    failed: int


def seed_demo_job(
    db: Session,
    client: LlmClient,
    *,
    storage: DocumentStorage,
    demo_mode: bool,
    max_size_bytes: int,
    max_pages: int,
    max_files: int,
    criteria_id: str | None = None,
) -> SeedResult:
    """Build one complete demo job, start to finish.

    Runs the real pipeline in the real order: create the job, attach the sample
    description, extract requirements, confirm them through the gate, upload the
    sample CVs, then for each parsed candidate extract a profile, match it and
    score it.

    A candidate that cannot be taken all the way is left where it stopped rather
    than being dropped -- the scanned CV is meant to fail, and seeing it fail is
    part of the demonstration.
    """
    require_demo_mode(demo_mode)

    samples = get_samples()
    structured = criteria_id is None or criteria_id == STRUCTURED_CRITERIA_ID
    criteria = None if structured else get_criteria(criteria_id)
    missing = [cv.filename for cv in samples.cvs if not cv.path.is_file()]
    if missing:
        raise ConflictError(
            "The synthetic sample CVs are missing from this checkout.",
            details={"missing": missing},
        )

    if structured:
        job = jobs.create_job(db, title=STRUCTURED_DEMO_TITLE)
        for item in STRUCTURED_CRITERIA:
            requirements.add_structured_requirement(
                db, job.id, spec=item.spec, must_have=item.must_have
            )
    else:
        assert criteria is not None
        job = jobs.create_job(db, title=_title_for(criteria))
        jobs.set_description(
            db,
            job.id,
            raw_text=criteria.text,
            source_type=JdSourceType.PASTED,
            source_filename=None,
        )
        jd_extraction.extract_requirements(db, job.id, client)
    requirements.confirm_requirements(db, job.id)

    outcomes = candidates_service.upload_candidates(
        db,
        job.id,
        [(cv.filename, cv.read()) for cv in samples.cvs],
        storage=storage,
        max_size_bytes=max_size_bytes,
        max_pages=max_pages,
        max_files=max_files,
    )

    screened = 0
    for outcome in outcomes:
        if outcome.candidate_id is None or outcome.status is not CandidateStatus.PARSED:
            continue
        if _screen(db, outcome.candidate_id, client, structured=structured):
            screened += 1

    db.refresh(job)
    result = SeedResult(
        job=job,
        uploaded=sum(1 for outcome in outcomes if outcome.accepted),
        rejected=sum(1 for outcome in outcomes if not outcome.accepted),
        screened=screened,
        failed=sum(1 for outcome in outcomes if outcome.status is CandidateStatus.FAILED),
    )
    logger.info(
        "Seeded demo job %s: %s uploaded, %s screened, %s failed",
        job.id,
        result.uploaded,
        result.screened,
        result.failed,
    )
    return result


def _title_for(criteria: SampleCriteria) -> str:
    """A job title that names the sample, so several seeds stay distinguishable."""
    if criteria.id == SAMPLE_JD_FIXTURE:
        return DEMO_JOB_TITLE
    return f"[Demo] {criteria.label}"


def _screen(db: Session, candidate_id: uuid.UUID, client: LlmClient, *, structured: bool) -> bool:
    """Profile, match and score one candidate. False when it could not finish.

    A candidate the fixtures do not cover stops here rather than taking the seed
    down with it: the rest of the demo is still worth showing, and the candidate
    is left in the state it actually reached.

    The structured demo skips profile extraction entirely, because nothing
    downstream reads a profile: structured criteria are matched against the CV
    text itself. That is not an optimisation -- it is the demonstration. The
    whole walkthrough completes with no model call, which is why it works with
    no API key, no recorded fixture and no local model running.
    """
    try:
        if not structured:
            profile_extraction.extract_profile(db, candidate_id, client)
        matching.run_matching(db, candidate_id, client)
        scoring.score_candidate(db, candidate_id)
    except (ExtractionFailedError, LlmUnavailableError, ConflictError) as exc:
        logger.warning("Demo seeding could not screen candidate %s: %s", candidate_id, exc)
        return False
    return True


__all__ = [
    "DEMO_JOB_TITLE",
    "SAMPLE_CRITERIA",
    "STRUCTURED_CRITERIA",
    "STRUCTURED_CRITERIA_ID",
    "STRUCTURED_DEMO_TITLE",
    "SAMPLE_CVS",
    "SAMPLE_DIR",
    "DemoCriterion",
    "DemoSamples",
    "SampleCriteria",
    "SampleCv",
    "SeedResult",
    "get_criteria",
    "get_samples",
    "require_demo_mode",
    "seed_demo_job",
]
