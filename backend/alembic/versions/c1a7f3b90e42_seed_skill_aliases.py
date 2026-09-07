"""seed skill aliases

Revision ID: c1a7f3b90e42
Revises: b527c519d55f
Create Date: 2026-09-07

Data only: this migration adds no table, column, constraint or index, so
`alembic check` still reports no drift between the models and the database.

`skill_alias` was created empty by the initial schema and is the lookup the
deterministic alias matcher reads (docs/data-model.md section 4.13). Seeding it
here rather than from application code keeps one source of truth: the matcher
queries the table and nothing else, so an operator can add a row without
touching Python.

The list is deliberately short and unambiguous. Every entry is a pair a human
would read as the same technology. Genuinely ambiguous abbreviations are left
out on purpose — "tf" means both Terraform and TensorFlow, and a wrong alias
would produce a confident, wrong MATCHED verdict, which is worse than routing
the pair to the model.
"""

from collections.abc import Sequence
from typing import Union
import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a7f3b90e42"
down_revision: Union[str, Sequence[str], None] = "b527c519d55f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (alias, canonical). Both stored in the normalized form
#: `app.services.matching.normalize_skill_name` produces: lowercase, single
#: spaces, and `+ # .` preserved so "c++", "c#" and "node.js" survive intact.
SEED_ALIASES: tuple[tuple[str, str], ...] = (
    ("postgres", "postgresql"),
    ("psql", "postgresql"),
    ("js", "javascript"),
    ("ecmascript", "javascript"),
    ("ts", "typescript"),
    ("k8s", "kubernetes"),
    ("nodejs", "node.js"),
    ("node js", "node.js"),
    ("golang", "go"),
    ("c sharp", "c#"),
    ("csharp", "c#"),
    ("dotnet", ".net"),
    (".net core", ".net"),
    ("py", "python"),
    ("aws", "amazon web services"),
    ("gcp", "google cloud platform"),
    ("ml", "machine learning"),
    ("nlp", "natural language processing"),
    ("ci cd", "continuous integration"),
    ("rdbms", "relational database"),
    ("k8", "kubernetes"),
    ("postgre", "postgresql"),
)


skill_alias = sa.table(
    "skill_alias",
    sa.column("id", sa.Uuid()),
    sa.column("canonical_name", sa.Text()),
    sa.column("alias", sa.Text()),
)


def upgrade() -> None:
    op.bulk_insert(
        skill_alias,
        [
            {"id": uuid.uuid4(), "alias": alias, "canonical_name": canonical}
            for alias, canonical in SEED_ALIASES
        ],
    )


def downgrade() -> None:
    # Only the seeded rows. An alias someone added by hand is not this
    # migration's to delete.
    op.execute(
        skill_alias.delete().where(
            skill_alias.c.alias.in_([alias for alias, _ in SEED_ALIASES])
        )
    )
