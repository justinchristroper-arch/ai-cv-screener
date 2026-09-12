"""HTTP request and response contracts for jobs and requirements.

Kept separate from `schemas/llm` on purpose: these change when the frontend
needs different data, those change when a prompt is revised
(docs/architecture.md section 2.2).

Every field a client can send is bounded and typed here, so an invalid category
or an out-of-range weight is rejected at the boundary with a 422 rather than
reaching a service or the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.core.enums import (
    JdSourceType,
    RequirementCategory,
    RequirementOrigin,
    RequirementSpecType,
)
from app.core.protected_attributes import scan as scan_for_protected_attributes
from app.core.text import strip_control_characters
from app.services import structured_match

MAX_TITLE_LENGTH = 200
MAX_JD_LENGTH = 100_000
MAX_REQUIREMENT_TEXT_LENGTH = 300
MAX_WEIGHT = Decimal("100")


def _non_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


def _pasteable(value: str) -> str:
    """Accept text a person pasted, minus characters that cannot be stored.

    A job description arrives from a clipboard, and a clipboard carries whatever
    the source document had in it — including NUL bytes out of some PDF viewers.
    PostgreSQL refuses NUL in a text column, so before this the paste surfaced as
    an opaque 500 with an error id, which tells a recruiter nothing and looks
    like the product is broken.

    Stripped rather than rejected: the characters are invisible, so removing
    them changes nothing the person can see, whereas refusing the paste would
    block a description that is otherwise entirely valid. Everything visible is
    preserved byte for byte, and the blank check still applies afterwards.
    """
    return _non_blank(strip_control_characters(value))


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)

    _validate_title = field_validator("title")(_non_blank)


class JobResponse(BaseModel):
    """A job plus the derived facts the UI needs to know what to offer next."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    #: NULL until a human confirms. This is the gate (ADR-0004).
    requirements_confirmed_at: datetime | None
    has_description: bool
    requirement_count: int
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# Job description
# --------------------------------------------------------------------------


class JobDescriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1, max_length=MAX_JD_LENGTH)
    source_type: JdSourceType = JdSourceType.PASTED
    source_filename: str | None = Field(default=None, max_length=255)

    _validate_text = field_validator("raw_text")(_pasteable)


class JobDescriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    source_type: JdSourceType
    source_filename: str | None
    raw_text: str
    text_sha256: str
    created_at: datetime

    injection_flags: list[dict] = Field(
        default_factory=list,
        description=(
            "Passages in the description that read as instructions to the system rather "
            "than as a statement of what the role needs. Flagged and surfaced, never "
            "removed and never acted on: the text is still used as the description, and "
            "the recruiter still reviews and confirms every requirement before anything "
            "is screened against it."
        ),
    )
    injection_flag_count: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def protected_attribute_flags(self) -> list[dict]:
        """Protected personal characteristics the criteria text itself names.

        The same scan the requirement rows get, run one step earlier so the
        recruiter meets the problem while looking at what they wrote rather than
        at a refused confirmation three steps later. Nothing is blocked here:
        the text is stored and used exactly as typed, and only confirmation
        actually refuses.
        """
        return [flag.as_dict() for flag in scan_for_protected_attributes(self.raw_text)]


# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------


