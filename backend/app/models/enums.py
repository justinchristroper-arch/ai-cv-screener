"""PostgreSQL native ENUM types, built from the domain enums in ``core``.

docs/data-model.md section 3 specifies native PostgreSQL ENUM types rather than
free-text columns, so that no invalid state can exist at the database level.

The Python enum *values* live in ``app/core/enums.py`` (a lower layer, so that
``schemas`` can import the taxonomy without depending on ``models``); this
module owns only the SQLAlchemy binding. They are re-exported here so existing
``from app.models.enums import X`` imports keep working.

Each SQLAlchemy type object is declared **once** here and bound to
``Base.metadata``. Three of them (`requirement_category`, `match_verdict`,
`recommendation_band`) are used by two columns each; reusing one bound type
object is what keeps a single ``CREATE TYPE`` per enum instead of a duplicate.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum

from app.core.enums import (
    CandidateFailureReason,
    CandidateStatus,
    DatePrecision,
    EvidenceVerification,
    JdSourceType,
    LlmPurpose,
    LlmSource,
    LlmStatus,
    MatchMethod,
    MatchVerdict,
    RecommendationBand,
    RequirementCategory,
    RequirementOrigin,
    ScoreStatus,
)
from app.db.base import Base

__all__ = [
    "CANDIDATE_FAILURE_REASON",
    "CANDIDATE_STATUS",
    "DATE_PRECISION",
    "ENUM_TYPE_NAMES",
    "EVIDENCE_VERIFICATION",
    "JD_SOURCE_TYPE",
    "LLM_PURPOSE",
    "LLM_SOURCE",
    "LLM_STATUS",
    "MATCH_METHOD",
    "MATCH_VERDICT",
    "RECOMMENDATION_BAND",
    "REQUIREMENT_CATEGORY",
    "REQUIREMENT_ORIGIN",
    "SCORE_STATUS",
    "CandidateFailureReason",
    "CandidateStatus",
    "DatePrecision",
    "EvidenceVerification",
    "JdSourceType",
    "LlmPurpose",
    "LlmSource",
    "LlmStatus",
    "MatchMethod",
    "MatchVerdict",
    "RecommendationBand",
    "RequirementCategory",
    "RequirementOrigin",
    "ScoreStatus",
]


def _pg_enum(python_enum: type[enum.Enum], name: str) -> SAEnum:
    """Build a metadata-bound native PostgreSQL ENUM type."""
    return SAEnum(
        python_enum,
        name=name,
        metadata=Base.metadata,
        values_callable=lambda members: [member.value for member in members],
    )


REQUIREMENT_CATEGORY = _pg_enum(RequirementCategory, "requirement_category")
REQUIREMENT_ORIGIN = _pg_enum(RequirementOrigin, "requirement_origin")
JD_SOURCE_TYPE = _pg_enum(JdSourceType, "jd_source_type")
CANDIDATE_STATUS = _pg_enum(CandidateStatus, "candidate_status")
CANDIDATE_FAILURE_REASON = _pg_enum(CandidateFailureReason, "candidate_failure_reason")
MATCH_VERDICT = _pg_enum(MatchVerdict, "match_verdict")
MATCH_METHOD = _pg_enum(MatchMethod, "match_method")
EVIDENCE_VERIFICATION = _pg_enum(EvidenceVerification, "evidence_verification")
RECOMMENDATION_BAND = _pg_enum(RecommendationBand, "recommendation_band")
SCORE_STATUS = _pg_enum(ScoreStatus, "score_status")
DATE_PRECISION = _pg_enum(DatePrecision, "date_precision")
LLM_PURPOSE = _pg_enum(LlmPurpose, "llm_purpose")
LLM_SOURCE = _pg_enum(LlmSource, "llm_source")
LLM_STATUS = _pg_enum(LlmStatus, "llm_status")

#: Every enum type name, in creation order. Used by the initial migration and
#: by the schema test that asserts the data model matches the specification.
ENUM_TYPE_NAMES = (
    "requirement_category",
    "requirement_origin",
    "jd_source_type",
    "candidate_status",
    "candidate_failure_reason",
    "match_verdict",
    "match_method",
    "evidence_verification",
    "recommendation_band",
    "score_status",
    "date_precision",
    "llm_purpose",
    "llm_source",
    "llm_status",
)
