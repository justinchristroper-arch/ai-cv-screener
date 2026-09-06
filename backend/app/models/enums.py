"""Enumerations, as Python enums and as PostgreSQL native ENUM types.

docs/data-model.md section 3 specifies native PostgreSQL ENUM types rather than
free-text columns, so that no invalid state can exist at the database level.

Each SQLAlchemy type object is declared **once** here and bound to
``Base.metadata``. Three of them (`requirement_category`, `match_verdict`,
`recommendation_band`) are used by two columns each; reusing one bound type
object is what keeps a single ``CREATE TYPE`` per enum instead of a duplicate.

Every member's name equals its value, so the stored representation is the
obvious one whichever way SQLAlchemy renders it.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum

from app.db.base import Base


class RequirementCategory(str, enum.Enum):
    EDUCATION = "EDUCATION"
    TECHNICAL_SKILL = "TECHNICAL_SKILL"
    EXPERIENCE = "EXPERIENCE"
    PROJECT = "PROJECT"
    SOFT_SKILL_OTHER = "SOFT_SKILL_OTHER"


class RequirementOrigin(str, enum.Enum):
    LLM_EXTRACTED = "LLM_EXTRACTED"
    HR_ADDED = "HR_ADDED"


class JdSourceType(str, enum.Enum):
    PASTED = "PASTED"
    UPLOADED = "UPLOADED"


class CandidateStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    SCORING = "SCORING"
    SCORED = "SCORED"
    FAILED = "FAILED"


class CandidateFailureReason(str, enum.Enum):
    CORRUPT_FILE = "CORRUPT_FILE"
    NO_TEXT_LAYER = "NO_TEXT_LAYER"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    PARSE_TIMEOUT = "PARSE_TIMEOUT"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    MATCHING_FAILED = "MATCHING_FAILED"


class MatchVerdict(str, enum.Enum):
    MATCHED = "MATCHED"
    PARTIAL = "PARTIAL"
    NO_EVIDENCE = "NO_EVIDENCE"


class MatchMethod(str, enum.Enum):
    DETERMINISTIC_EXACT = "DETERMINISTIC_EXACT"
    DETERMINISTIC_ALIAS = "DETERMINISTIC_ALIAS"
    DETERMINISTIC_DURATION = "DETERMINISTIC_DURATION"
    LLM_SEMANTIC = "LLM_SEMANTIC"
    DOWNGRADED_UNVERIFIED = "DOWNGRADED_UNVERIFIED"


class EvidenceVerification(str, enum.Enum):
    VERIFIED_EXACT = "VERIFIED_EXACT"
    VERIFIED_NORMALIZED = "VERIFIED_NORMALIZED"
    UNVERIFIED = "UNVERIFIED"


class RecommendationBand(str, enum.Enum):
    STRONG_MATCH = "STRONG_MATCH"
    GOOD_MATCH = "GOOD_MATCH"
    REVIEW = "REVIEW"
    LOW_MATCH = "LOW_MATCH"


class ScoreStatus(str, enum.Enum):
    COMPUTED = "COMPUTED"
    UNDEFINED_NO_WEIGHT = "UNDEFINED_NO_WEIGHT"


class DatePrecision(str, enum.Enum):
    DAY = "DAY"
    MONTH = "MONTH"
    YEAR = "YEAR"
    UNKNOWN = "UNKNOWN"


class LlmPurpose(str, enum.Enum):
    JD_EXTRACTION = "JD_EXTRACTION"
    PROFILE_EXTRACTION = "PROFILE_EXTRACTION"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"


class LlmSource(str, enum.Enum):
    LIVE = "LIVE"
    FIXTURE = "FIXTURE"


class LlmStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    TIMEOUT = "TIMEOUT"


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
