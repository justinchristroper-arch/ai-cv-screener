"""HTTP contract for the synthetic demo data.

Everything these models carry is invented. The point of the endpoints behind
them is that the product can be shown end to end with no API key, no cost and no
real applicant's CV — so the responses say plainly that the data is synthetic
rather than leaving a reader to infer it.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.enums import RequirementSpecType


class SampleCvResponse(BaseModel):
    """One synthetic CV bundled with the project."""

    filename: str
    label: str
    demonstrates: str = Field(description="What a reader is meant to learn from this file.")


class StructuredCriterionResponse(BaseModel):
    """One criterion the structured demo screens with."""

    spec_type: RequirementSpecType
    text: str = Field(description="The criterion as it is displayed on the job.")
    must_have: bool
    demonstrates: str = Field(description="What a reader is meant to learn from this one.")


class SampleCriteriaResponse(BaseModel):
    """One set of screening criteria a visitor can try."""

    id: str = Field(description="Stable identifier; also the recording's file stem.")
    label: str
    language: str
    demonstrates: str = Field(description="What a reader is meant to learn from this one.")
    text: str = Field(description="Exactly the text to paste, byte for byte.")
    full_walkthrough: bool = Field(
        description=(
            "Whether the recordings go past extraction. When true this text can be taken "
            "all the way to a ranked list with no API key."
        )
    )


class DemoSamplesResponse(BaseModel):
    """The sample inputs a demo is driven with."""

    demo_mode: bool = Field(
        description=(
            "Whether this server serves model calls from recorded fixtures. Seeding "
            "is refused when false."
        )
    )
    job_title: str
    job_description: str = Field(
        description=(
            "The synthetic job description, read from the recorded extraction fixture "
            "so the two cannot drift apart. Paste it to walk the workflow by hand."
        )
    )
    structured_criteria_id: str = Field(
        description=(
            "Pass this as `criteria_id` to seed the structured demo. It is also "
            "the default, so omitting `criteria_id` seeds the same thing."
        )
    )
    structured_criteria: list[StructuredCriterionResponse] = Field(
        default_factory=list,
        description=(
            "The six criteria the structured demo screens with. Unlike the briefs "
            "below they need no recording of any kind: the structured engine reads "
            "the CV directly, so this walkthrough runs with no model at all."
        ),
    )
    criteria: list[SampleCriteriaResponse] = Field(
        default_factory=list,
        description=(
            "The free-text briefs this build has recordings for — a formal job "
            "description and three informal ones, one Indonesian, one mixed and one "
            "English. These drive the legacy model-read path (ADR-0009, superseded), "
            "which can only analyse text it has a recording of, so they are offered "
            "rather than left to be discovered."
        ),
    )
    cvs: list[SampleCvResponse] = Field(
        description="The synthetic CVs, which live in `data/sample/` in the repository."
    )


class DemoSeedResponse(BaseModel):
    """What a seeded demo job contains."""

    job_id: uuid.UUID
    job_title: str = Field(description="Prefixed [Demo] so synthetic data is labelled in the UI.")
    uploaded: int
    rejected: int
    screened: int = Field(description="Candidates taken all the way through to a score.")
    failed: int = Field(
        description="Candidates whose processing failed, with a reason. Never hidden."
    )
