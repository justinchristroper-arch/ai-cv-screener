"""The contract the model must satisfy when extracting a candidate profile.

Three rules are expressed structurally here rather than as prompt wording,
because a prompt instruction is not a control anyone can verify (ADR-0003):

* **No sensitive attribute has a field.** There is nowhere to put an age, a
  date of birth, a gender, a nationality, a photo, a marital status, an
  address, a phone number or an email -- and ``extra="forbid"`` means a reply
  that invents one is rejected in full rather than having the surplus dropped.
* **No judgement has a field.** There is no score, no rating, no rank, no
  recommendation. The model reports what the document says; what that is worth
  is decided downstream, in code (ADR-0001).
* **Every item must quote the document.** ``evidence_quote`` is required on
  every skill, role, qualification and project. The quote is verified against
  the parsed text by ``services/evidence.py``; the model is never asked for
  character offsets, because searching for its quote in our own text is both
  easier and self-verifying (docs/architecture.md section 6).

Dates arrive as partial strings (``2021``, ``2021-03``, ``2021-03-15``) because
that is what a CV actually supports. Turning them into a DATE plus a
``date_precision`` is the application's job, not the model's.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.llm.json_schema import provider_json_schema

#: Bounds against a degenerate reply -- one giant blob, or hundreds of
#: fragments. Not opinions about how long a real CV ought to be.
MAX_SKILLS = 80
MAX_EXPERIENCE = 30
MAX_EDUCATION = 15
MAX_PROJECTS = 25
MAX_TECHNOLOGIES = 25

#: A quote shorter than this is a fragment rather than a citation: one or two
#: characters cannot identify a passage. It matches
#: `matching.MIN_SEARCHABLE_TOKEN_LENGTH` and the semantic-matching contract.
#:
#: It used to be eight, on the reasoning that a longer quote "cannot verify
#: against an incidental occurrence". That reasoning was sound and the mechanism
#: was wrong: it also rejected legitimate one-word quotations such as "Python"
#: on a skills line. Guarding against an incidental match is now done by
#: `services/evidence.py`, which requires a short quote to sit on token
#: boundaries in the document -- a fact about position rather than a guess from
#: length.
MIN_QUOTE_LENGTH = 3
MAX_QUOTE_LENGTH = 400

MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 1000

MIN_COMPLETION_YEAR = 1900
MAX_COMPLETION_YEAR = 2100

#: ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``, and nothing else.
_PARTIAL_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def _non_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


def _optional_non_blank(value: str | None) -> str | None:
    """Treat a whitespace-only optional string as absent rather than present."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _valid_partial_date(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    match = _PARTIAL_DATE.match(stripped)
    if match is None:
        raise ValueError("must be YYYY, YYYY-MM or YYYY-MM-DD, or null when the CV does not say")
    _, month, day = match.groups()
    if month is not None and not 1 <= int(month) <= 12:
        raise ValueError("month must be between 01 and 12")
    if day is not None and not 1 <= int(day) <= 31:
        raise ValueError("day must be between 01 and 31")
    return stripped


def partial_date_sort_key(value: str) -> tuple[int, int, int]:
    """Comparable key for a validated partial date, missing parts padded with 0."""
    match = _PARTIAL_DATE.match(value)
    if match is None:  # pragma: no cover - callers pass validated values
        raise ValueError(f"not a partial date: {value!r}")
    year, month, day = match.groups()
    return (int(year), int(month or 0), int(day or 0))


class _EvidencedItem(BaseModel):
    """Anything the model claims must come with the words it read."""

    model_config = ConfigDict(extra="forbid")

    evidence_quote: str = Field(
        min_length=MIN_QUOTE_LENGTH,
        max_length=MAX_QUOTE_LENGTH,
        description=(
            "A verbatim sentence or line copied from the CV that supports this item. "
            "Copy it exactly; do not paraphrase, join or shorten it."
        ),
    )

    _validate_quote = field_validator("evidence_quote")(_non_blank)


class ExtractedSkill(_EvidencedItem):
    """A skill the CV claims, named as the CV names it."""

    name: str = Field(
        min_length=1,
        max_length=MAX_NAME_LENGTH,
        description="The skill exactly as written in the CV.",
    )

    _validate_name = field_validator("name")(_non_blank)


