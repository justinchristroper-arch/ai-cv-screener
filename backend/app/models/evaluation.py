"""Evidence, match results and scores.

Mirrors docs/data-model.md sections 4.9-4.11.

Two of the project's product rules are expressed here as database constraints
rather than as conventions someone might forget:

* a positive verdict cannot be stored without an evidence span;
* a verified span always has a location, an unverified one never claims to.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    EVIDENCE_VERIFICATION,
    MATCH_METHOD,
    MATCH_VERDICT,
    RECOMMENDATION_BAND,
    SCORE_STATUS,
    EvidenceVerification,
    MatchMethod,
    MatchVerdict,
    RecommendationBand,
    ScoreStatus,
)
from app.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class EvidenceSpan(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A quotation from a CV supporting a claim.

    The model supplies ``quoted_text`` and nothing else: the offsets are found
    by our verifier searching the parsed document. Asking an LLM for character
    offsets is unreliable; searching for its quote in our own text is trivial
    and yields verification for free.
    """

    __tablename__ = "evidence_span"

    parsed_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parsed_document.id", ondelete="CASCADE"), nullable=False
    )

    #: The only field the model supplies.
    quoted_text: Mapped[str] = mapped_column(Text, nullable=False)

    #: Located by our verifier. NULL when the span could not be verified.
    start_char: Mapped[int | None] = mapped_column()
    end_char: Mapped[int | None] = mapped_column()

    #: Derived from parsed_document.page_offsets, never asked of the model.
    page_number: Mapped[int | None] = mapped_column()

    verification_status: Mapped[EvidenceVerification] = mapped_column(
        EVIDENCE_VERIFICATION, nullable=False
    )
    normalization_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "(verification_status = 'UNVERIFIED') = (start_char IS NULL)",
            name="located_iff_verified",
        ),
        Index("ix_evidence_span_parsed_document_id", "parsed_document_id"),
    )


class MatchResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One verdict for one (requirement, candidate) pair."""

    __tablename__ = "match_result"

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirement.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )

    #: The verdict actually used for scoring.
    verdict: Mapped[MatchVerdict] = mapped_column(MATCH_VERDICT, nullable=False)

    #: Which mechanism decided this pair. Makes the deterministic/LLM split
    #: measurable rather than a matter of belief.
    decided_by: Mapped[MatchMethod] = mapped_column(MATCH_METHOD, nullable=False)

    #: Shown to the recruiter. Evidence-first wording: absence is reported as
    #: "no evidence found in CV", never as a claim about the candidate.
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    evidence_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_span.id", ondelete="SET NULL")
    )

    #: What the model said before any downgrade. The count of downgraded rows
    #: is the hallucinated-or-injected-evidence rate, reported in Phase 13.
    raw_verdict: Mapped[MatchVerdict | None] = mapped_column(MATCH_VERDICT)
    downgraded: Mapped[bool] = mapped_column(nullable=False, default=False)

    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_call_log.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint(
            "requirement_id", "candidate_id", name="uq_match_result_requirement_candidate"
        ),
        # The evidence-first principle, enforced by the database.
        CheckConstraint(
            "verdict = 'NO_EVIDENCE' OR evidence_span_id IS NOT NULL",
            name="positive_verdict_requires_evidence",
        ),
        Index("ix_match_result_candidate_id", "candidate_id"),
    )


class Score(UUIDPrimaryKeyMixin, Base):
    """A candidate's computed score, and every input needed to reproduce it.

    If a job has no requirements or every weight is zero the score is not 0 —
    it is undefined (``status = UNDEFINED_NO_WEIGHT``, numeric columns NULL).
    Returning 0 would read as "terrible candidate" when the truth is "nothing
    was asked of them", and it removes the division-by-zero case by construction.
    """

    __tablename__ = "score"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[ScoreStatus] = mapped_column(SCORE_STATUS, nullable=False)

    weighted_sum: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    total_weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    score_raw: Mapped[Decimal | None] = mapped_column(Numeric(9, 8))
    score: Mapped[int | None] = mapped_column()

    #: NULL when the job has no must-have requirements.
    must_have_coverage: Mapped[Decimal | None] = mapped_column(Numeric(9, 8))

    #: The band implied by the score alone, before the must-have guard.
    band_raw: Mapped[RecommendationBand | None] = mapped_column(RECOMMENDATION_BAND)

    #: The band actually displayed, after the guard.
    band: Mapped[RecommendationBand | None] = mapped_column(RECOMMENDATION_BAND)

    capped: Mapped[bool] = mapped_column(nullable=False, default=False)
    capped_by_requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirement.id", ondelete="SET NULL")
    )

    #: Names the value/threshold bundle used, so a stored score stays
    #: interpretable against the rules that produced it.
    scoring_config_version: Mapped[str] = mapped_column(Text, nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("score >= 0 AND score <= 100", name="score_range"),)
