"""Job, job description and requirement tables.

Mirrors docs/data-model.md sections 4.1-4.3.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    JD_SOURCE_TYPE,
    REQUIREMENT_CATEGORY,
    REQUIREMENT_ORIGIN,
    JdSourceType,
    RequirementCategory,
    RequirementOrigin,
)
from app.models.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A role being screened for.

    There is deliberately no ``status`` column: display status is derived from
    whether a JD exists, whether requirements exist, whether
    ``requirements_confirmed_at`` is set, and the candidates' own states. A
    stored status would duplicate knowable state and drift out of sync with it.
    """

    __tablename__ = "job"

    title: Mapped[str] = mapped_column(Text, nullable=False)

    #: NULL until HR confirms the requirement set. This is the gate: no
    #: candidate is scored against an unconfirmed job.
    requirements_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title_length"),
        Index("ix_job_created_at", "created_at"),
    )


class JobDescription(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """The source prose for a job. One per job, enforced by the unique key."""

    __tablename__ = "job_description"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    source_type: Mapped[JdSourceType] = mapped_column(JD_SOURCE_TYPE, nullable=False)

    #: Display only. Never used to build a filesystem path.
    source_filename: Mapped[str | None] = mapped_column(Text)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    #: Cache key for requirement extraction.
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint("char_length(raw_text) BETWEEN 1 AND 100000", name="raw_text_length"),
    )


class Requirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One atomic, testable criterion extracted from a job description.

    The three ``proposed_*`` columns are written once at extraction and never
    updated. ``proposed_* != current`` is exactly the set of human corrections
    to machine output — a labelled evaluation signal that accumulates from
    ordinary use. They are NULL for requirements added by hand.
    """

    __tablename__ = "requirement"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[RequirementCategory] = mapped_column(REQUIREMENT_CATEGORY, nullable=False)
    must_have: Mapped[bool] = mapped_column(nullable=False)

    #: Defaults applied by the service layer: 3 for must-have, 1 otherwise.
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    display_order: Mapped[int] = mapped_column(nullable=False)
    origin: Mapped[RequirementOrigin] = mapped_column(REQUIREMENT_ORIGIN, nullable=False)

    proposed_text: Mapped[str | None] = mapped_column(Text)
    proposed_category: Mapped[RequirementCategory | None] = mapped_column(REQUIREMENT_CATEGORY)
    proposed_must_have: Mapped[bool | None] = mapped_column()

    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_call_log.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint("weight >= 0", name="weight_non_negative"),
        Index("ix_requirement_job_id_display_order", "job_id", "display_order"),
    )
