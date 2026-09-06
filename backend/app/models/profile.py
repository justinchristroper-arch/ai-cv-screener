"""Candidate profile and its item tables — the scoring input.

Mirrors docs/data-model.md sections 4.7-4.8.

What this schema does **not** contain is as deliberate as what it does. There
is no name, date of birth, age, gender, photo, nationality, ethnicity,
religion, marital status, address, phone or email column anywhere in the
profile. Sensitive attributes are excluded from the scoring input by
construction, not by asking a model to ignore them.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DATE_PRECISION, DatePrecision
from app.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class CandidateProfile(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Header row for a candidate's extracted content."""

    __tablename__ = "candidate_profile"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    parsed_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parsed_document.id", ondelete="CASCADE"), nullable=False
    )
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_call_log.id", ondelete="SET NULL")
    )

    #: Denormalized from the LLM call for convenient querying in evaluation.
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)


class ProfileSkill(UUIDPrimaryKeyMixin, Base):
    """A skill claimed in the CV."""

    __tablename__ = "profile_skill"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )

    #: As written in the CV.
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)

    #: Casefolded and punctuation-stripped by our code, not by the model.
    #: This is what the deterministic alias matcher indexes.
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)

    evidence_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_span.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_profile_skill_profile_id_normalized_name", "profile_id", "normalized_name"),
    )


class ProfileExperience(UUIDPrimaryKeyMixin, Base):
    """A role held, with whatever date precision the CV actually supports.

    CV dates are messy — "2019 - present", "Jan 2020", "2021". Storing a DATE
    alone would invent precision the document does not have, so
    ``date_precision`` lets the duration matcher state its own uncertainty.
    """

    __tablename__ = "profile_experience"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    role_title: Mapped[str] = mapped_column(Text, nullable=False)

    #: A bias proxy that IS in the scoring input. Kept because experience
    #: requirements are sometimes genuinely about domain; named as a residual
    #: risk in docs/data-model.md section 5 rather than quietly ignored.
    organization: Mapped[str | None] = mapped_column(Text)

    start_date: Mapped[date | None] = mapped_column()
    end_date: Mapped[date | None] = mapped_column()
    date_precision: Mapped[DatePrecision] = mapped_column(DATE_PRECISION, nullable=False)
    is_current: Mapped[bool] = mapped_column(nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text)

    evidence_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_span.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_profile_experience_profile_id", "profile_id"),)


class ProfileEducation(UUIDPrimaryKeyMixin, Base):
    """A qualification claimed in the CV."""

    __tablename__ = "profile_education"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    degree: Mapped[str | None] = mapped_column(Text)
    field_of_study: Mapped[str | None] = mapped_column(Text)

    #: A well-documented bias proxy that IS in the scoring input. Kept because
    #: education requirements are sometimes genuinely about accreditation or
    #: field of study; acknowledged as a limitation rather than claimed neutral.
    institution: Mapped[str | None] = mapped_column(Text)

    completion_year: Mapped[int | None] = mapped_column()

    evidence_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_span.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_profile_education_profile_id", "profile_id"),)


class ProfileProject(UUIDPrimaryKeyMixin, Base):
    """Practical work described in the CV."""

    __tablename__ = "profile_project"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profile.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    technologies: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    evidence_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_span.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_profile_project_profile_id", "profile_id"),)
