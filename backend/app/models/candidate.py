"""Candidate, uploaded document and parsed document tables.

Mirrors docs/data-model.md sections 4.4-4.6.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    CANDIDATE_FAILURE_REASON,
    CANDIDATE_STATUS,
    CandidateFailureReason,
    CandidateStatus,
)
from app.models.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Candidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An applicant to one job.

    ``display_name`` lives here and **not** on ``candidate_profile``. The
    matching and scoring services read only from the profile, which makes the
    scorer structurally incapable of reading a name — the fairness boundary
    described in docs/data-model.md section 5.
    """

    __tablename__ = "candidate"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )

    #: Display only. Never reaches the matcher or the scorer.
    display_name: Mapped[str | None] = mapped_column(Text)

    status: Mapped[CandidateStatus] = mapped_column(
        CANDIDATE_STATUS, nullable=False, default=CandidateStatus.UPLOADED
    )

    #: When the current stage began. A candidate in a transient state past a
    #: timeout is detectably stuck rather than silently pending forever.
    stage_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    failure_reason: Mapped[CandidateFailureReason | None] = mapped_column(CANDIDATE_FAILURE_REASON)

    #: Human-readable detail. Never a stack trace.
    failure_detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # A failed candidate always has a reason; a non-failed one never does.
        # The product rule that failures are reported honestly, enforced by the
        # database rather than by convention.
        CheckConstraint(
            "(status = 'FAILED') = (failure_reason IS NOT NULL)",
            name="failure_reason_iff_failed",
        ),
        Index("ix_candidate_job_id_status", "job_id", "status"),
    )


class CandidateDocument(UUIDPrimaryKeyMixin, Base):
    """The uploaded file itself."""

    __tablename__ = "candidate_document"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    #: Display only. Never used to build a filesystem path.
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)

    #: Generated from the row id; no client-controlled component.
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)

    #: Verified by magic bytes at upload time, not by file extension.
    content_type: Mapped[str] = mapped_column(Text, nullable=False)

    size_bytes: Mapped[int] = mapped_column(nullable=False)
    page_count: Mapped[int | None] = mapped_column()
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="size_positive"),
        Index("ix_candidate_document_file_sha256", "file_sha256"),
    )


class ParsedDocument(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Extracted text plus everything needed to cite a location within it."""

    __tablename__ = "parsed_document"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_document.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    #: The substrate every evidence span is verified against.
    full_text: Mapped[str] = mapped_column(Text, nullable=False)

    #: Which normalizer produced `full_text`. If the normalizer changes,
    #: previously verified spans may no longer match; this makes that a
    #: visible, explainable event rather than a mysterious drop in validity.
    normalization_version: Mapped[str] = mapped_column(Text, nullable=False)

    #: [{"page": 1, "start": 0, "end": 1423}, ...] — maps char offset to page,
    #: so a page number is never asked of the model.
    page_offsets: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)

    char_count: Mapped[int] = mapped_column(nullable=False)
    page_count: Mapped[int] = mapped_column(nullable=False)

    #: False means a scanned or image-only PDF: the candidate fails honestly
    #: rather than being scored against empty text.
    has_text_layer: Mapped[bool] = mapped_column(nullable=False)

    language_detected: Mapped[str | None] = mapped_column(Text)

    #: [{"pattern": ..., "offset": N, "excerpt": ...}] — flagged and surfaced,
    #: never stripped.
    injection_flags: Mapped[list[dict] | None] = mapped_column(JSONB)

    #: Cache key for profile extraction.
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    parser_name: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_parsed_document_text_sha256", "text_sha256"),)
