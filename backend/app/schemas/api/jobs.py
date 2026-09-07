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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import JdSourceType, RequirementCategory, RequirementOrigin
from app.core.text import strip_control_characters

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
    proposed_text: str | None
    proposed_category: RequirementCategory | None
    proposed_must_have: bool | None
    created_at: datetime
    updated_at: datetime


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
