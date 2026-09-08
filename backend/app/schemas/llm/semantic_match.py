"""The contract the model must satisfy when judging requirement/CV pairs.

The model's job here is narrow on purpose (ADR-0001): for each requirement it
is handed, say whether the CV shows evidence for it, and quote the text it read.
It does not score, weight, rank, or recommend, and there is no field in which it
could -- ``extra="forbid"`` rejects the whole reply if it tries.

Two rules the schema itself enforces, so they cannot be forgotten by a prompt:

* a positive verdict (``MATCHED`` or ``PARTIAL``) **must** carry a quote;
* ``NO_EVIDENCE`` **must not** carry one, because there is by definition
  nothing to cite.

The quote's length is bounded here only enough to reject a fragment that could
not identify any passage. Whether a short quote is a real citation or a
coincidence inside a longer word is not knowable from the reply alone, and is
decided by ``services/evidence.py`` against the document itself.

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

#: A quote this short is not a citation, it is a fragment: one or two characters
#: carry no information about which line of a CV was read. Three matches
#: `matching.MIN_SEARCHABLE_TOKEN_LENGTH`, which already decided that a one- or
#: two-character token is too ambiguous for this application to reason about.
#:
#: It used to be eight, which rejected "English" -- the exact word a CV uses for
#: a language requirement, quoted correctly and present verbatim in the document.
#: Length was never the property that mattered. What guards against a fragment
#: matching some longer word by accident now lives in `services/evidence.py`,
#: which is the only component that knows *where* a quote landed.
MIN_QUOTE_LENGTH = 3
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
        if len(quote) < MIN_QUOTE_LENGTH:
            # Worded so a retry can act on it. The previous message named a
            # bound without saying what to do, and a model whose quote was
            # correct had no compliant answer available -- it repeated the same
            # reply and burned the retry.
            raise ValueError(
                f"evidence_quote is too short to identify a passage "
                f"({len(quote)} characters, minimum {MIN_QUOTE_LENGTH}). "
                f"Quote the whole line the term appears on instead."
            )
        if len(quote) > MAX_QUOTE_LENGTH:
            raise ValueError(
                f"evidence_quote is longer than {MAX_QUOTE_LENGTH} characters. "
                f"Quote only the sentence or line that supports the verdict."
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
