"""Alembic environment.

Two decisions worth stating:

* The database URL is read from the application settings, not from
  ``alembic.ini``. No credential is ever written to a tracked file, and
  migrations cannot accidentally run against a different database than the app.
* ``Base.metadata`` is the single source of truth for the schema. Autogenerate
  diffs the ORM models against the live database, so the models and the
  migrations cannot drift apart without it showing up as a pending revision.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# The backend package root must be on sys.path before `app` can be imported.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: E402,F401  # importing registers every table on Base.metadata
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """The URL the application itself would use."""
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    # The URL is handed straight to create_engine rather than written into the
    # Alembic config object: a password containing '%' would otherwise be
    # mangled by ConfigParser's string interpolation.
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
