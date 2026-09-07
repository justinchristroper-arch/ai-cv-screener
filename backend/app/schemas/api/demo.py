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