class ExtractedExperience(_EvidencedItem):
    """A role the CV describes, with whatever date precision it actually gives."""

    role_title: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    organization: str | None = Field(max_length=MAX_NAME_LENGTH)
    start_date: str | None = Field(
        description="YYYY, YYYY-MM or YYYY-MM-DD. Null when the CV does not say."
    )
    end_date: str | None = Field(
        description="YYYY, YYYY-MM or YYYY-MM-DD. Null when ongoing or not stated."
    )
    is_current: bool = Field(description="True only when the CV says the role is ongoing.")
    description: str | None = Field(max_length=MAX_DESCRIPTION_LENGTH)

    _validate_role = field_validator("role_title")(_non_blank)
    _validate_optional = field_validator("organization", "description")(_optional_non_blank)
    _validate_dates = field_validator("start_date", "end_date")(_valid_partial_date)

    @model_validator(mode="after")
    def _end_is_not_before_start(self) -> ExtractedExperience:
        """A role that ends before it starts is a misread, not a date format."""
        if not (self.start_date and self.end_date):
            return self
        if partial_date_sort_key(self.end_date) < partial_date_sort_key(self.start_date):
            raise ValueError("end_date is earlier than start_date")
        return self


class ExtractedEducation(_EvidencedItem):
    """A qualification the CV claims."""

    degree: str | None = Field(max_length=MAX_NAME_LENGTH)
    field_of_study: str | None = Field(max_length=MAX_NAME_LENGTH)
    institution: str | None = Field(max_length=MAX_NAME_LENGTH)
    completion_year: int | None = Field(ge=MIN_COMPLETION_YEAR, le=MAX_COMPLETION_YEAR)

    _validate_optional = field_validator("degree", "field_of_study", "institution")(
        _optional_non_blank
    )

    @model_validator(mode="after")
    def _says_something(self) -> ExtractedEducation:
        if not (self.degree or self.field_of_study or self.institution):
            raise ValueError("needs at least one of degree, field_of_study or institution")
        return self


class ExtractedProject(_EvidencedItem):
    """Practical work the CV describes."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str | None = Field(max_length=MAX_DESCRIPTION_LENGTH)
    technologies: list[str] = Field(
        max_length=MAX_TECHNOLOGIES,
        description="Technologies the CV names for this project. Empty list when it names none.",
    )

    _validate_name = field_validator("name")(_non_blank)
    _validate_description = field_validator("description")(_optional_non_blank)

    @field_validator("technologies")
    @classmethod
    def _technologies_are_named(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) != len(value):
            raise ValueError("technology names must not be blank")
        return cleaned


class ProfileExtractionOutput(BaseModel):
    """Everything one CV yields.

    ``display_name`` is the one field here that is not job-relevant content. It
    is written to ``candidate.display_name`` -- the display entity -- and never
    to the profile, so the matcher is structurally incapable of reading it
    (docs/data-model.md section 5).
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(
        max_length=MAX_NAME_LENGTH,
        description=(
            "The candidate's name as printed on the CV, so a recruiter can tell "
            "documents apart. Null when the CV does not print one."
        ),
    )
    skills: list[ExtractedSkill] = Field(max_length=MAX_SKILLS)
    experience: list[ExtractedExperience] = Field(max_length=MAX_EXPERIENCE)
    education: list[ExtractedEducation] = Field(max_length=MAX_EDUCATION)
    projects: list[ExtractedProject] = Field(max_length=MAX_PROJECTS)

    _validate_display_name = field_validator("display_name")(_optional_non_blank)

    @model_validator(mode="after")
    def _profile_is_not_empty(self) -> ProfileExtractionOutput:
        """A document with extractable text always says *something*.

        An entirely empty profile is a failed read, not a candidate with no
        qualifications -- and stored, it would look exactly like the latter.
        """
        if not (self.skills or self.experience or self.education or self.projects):
            raise ValueError(
                "the profile contains no items at all; a CV with readable text "
                "must yield at least one skill, role, qualification or project"
            )
        return self

    @property
    def item_count(self) -> int:
        """How many evidenced items this reply carries, across all four lists."""
        return len(self.skills) + len(self.experience) + len(self.education) + len(self.projects)


def profile_extraction_json_schema() -> dict[str, Any]:
    """JSON Schema for the provider's structured-output constraint."""
    return provider_json_schema(ProfileExtractionOutput)
