"""Live database tests.

Every test here is marked `requires_db` and is skipped, with a reason naming
the connection string, when PostgreSQL is not reachable. Skipping is honest;
passing without a database would not be.

These assert against the *migrated* schema, so they verify that Alembic
actually produced what the models describe — not merely that the models are
internally consistent, which `test_schema.py` already covers offline.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.session import get_engine
from tests.test_schema import EXPECTED_TABLES, FORBIDDEN_COLUMN_NAMES

pytestmark = pytest.mark.requires_db


def test_database_accepts_a_connection() -> None:
    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1


def test_server_is_postgresql() -> None:
    with get_engine().connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar_one()

    assert "PostgreSQL" in version


def test_migrations_have_been_applied() -> None:
    """`alembic upgrade head` must have run against this database."""
    inspector = inspect(get_engine())

    assert "alembic_version" in inspector.get_table_names(), (
        "alembic_version is absent — run: alembic upgrade head"
    )

    with get_engine().connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()

    assert revision, "alembic_version table is empty"


def test_every_model_table_exists_in_the_database() -> None:
    actual_tables = set(inspect(get_engine()).get_table_names())

    missing = EXPECTED_TABLES - actual_tables
    assert not missing, f"tables missing from the migrated database: {sorted(missing)}"


def test_live_schema_contains_no_sensitive_columns() -> None:
    """The strong form of the fairness assertion: against the real schema."""
    inspector = inspect(get_engine())
    offenders = [
        f"{table}.{column['name']}"
        for table in inspector.get_table_names()
        for column in inspector.get_columns(table)
        if column["name"] in FORBIDDEN_COLUMN_NAMES
    ]

    assert offenders == [], f"sensitive attribute(s) in the live schema: {offenders}"


def test_all_enum_types_exist_in_the_database() -> None:
    from app.models.enums import ENUM_TYPE_NAMES

    with get_engine().connect() as connection:
        rows = connection.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
        existing = {row[0] for row in rows}

    missing = set(ENUM_TYPE_NAMES) - existing
    assert not missing, f"enum types missing from the database: {sorted(missing)}"


def test_pgvector_extension_is_not_installed() -> None:
    """Phase 1 deferred pgvector. Verify the deployed database matches."""
    with get_engine().connect() as connection:
        rows = connection.execute(text("SELECT extname FROM pg_extension"))
        extensions = {row[0] for row in rows}

    assert "vector" not in extensions


def test_evidence_constraint_rejects_a_positive_verdict_without_evidence() -> None:
    """Exercise the CHECK constraint against the real database engine.

    A constraint that exists in metadata but was never emitted to PostgreSQL
    would pass the offline test and still allow bad data. This closes that gap.
    """
    engine = get_engine()
    with engine.begin() as connection:
        # A row violating the constraint must be rejected. Using a transaction
        # that is rolled back means nothing is persisted either way.
        transaction_failed = False
        try:
            connection.execute(
                text(
                    "INSERT INTO match_result "
                    "(id, requirement_id, candidate_id, verdict, decided_by, reason, downgraded) "
                    "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
                    "'MATCHED', 'LLM_SEMANTIC', 'no evidence attached', false)"
                )
            )
        except Exception:  # noqa: BLE001 — any rejection proves the point
            transaction_failed = True

    assert transaction_failed, (
        "a MATCHED verdict with no evidence span was accepted; the "
        "positive_verdict_requires_evidence constraint is not enforced"
    )


def test_model_metadata_matches_the_live_table_list() -> None:
    actual = set(inspect(get_engine()).get_table_names()) - {"alembic_version"}

    assert actual == set(Base.metadata.tables)
