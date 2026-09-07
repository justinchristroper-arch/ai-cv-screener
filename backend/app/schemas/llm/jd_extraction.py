"""The contract the model must satisfy when extracting job requirements.

This is a `schemas/llm` family member, kept separate from `schemas/api` because
the two change for different reasons: this one changes when a prompt is revised
(and that revision has to be versioned and correlated with stored output),
docs/architecture.md section 2.2.

The model's reply is re-validated against this locally on every call, in both
live and demo mode. A provider-side schema constraint is not a substitute for
our own check (architecture section 4.3).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import RequirementCategory
from app.schemas.llm.json_schema import provider_json_schema

#: Bounds on what a single extraction may return. These are guards against a
#: degenerate reply (one giant blob, or hundreds of fragments), not opinions
#: about how many requirements a real job description ought to have.
MAX_REQUIREMENTS = 60
MAX_REQUIREMENT_TEXT_LENGTH = 300
MIN_REQUIREMENT_TEXT_LENGTH = 3


class ExtractedRequirement(BaseModel):
    """One atomic, independently testable criterion, as proposed by the model."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=MIN_REQUIREMENT_TEXT_LENGTH,
        max_length=MAX_REQUIREMENT_TEXT_LENGTH,
        description="One requirement, phrased so it can be evaluated on its own.",
    )
    category: RequirementCategory
    must_have: bool = Field(
        description="True when the job description states this as a hard requirement."
    )

    @field_validator("text")
    @classmethod
    def _text_is_not_blank(cls, value: str) -> str:
        """`min_length` counts whitespace; a string of spaces must still fail."""
        stripped = value.strip()
        if len(stripped) < MIN_REQUIREMENT_TEXT_LENGTH:
            raise ValueError("requirement text must not be blank")
        return stripped


class RequirementExtractionOutput(BaseModel):
    """The whole reply: the set of requirements found in one job description."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[ExtractedRequirement] = Field(
        min_length=1,
        max_length=MAX_REQUIREMENTS,
        description="Every requirement found, one entry per atomic criterion.",
    )


def requirement_extraction_json_schema() -> dict[str, Any]:
    """JSON Schema for the provider's structured-output constraint.

    Derived from the Pydantic model rather than hand-written, so the constraint
    sent to the provider and the constraint enforced locally cannot drift.
    The inlining itself lives in `schemas/llm/json_schema.py`, shared with the
    profile-extraction and semantic-matching contracts.
    """
    return provider_json_schema(RequirementExtractionOutput)
