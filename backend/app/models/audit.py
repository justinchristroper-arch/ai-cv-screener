"""LLM call audit log and the skill alias lookup table.

Mirrors docs/data-model.md sections 4.12-4.13.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    LLM_PURPOSE,
    LLM_SOURCE,
    LLM_STATUS,
    LlmPurpose,
    LlmSource,
    LlmStatus,
)
from app.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class LlmCallLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One record per model call: what ran, on what, and how it went.

    ``input_sha256`` is stored **instead of** the input text. CV content must
    not end up in a log table that gets exported, backed up, or shipped to an
    aggregator; the hash is enough to key the cache and to prove that two calls
    had identical input.
    """

    __tablename__ = "llm_call_log"

    purpose: Mapped[LlmPurpose] = mapped_column(LLM_PURPOSE, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)

    #: Cache key. Never the input itself.
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    source: Mapped[LlmSource] = mapped_column(LLM_SOURCE, nullable=False)
    status: Mapped[LlmStatus] = mapped_column(LLM_STATUS, nullable=False)

    #: 1 or 2 — the retry policy is exactly one retry on schema failure.
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    input_tokens: Mapped[int | None] = mapped_column()
    output_tokens: Mapped[int | None] = mapped_column()
    latency_ms: Mapped[int | None] = mapped_column()

    error_detail: Mapped[str | None] = mapped_column(Text)

    #: Truncated. Written only on SCHEMA_INVALID, to debug a malformed reply.
    raw_response_excerpt: Mapped[str | None] = mapped_column(Text)

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job.id", ondelete="SET NULL")
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index(
            "ix_llm_call_log_input_sha256_prompt_version_model",
            "input_sha256",
            "prompt_version",
            "model",
        ),
        Index("ix_llm_call_log_created_at", "created_at"),
    )


class SkillAlias(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Curated alias -> canonical skill name.

    Small, boring, and it removes a whole class of pairs from the LLM's
    workload: cheaper, faster, and perfectly reproducible.
    """

    __tablename__ = "skill_alias"

    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("alias", name="uq_skill_alias_alias"),)
