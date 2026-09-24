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
from typing import Any, ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.text import find_whole_line
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

#: The validation-context key under which the extraction service supplies the
#: parsed document text. Only with it can a skill's quote shorter than
#: `MIN_QUOTE_LENGTH` be accepted -- see `_EvidencedItem`. Validation without it
#: (tests, schema generation, any other caller) keeps the plain minimum.
DOCUMENT_TEXT_CONTEXT = "document_text"

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


def _short_quote_error(quote: str) -> ValueError:
    # Two remedies, because a CV can print a one-letter skill alone on its
    # line, where quoting the whole line cannot reach the minimum. Omitting the
    # item is the prompt's own rule for anything that cannot be quoted --
    # unlike semantic matching, which must answer every requirement and so has
    # no such option.
    return ValueError(
        f"evidence_quote is too short to identify a passage "
        f"({len(quote)} characters, minimum {MIN_QUOTE_LENGTH}). "
        f"Quote the whole line the term appears on instead, or leave the item "
        f"out if that line is shorter than {MIN_QUOTE_LENGTH} characters."
    )


class _EvidencedItem(BaseModel):
    """Anything the model claims must come with the words it read.

    **One narrow exception to the minimum.** A CV can print a one-letter skill
    ("C", "R") alone on its line, and then the correct quote is that one
    character -- there is no longer line to quote. Such a quote is accepted
    only when *all* of these hold, and is refused with the ordinary message
    otherwise:

    * the item is a skill (`allows_standalone_short_quote`), and the quote is
      the skill's own name (`ExtractedSkill`);
    * it contains a letter, so a page number or a bullet can never qualify;
    * the parsed document was supplied as validation context, and the quote is
      an **entire line** of it (`find_whole_line`). A token boundary is not
      enough: "R" is a token of "R&D", and "C" of "C++" and "C#".

    Matching is unaffected: it never searches for a name this short
    (`matching.MIN_SEARCHABLE_TOKEN_LENGTH`).
    """

    model_config = ConfigDict(extra="forbid")

    #: Whether this kind of item may carry the one-line short quote above.
    allows_standalone_short_quote: ClassVar[bool] = False

    # The bounds are enforced by `_quote_identifies_a_passage` rather than by
    # `min_length`/`max_length`, whose generic message ("String should have at
    # least 3 characters") named a bound without saying what to do: a model
    # whose quote was correct repeated it and burned the one retry. They are
    # still advertised to the provider, so the schema it receives is unchanged.
    evidence_quote: str = Field(
        description=(
            "A verbatim sentence or line copied from the CV that supports this item. "
            "Copy it exactly; do not paraphrase, join or shorten it."
        ),
        json_schema_extra={"minLength": MIN_QUOTE_LENGTH, "maxLength": MAX_QUOTE_LENGTH},
    )

    @field_validator("evidence_quote")
    @classmethod
    def _quote_identifies_a_passage(cls, value: str, info: ValidationInfo) -> str:
        """Bound the quote after trimming, in words a retry can act on.

        Matches the semantic-matching contract (`schemas/llm/semantic_match.py`).
        Trimming first means padding cannot carry a fragment past the minimum.
        """
        quote = _non_blank(value)
        if len(quote) < MIN_QUOTE_LENGTH and not cls._stands_as_its_own_line(quote, info):
            raise _short_quote_error(quote)
        if len(quote) > MAX_QUOTE_LENGTH:
            raise ValueError(
                f"evidence_quote is longer than {MAX_QUOTE_LENGTH} characters. "
                f"Quote only the sentence or line that supports this item."
            )
        return quote

    @classmethod
    def _stands_as_its_own_line(cls, quote: str, info: ValidationInfo) -> bool:
        """The document-dependent half of the exception; see the class docstring."""
        if not cls.allows_standalone_short_quote:
            return False
        document = (info.context or {}).get(DOCUMENT_TEXT_CONTEXT)
        if not isinstance(document, str):
            return False
        return any(ch.isalpha() for ch in quote) and find_whole_line(quote, document) != -1


class ExtractedSkill(_EvidencedItem):
    """A skill the CV claims, named as the CV names it."""

    allows_standalone_short_quote: ClassVar[bool] = True

    name: str = Field(
        min_length=1,
        max_length=MAX_NAME_LENGTH,
        description="The skill exactly as written in the CV.",
    )

    _validate_name = field_validator("name")(_non_blank)

    @model_validator(mode="after")
    def _a_short_quote_is_the_skill_itself(self) -> ExtractedSkill:
        """The name half of the exception. It runs after the fields because
        `name` is validated after the inherited `evidence_quote`."""
        if len(self.evidence_quote) < MIN_QUOTE_LENGTH and self.evidence_quote != self.name:
            raise _short_quote_error(self.evidence_quote)
        return self


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
