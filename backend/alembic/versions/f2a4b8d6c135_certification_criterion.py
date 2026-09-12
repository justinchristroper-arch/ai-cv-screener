"""An eighth criterion type: certification presence.

Revision ID: f2a4b8d6c135
Revises: e7c95a2f1b08

Indonesian CVs lean on credentials far more than Western ones, and for an
accounting vacancy Brevet A/B is frequently a stated requirement rather than a
nice-to-have. `CERTIFICATION_PRESENT` names one from a curated list and asks
only whether the CV claims it -- no grade, no date comparison, and no inference
that one certificate implies another.

One new enum value, and a rewrite of `spec_is_complete` to admit it. The new
type takes a subject and nothing else, which is the shape three existing types
already have, so it joins their branch rather than gaining one of its own.
The constraint is still dropped and recreated: PostgreSQL has no ALTER for a
CHECK expression.

Written by hand. `alembic check` sees neither change.
"""

from __future__ import annotations

from alembic import op

revision = "f2a4b8d6c135"
down_revision = "e7c95a2f1b08"
branch_labels = None
depends_on = None


def _valid_spec(subject_only: str) -> str:
    """The constraint, parameterised by which types are subject-only.

    Written once rather than twice so the upgrade and the downgrade cannot
    drift: the only difference between them is whether CERTIFICATION_PRESENT
    appears in that list.
    """
    return f"""
CASE
    WHEN spec_type IS NULL THEN
        subject IS NULL AND threshold_value IS NULL AND threshold_scale IS NULL
    WHEN spec_type::text IN ({subject_only}) THEN
        subject IS NOT NULL AND threshold_value IS NULL AND threshold_scale IS NULL
    WHEN spec_type::text = 'GPA_MIN' THEN
        subject IS NULL AND threshold_value IS NOT NULL
    WHEN spec_type::text = 'EXPERIENCE_MIN' THEN
        subject IS NULL AND threshold_value IS NOT NULL AND threshold_scale IS NULL
    WHEN spec_type::text = 'INTERNSHIP_MIN' THEN
        subject IS NULL AND threshold_scale IS NULL
    WHEN spec_type::text = 'EXPERIENCE_IN_FIELD' THEN
        subject IS NOT NULL AND threshold_value IS NOT NULL AND threshold_scale IS NULL
    ELSE FALSE
END
"""


_WITH_CERTIFICATION = "'EDUCATION_MIN', 'SKILL', 'LANGUAGE_PRESENT', 'CERTIFICATION_PRESENT'"
_WITHOUT_CERTIFICATION = "'EDUCATION_MIN', 'SKILL', 'LANGUAGE_PRESENT'"


def upgrade() -> None:
    op.execute(
        "ALTER TYPE requirement_spec_type ADD VALUE IF NOT EXISTS 'CERTIFICATION_PRESENT'"
    )
    op.drop_constraint("spec_is_complete", "requirement", type_="check")
    op.create_check_constraint(
        "spec_is_complete", "requirement", _valid_spec(_WITH_CERTIFICATION)
    )


def downgrade() -> None:
    # Rows of the new type have to go before the constraint that forbids them
    # comes back; there is no older shape for such a row to become. Safe only
    # because a downgrade also removes the ability to create one.
    op.execute("DELETE FROM requirement WHERE spec_type::text = 'CERTIFICATION_PRESENT'")

    op.drop_constraint("spec_is_complete", "requirement", type_="check")
    op.create_check_constraint(
        "spec_is_complete", "requirement", _valid_spec(_WITHOUT_CERTIFICATION)
    )

    # The enum value stays. PostgreSQL has no `ALTER TYPE ... DROP VALUE`, and
    # rebuilding the type would mean rewriting every requirement row. An unused
    # label is harmless, and a downgrade to base drops the type outright.
