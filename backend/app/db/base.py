"""SQLAlchemy declarative base and the constraint naming convention.

The naming convention is not cosmetic. Without it PostgreSQL invents names for
indexes, unique constraints and check constraints, Alembic autogenerate cannot
reliably match an existing constraint to a model one, and downgrades become
guesswork. Fixing the names here makes migrations deterministic.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ORM model.

    `Base.metadata` is the single source of truth for the schema; Alembic
    autogenerates migrations by diffing it against the live database.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
