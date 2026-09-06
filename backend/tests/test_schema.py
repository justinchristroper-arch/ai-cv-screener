"""The ORM schema must match the approved data model.

These tests read `Base.metadata` and need no database, so the data model's
guarantees are checked on every test run rather than only when PostgreSQL
happens to be up.

The fairness assertion here is the important one: docs/data-model.md section 5
states that sensitive attributes are excluded from the scoring input *by
construction*. This is what turns that claim into a regression test.
"""

from __future__ import annotations

import app.models  # noqa: F401  # importing registers every table on Base.metadata
from app.db.base import Base
from app.models.enums import ENUM_TYPE_NAMES

# Every table specified in docs/data-model.md section 4.
EXPECTED_TABLES = {
    "job",
    "job_description",
    "requirement",
    "candidate",
    "candidate_document",
    "parsed_document",
    "candidate_profile",
    "profile_skill",
    "profile_experience",
    "profile_education",
    "profile_project",
    "evidence_span",
    "match_result",
    "score",
    "llm_call_log",
    "skill_alias",
}

# Attributes that must not exist anywhere in the schema. Listed in
# docs/data-model.md section 5.
FORBIDDEN_COLUMN_NAMES = {
    "date_of_birth",
    "birth_date",
    "dob",
    "age",
    "gender",
    "sex",
    "photo",
    "photo_url",
    "image",
    "nationality",
    "ethnicity",
    "race",
    "religion",
    "marital_status",
    "family_status",
    "home_address",
    "address",
    "postal_code",
    "zip_code",
    "phone",
    "phone_number",
    "email",
}


def test_every_specified_table_is_defined() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_no_sensitive_column_exists_anywhere() -> None:
    """The fairness boundary, as a test rather than as a promise."""
    offenders = [
        f"{table_name}.{column.name}"
        for table_name, table in Base.metadata.tables.items()
        for column in table.columns
        if column.name in FORBIDDEN_COLUMN_NAMES
    ]

    assert offenders == [], f"sensitive attribute(s) present in the schema: {offenders}"


def test_candidate_profile_carries_no_identity_column() -> None:
    """The scorer reads the profile; the profile must not contain a name.

    `display_name` lives on `candidate` (display only), which is what makes the
    matching and scoring stages structurally incapable of reading it.
    """
    profile_columns = {column.name for column in Base.metadata.tables["candidate_profile"].columns}

    assert not any("name" in column for column in profile_columns)
    assert "display_name" in {column.name for column in Base.metadata.tables["candidate"].columns}


def test_positive_verdict_requires_evidence_is_a_database_constraint() -> None:
    """The evidence-first principle must be enforced by the database."""
    constraint_names = {
        constraint.name
        for constraint in Base.metadata.tables["match_result"].constraints
        if constraint.name
    }

    assert "ck_match_result_positive_verdict_requires_evidence" in constraint_names


def test_failed_candidate_requires_a_reason_is_a_database_constraint() -> None:
    constraint_names = {
        constraint.name
        for constraint in Base.metadata.tables["candidate"].constraints
        if constraint.name
    }

    assert "ck_candidate_failure_reason_iff_failed" in constraint_names


def test_evidence_span_location_matches_verification_status() -> None:
    constraint_names = {
        constraint.name
        for constraint in Base.metadata.tables["evidence_span"].constraints
        if constraint.name
    }

    assert "ck_evidence_span_located_iff_verified" in constraint_names


def test_one_verdict_per_requirement_and_candidate() -> None:
    constraint_names = {
        constraint.name
        for constraint in Base.metadata.tables["match_result"].constraints
        if constraint.name
    }

    assert "uq_match_result_requirement_candidate" in constraint_names


def test_score_stores_every_input_needed_to_recompute_it() -> None:
    """Recomputing from stored rows must reproduce the stored value exactly."""
    columns = {column.name for column in Base.metadata.tables["score"].columns}

    for required in ("weighted_sum", "total_weight", "score_raw", "scoring_config_version"):
        assert required in columns, f"score.{required} is needed for reconstructibility"


def test_llm_calls_are_auditable() -> None:
    columns = {column.name for column in Base.metadata.tables["llm_call_log"].columns}

    for required in ("model", "prompt_version", "input_sha256", "source", "status", "attempt"):
        assert required in columns

    # The input text itself must never be stored: CV content must not reach a
    # log table that gets exported or shipped to an aggregator.
    assert "input_text" not in columns
    assert "prompt_text" not in columns


def test_fourteen_enum_types_are_declared() -> None:
    assert len(ENUM_TYPE_NAMES) == 14
    assert len(set(ENUM_TYPE_NAMES)) == 14, "enum type names must be unique"


def test_no_vector_columns_yet() -> None:
    """pgvector was deferred in Phase 1; nothing should have crept in."""
    column_types = [
        str(column.type).lower()
        for table in Base.metadata.tables.values()
        for column in table.columns
    ]

    assert not any("vector" in type_name for type_name in column_types)
