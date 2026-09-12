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


class RequirementSpecType(str, enum.Enum):
    """The six structured criterion types (ADR-0012).

    A closed set on purpose. A criterion outside it cannot be stored, not merely
    cannot be evaluated -- the interface shows the boundary rather than
    accepting a requirement the engine could never answer.

    NULL on a requirement row means it predates ADR-0012 and carries free text
    instead.
    """

    EDUCATION_MIN = "EDUCATION_MIN"
    GPA_MIN = "GPA_MIN"
    EXPERIENCE_MIN = "EXPERIENCE_MIN"
    SKILL = "SKILL"
    INTERNSHIP_MIN = "INTERNSHIP_MIN"
    LANGUAGE_PRESENT = "LANGUAGE_PRESENT"

    #: Duration restricted to a field: "at least 12 months in Accounting". The
    #: first type to carry a subject AND a threshold together. Added because
    #: EXPERIENCE_MIN answers only "how long", so four years of retail matched
    #: an accounting vacancy exactly as well as four years of accounting. This
    #: is not the industry criterion ADR-0012 rejected: it asks whether the
    #: CV's own entry for a dated role evidences a supported skill, which is
    #: readable, rather than judging what counts as an industry.
    EXPERIENCE_IN_FIELD = "EXPERIENCE_IN_FIELD"

    #: A credential the CV claims: Brevet A, CPA, AWS Certified. Presence
    #: only -- never a grade, never a date, and never an inference that one
    #: certificate implies another. Indonesian CVs lean on credentials much
    #: more than Western ones, and for an accounting vacancy Brevet A/B is
    #: often a stated requirement rather than a nice-to-have.
    CERTIFICATION_PRESENT = "CERTIFICATION_PRESENT"


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

    #: The document says something here and the engine cannot safely resolve it
    #: -- a GPA whose scale is not stated, for example. Deliberately NOT a
    #: synonym for NO_EVIDENCE: absence is a fact about the document, whereas
    #: this is a limit of our own reading, and reporting one as the other is
    #: exactly what ADR-0002 forbids. Excluded from both sides of the score
    #: (ADR-0012), and never a trigger for the must-have guard.
    NEEDS_REVIEW = "NEEDS_REVIEW"


class MatchMethod(str, enum.Enum):
    DETERMINISTIC_EXACT = "DETERMINISTIC_EXACT"
    DETERMINISTIC_ALIAS = "DETERMINISTIC_ALIAS"
    DETERMINISTIC_DURATION = "DETERMINISTIC_DURATION"

    #: The structured screening engine (ADR-0012). Its own value rather than a
    #: borrowed one: recording a grade comparison as DETERMINISTIC_EXACT, or a
    #: language check as DETERMINISTIC_ALIAS, would make the audit trail say
    #: something that did not happen.
    DETERMINISTIC_STRUCTURED = "DETERMINISTIC_STRUCTURED"

    LLM_SEMANTIC = "LLM_SEMANTIC"
    DOWNGRADED_UNVERIFIED = "DOWNGRADED_UNVERIFIED"


#: The methods that reached a verdict without asking a model. Declared once,
#: next to the enum, because it is reported to the recruiter as "decided by
#: code" -- a claim that must not quietly become false when a method is added.
DETERMINISTIC_METHODS: tuple[MatchMethod, ...] = (
    MatchMethod.DETERMINISTIC_EXACT,
    MatchMethod.DETERMINISTIC_ALIAS,
    MatchMethod.DETERMINISTIC_DURATION,
    MatchMethod.DETERMINISTIC_STRUCTURED,
)


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

    #: Requirements and weights exist, but every one of them came back
    #: NEEDS_REVIEW, so there is nothing decidable left to average. A 0 here
    #: would say the candidate failed an evaluation that never completed.
    UNDEFINED_NO_DECIDABLE = "UNDEFINED_NO_DECIDABLE"


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
