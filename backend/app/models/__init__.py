"""ORM models — the single source of truth for the database schema.

Importing this package registers every table on ``Base.metadata``, which is
what Alembic diffs against the live database to autogenerate migrations. The
Alembic environment imports this package for exactly that reason.

Phase 2 defines the tables, columns, constraints and indexes from
docs/data-model.md. It deliberately defines **no ORM relationships**: nothing
uses them yet, and unused bidirectional relationships are a common source of
mapper-configuration errors. The foreign keys carry the structure, including
the ``ON DELETE`` behaviour, at the database level where it is durable.
Relationships are added per phase as the services that need them arrive.
"""

from app.models.audit import LlmCallLog, SkillAlias
from app.models.candidate import Candidate, CandidateDocument, ParsedDocument
from app.models.evaluation import EvidenceSpan, MatchResult, Score
from app.models.job import Job, JobDescription, Requirement
from app.models.profile import (
    CandidateProfile,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
)

__all__ = [
    "Candidate",
    "CandidateDocument",
    "CandidateProfile",
    "EvidenceSpan",
    "Job",
    "JobDescription",
    "LlmCallLog",
    "MatchResult",
    "ParsedDocument",
    "ProfileEducation",
    "ProfileExperience",
    "ProfileProject",
    "ProfileSkill",
    "Requirement",
    "Score",
    "SkillAlias",
]
