"""HTTP contracts for candidate profiles and requirement matching.

Kept in `schemas/api` and separate from `schemas/llm` for the usual reason
(docs/architecture.md section 2.2): these change when the UI needs different
data, those change when a prompt is revised.

Two things are deliberately absent from every model here, because they do not
exist yet and inventing a placeholder for them would misrepresent the system:
**no score, and no ranking.** A verdict, its reason and its evidence are the
whole of what this milestone produces.

Evidence is always returned with its `verification_status`, so a client never
has to guess whether a quote was actually found in the document. A quote that
was not found is still shown — flagged — rather than hidden.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    DatePrecision,
    EvidenceVerification,
    MatchMethod,
    MatchVerdict,
    RequirementCategory,
)


class EvidenceResponse(BaseModel):
    """One quoted passage and where it was found.

    `start_char`/`end_char` index into the text served by
    `GET /api/candidates/{id}/text`, so a UI can highlight the exact passage.
    Both are null when the quote could not be located, which is precisely what
    `verification_status = UNVERIFIED` means.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    quoted_text: str
    start_char: int | None
    end_char: int | None
    page_number: int | None
    verification_status: EvidenceVerification
    normalization_version: str


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


class ProfileSkillResponse(BaseModel):
    id: uuid.UUID
    raw_name: str = Field(description="The skill as written in the CV.")
    normalized_name: str = Field(
        description="Computed by the application, not the model. What the matcher indexes."
    )
    evidence: EvidenceResponse | None = None


class ProfileExperienceResponse(BaseModel):
    id: uuid.UUID
    role_title: str
    organization: str | None
    start_date: date | None
    end_date: date | None
    date_precision: DatePrecision = Field(
        description=(
            "How precise the CV's own dates were. A YEAR-precision role is not "
            "evidence of a month-precise duration, and the matcher says so."
        )
    )
    is_current: bool
    description: str | None
    evidence: EvidenceResponse | None = None


class ProfileEducationResponse(BaseModel):
    id: uuid.UUID
    degree: str | None
    field_of_study: str | None
    institution: str | None
    completion_year: int | None
    evidence: EvidenceResponse | None = None


class ProfileProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    technologies: list[str] = Field(default_factory=list)
    evidence: EvidenceResponse | None = None


class EvidenceSummary(BaseModel):
    """How much of this profile is backed by text that was actually located."""

    items: int
    with_evidence: int
    verified: int
    unverified: int


class CandidateProfileResponse(BaseModel):
    """A candidate's extracted profile.

    There is no name, age, gender, nationality, photo, address, phone or email
    field here, and there is no column for one either — sensitive attributes are
    excluded from the scoring input by construction (ADR-0003). The candidate's
    display name lives on the candidate resource, not on the profile.
    """

    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    parsed_document_id: uuid.UUID
    prompt_version: str
    llm_call_id: uuid.UUID | None
    created_at: datetime

    skills: list[ProfileSkillResponse]
    experience: list[ProfileExperienceResponse]
    education: list[ProfileEducationResponse]
    projects: list[ProfileProjectResponse]

    evidence_summary: EvidenceSummary


class ProfileExtractionResponse(BaseModel):
    """The result of running extraction, plus the profile it produced."""

    candidate_id: uuid.UUID
    source: str | None = Field(
        default=None,
        description="LIVE or FIXTURE. Null when no model call was made.",
    )
    attempts: int = Field(description="Model calls made. Zero on a cache hit.")
    cache_hit: bool = Field(
        description=(
            "True when the profile already existed, or when an identical document "
            "had already been extracted. No model call was made."
        )
    )
    profile: CandidateProfileResponse


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


class MatchResultResponse(BaseModel):
    """One requirement, and what this CV evidences for it."""

    requirement_id: uuid.UUID
    requirement_text: str
    category: RequirementCategory
    must_have: bool = Field(
        description="The recruiter's flag, shown for context. It does not affect the verdict."
    )
    display_order: int

    verdict: MatchVerdict = Field(
        description=(
            "NO_EVIDENCE means this document contains no verified evidence for the "
            "requirement. It is never a claim that the candidate lacks the skill."
        )
    )
    decided_by: MatchMethod = Field(
        description="Which mechanism settled this pair, deterministic or the model."
    )
    reason: str
    evidence: EvidenceResponse | None = None

    raw_verdict: MatchVerdict | None = Field(
        default=None, description="What the model proposed, before any downgrade."
    )
    downgraded: bool = Field(
        description=(
            "True when a proposed positive verdict was refused because its evidence "
            "could not be verified, or read as instruction text rather than CV content."
        )
    )
    llm_call_id: uuid.UUID | None = None


class MatchSummary(BaseModel):
    """Counts only. No score, no band, no rank — those are a later milestone."""

    total: int
    matched: int
    partial: int
    no_evidence: int
    downgraded: int
    decided_deterministically: int
    decided_by_model: int


class MatchResultsResponse(BaseModel):
    """Every verdict for one candidate, in requirement order."""

    candidate_id: uuid.UUID
    job_id: uuid.UUID
    requirements_confirmed_at: datetime | None
    results: list[MatchResultResponse]
    summary: MatchSummary


class MatchingRunResponse(MatchResultsResponse):
    """The result of running matching, plus how it was decided."""

    source: str | None = Field(
        default=None,
        description="LIVE or FIXTURE for the semantic-matching call. Null when none was needed.",
    )
    attempts: int = Field(
        description="Model calls made for the undecided pairs. Zero when code settled everything."
    )
