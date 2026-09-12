"""Response contracts for the supported screening vocabulary (ADR-0012).

Read-only and derived entirely from `services.structured_match` and
`services.skill_taxonomy`. Nothing here is stored; it is the closed list of
things the screener can be asked, published so the interface can offer exactly
that and say plainly that anything else is unsupported.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.enums import RequirementSpecType


class CriterionTypeResponse(BaseModel):
    """One of the six criterion types, and the fields it collects.

    The form is built from this rather than hard-coded, so a type whose shape
    changes in `SPEC_SHAPES` changes the interface with it.
    """

    spec_type: RequirementSpecType
    label: str = Field(description="What the recruiter sees in the add-criterion menu")
    subject_source: Literal["degrees", "skills", "languages", "certifications"] | None = Field(
        description=(
            "Which list in this response the subject must be chosen from. Null "
            "when the type takes no subject."
        )
    )
    threshold_unit: Literal["months", "grade"] | None = Field(
        description="What the number means. Null when the type takes no number."
    )
    threshold_required: bool = Field(description="Whether that number must be supplied")
    needs_scale: bool = Field(
        description=(
            "Whether the recruiter must also state the scale the threshold is out "
            "of. True for GPA only: the engine never assumes a 4.0 or 5.0 scale."
        )
    )


class SkillOption(BaseModel):
    """A skill that can be screened for, with its family for grouping."""

    name: str
    family: str = Field(
        description=(
            "A display grouping — 'language', 'datastore', 'ml' and so on. Never "
            "used to credit one skill for another."
        )
    )


class SkillGroupResponse(BaseModel):
    """A named preset that expands to several ordinary skill criteria.

    A convenience for adding six rows in one click, never a criterion type of
    its own: screening for "AI/ML" as a single thing would mean inventing a
    judgement the CV does not contain.
    """

    name: str
    skills: list[str]


class DegreeOption(BaseModel):
    """A degree level the recruiter can require.

    `rank` is what the comparison actually uses, and equal ranks are the same
    level under different naming conventions — S1 and Bachelor are one level.
    """

    name: str
    rank: int


class CriteriaVocabularyResponse(BaseModel):
    """Everything the criteria builder is allowed to offer."""

    spec_types: list[CriterionTypeResponse]
    skills: list[SkillOption]
    languages: list[str] = Field(
        description="Detected by presence only. This screener never infers a proficiency level."
    )
    degrees: list[DegreeOption]
    certifications: list[str] = Field(
        default_factory=list,
        description=(
            "Credentials detected by presence only. This screener never compares a "
            "certificate's date, ranks one level above another, or treats one "
            "certificate as covering another."
        ),
    )
    skill_groups: list[SkillGroupResponse]
