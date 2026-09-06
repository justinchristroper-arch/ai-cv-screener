"""Domain enumerations, as plain Python values.

These live in ``core`` rather than ``models`` for a layering reason: the
dependency direction in docs/architecture.md section 2.1 puts ``core`` and
``schemas`` at the bottom, below ``models``. The LLM output schemas in
``schemas/llm`` need the requirement taxonomy, and importing it from
``models`` would make a bottom layer depend on a higher one.

``models/enums.py`` imports these and builds the PostgreSQL ENUM types from
them, so there is still exactly one definition of every value. Every member's
name equals its value, so the stored representation is the obvious one
whichever way SQLAlchemy renders it.
"""

from __future__ import annotations

import enum


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
