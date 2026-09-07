"""The contract the model must satisfy when judging requirement/CV pairs.

The model's job here is narrow on purpose (ADR-0001): for each requirement it
is handed, say whether the CV shows evidence for it, and quote the text it read.
It does not score, weight, rank, or recommend, and there is no field in which it
could -- ``extra="forbid"`` rejects the whole reply if it tries.

Two rules the schema itself enforces, so they cannot be forgotten by a prompt:

* a positive verdict (``MATCHED`` or ``PARTIAL``) **must** carry a quote;
* ``NO_EVIDENCE`` **must not** carry one, because there is by definition
  nothing to cite.

Requirements are addressed by list position rather than by database id. The
model never sees an internal identifier, and the service checks that the reply
covers exactly the positions it asked about -- no gaps, no repeats, no
invented indices.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import MatchVerdict
from app.schemas.llm.json_schema import provider_json_schema

#: One call carries the pairs a deterministic rule could not settle for one
#: candidate. The cap matches the requirement-extraction cap, since that is the
#: largest requirement set a job can have.
MAX_VERDICTS = 60

MIN_QUOTE_LENGTH = 8
MAX_QUOTE_LENGTH = 400

MIN_REASON_LENGTH = 3
MAX_REASON_LENGTH = 400


class RequirementVerdict(BaseModel):
    """One requirement, judged against one CV."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(
        ge=0,
        description="The position of the requirement in the list supplied in the user turn.",
    )
    verdict: MatchVerdict = Field(
        description=(
            "MATCHED when the CV clearly evidences the requirement, PARTIAL when it "
            "evidences something related but incomplete, NO_EVIDENCE when the CV "
            "contains nothing that evidences it."
        )
    )
    evidence_quote: str | None = Field(
        description=(
            "A verbatim sentence or line copied from the CV supporting the verdict. "
            "Required for MATCHED and PARTIAL; must be null for NO_EVIDENCE."
        )
    )
    reason: str = Field(
        min_length=MIN_REASON_LENGTH,
        max_length=MAX_REASON_LENGTH,
        description=(
            "One sentence about what the document contains. Never a claim about the "
            "person, and never a score, a rating or a recommendation."
        ),
    )

    @field_validator("reason")
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < MIN_REASON_LENGTH:
            raise ValueError("reason must not be blank")
        return stripped

    @model_validator(mode="after")
    def _evidence_matches_the_verdict(self) -> RequirementVerdict:
        quote = (self.evidence_quote or "").strip()

        if self.verdict is MatchVerdict.NO_EVIDENCE:
            if quote:
                raise ValueError("NO_EVIDENCE must not carry an evidence_quote")
            return self

        if not quote:
            raise ValueError(f"{self.verdict.value} requires an evidence_quote from the CV")
        if not MIN_QUOTE_LENGTH <= len(quote) <= MAX_QUOTE_LENGTH:
            raise ValueError(
                f"evidence_quote must be between {MIN_QUOTE_LENGTH} and "
                f"{MAX_QUOTE_LENGTH} characters"
            )

        object.__setattr__(self, "evidence_quote", quote)
        return self


class SemanticMatchOutput(BaseModel):
    """The whole reply: one verdict per requirement the call asked about."""

    model_config = ConfigDict(extra="forbid")

    verdicts: list[RequirementVerdict] = Field(
        min_length=1,
        max_length=MAX_VERDICTS,
        description="Exactly one entry per requirement supplied, in any order.",
    )


def semantic_match_json_schema() -> dict[str, Any]:
    """JSON Schema for the provider's structured-output constraint."""
    return provider_json_schema(SemanticMatchOutput)