class RequirementResponse(BaseModel):
    """One requirement, including what the model originally proposed.

    `proposed_*` is `null` for hand-added requirements and, for extracted ones,
    is what the model said before any human edit — the correction signal
    described in ADR-0004.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    text: str
    category: RequirementCategory
    must_have: bool
    weight: Decimal
    display_order: int
    origin: RequirementOrigin

    #: Present on structured criteria (ADR-0012); null on legacy free-text rows.
    spec_type: RequirementSpecType | None = None
    subject: str | None = None
    threshold_value: Decimal | None = None
    threshold_scale: Decimal | None = None

    proposed_text: str | None
    proposed_category: RequirementCategory | None
    proposed_must_have: bool | None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def protected_attribute_flags(self) -> list[dict]:
        """Protected personal characteristics this requirement's text names.

        Age, gender, marital status, religion, ethnicity, nationality,
        appearance or health. A requirement set containing any of these cannot
        be confirmed (`services/requirements.confirm_requirements`), so no
        candidate is ever screened against one — this field is what lets the UI
        say so on the row before the recruiter reaches that wall.

        Derived from the text on every read rather than stored: the text is the
        entire input, so there is nothing to fall out of date, and a pattern
        added later covers requirements written before it existed.
        """
        return [flag.as_dict() for flag in scan_for_protected_attributes(self.text)]


class StructuredRequirementRequest(BaseModel):
    """One of the six structured criteria (ADR-0012).

    Deliberately one flat shape with a discriminating `spec_type` rather than
    six request bodies: the recruiter is filling one row of a form, and the
    per-type rules below are what stop an ill-formed row from reaching the
    database, where the same rules exist again as a CHECK constraint.
    """

    model_config = ConfigDict(extra="forbid")

    spec_type: RequirementSpecType
    must_have: bool

    #: The thing named: a degree level, a skill, or a language.
    subject: str | None = Field(default=None, max_length=100)

    #: The bar: months for a duration, a grade for GPA.
    threshold_value: Decimal | None = Field(default=None, ge=0, le=Decimal("1200"))

    #: GPA only. Stated by the recruiter because the engine never assumes one.
    threshold_scale: Decimal | None = Field(default=None, gt=0, le=Decimal("10"))

    #: Omitted means "use the default for this must-have flag" (3 or 1).
    weight: Decimal | None = Field(default=None, ge=0, le=MAX_WEIGHT)

    @model_validator(mode="after")
    def _shape_matches_the_type(self) -> StructuredRequirementRequest:
        """Refuse a row the chosen type cannot carry.

        The rules come from `structured_match.SPEC_SHAPES` rather than being
        restated here, so the form the interface builds and the body this
        accepts cannot drift apart. The database repeats them a third time as a
        CHECK constraint — belt and braces, because a malformed criterion is
        stored for as long as the job exists.
        """
        shape = structured_match.SPEC_SHAPES[self.spec_type.value]
        subject = (self.subject or "").strip()

        # The three fields are checked independently against the shape rather
        # than as one branching decision. They used to be treated as mutually
        # exclusive -- a type either named a thing or set a bar -- which was
        # true of the original six and stopped being true the moment
        # EXPERIENCE_IN_FIELD needed both.
        if shape.subject_source:
            if not subject:
                raise ValueError(f"{self.spec_type.value} needs a subject")
        elif self.subject is not None:
            raise ValueError(f"{self.spec_type.value} takes no subject")

        if shape.threshold_unit is None:
            if self.threshold_value is not None:
                raise ValueError(f"{self.spec_type.value} takes no numeric threshold")
        elif shape.threshold_required and self.threshold_value is None:
            unit = "a minimum grade" if shape.threshold_unit == "grade" else "a duration in months"
            raise ValueError(f"{self.spec_type.value} needs {unit}")

        if shape.needs_scale:
            if self.threshold_scale is None:
                raise ValueError(f"{self.spec_type.value} needs the scale the minimum is out of")
            if self.threshold_value is not None and self.threshold_value > self.threshold_scale:
                raise ValueError("the minimum grade cannot exceed its scale")
        elif self.threshold_scale is not None:
            raise ValueError(f"{self.spec_type.value} takes no scale")

        return self


class RequirementCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_REQUIREMENT_TEXT_LENGTH)
    category: RequirementCategory
    must_have: bool
    #: Omitted means "use the default for this must-have flag" (3 or 1).
    weight: Decimal | None = Field(default=None, ge=0, le=MAX_WEIGHT)

    _validate_text = field_validator("text")(_non_blank)


class RequirementUpdateRequest(BaseModel):
    """A partial edit. Every field optional; omitted fields are left alone.

    Which of these the server will actually accept depends on whether the job
    is confirmed — `text` and `category` are frozen by confirmation, `weight`
    and `must_have` never are. That rule lives in the service, not here.
    """

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=MAX_REQUIREMENT_TEXT_LENGTH)
    category: RequirementCategory | None = None
    must_have: bool | None = None
    weight: Decimal | None = Field(default=None, ge=0, le=MAX_WEIGHT)

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else _non_blank(value)


class RequirementListResponse(BaseModel):
    """The requirement set, with the gate's state attached.

    `requirements_confirmed_at` travels with the list so a client never has to
    infer confirmation state from a separate call and get it wrong.
    """

    job_id: uuid.UUID
    requirements_confirmed_at: datetime | None
    requirements: list[RequirementResponse]


class ExtractionResponse(BaseModel):
    """The outcome of one extraction run."""

    job_id: uuid.UUID
    #: Which audit row this came from, so a result is traceable to its call.
    llm_call_id: uuid.UUID
    #: LIVE or FIXTURE. Makes it unambiguous whether a demo produced this.
    source: str
    #: 1, or 2 when the first reply failed validation and the retry succeeded.
    attempts: int
    requirements_confirmed_at: datetime | None
    requirements: list[RequirementResponse]
