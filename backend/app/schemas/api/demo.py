"""HTTP contract for the synthetic demo data.

Everything these models carry is invented. The point of the endpoints behind
them is that the product can be shown end to end with no API key, no cost and no
real applicant's CV — so the responses say plainly that the data is synthetic
rather than leaving a reader to infer it.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class SampleCvResponse(BaseModel):
    """One synthetic CV bundled with the project."""

    filename: str
    label: str
    demonstrates: str = Field(description="What a reader is meant to learn from this file.")


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
    criteria: list[SampleCriteriaResponse] = Field(
        default_factory=list,
        description=(
            "Every set of screening criteria this build has recordings for — a formal job "
            "description and three informal briefs, one Indonesian, one mixed and one "
            "English. Demo mode can only analyse text it has a recording of, so these are "
            "offered rather than left to be discovered."
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
