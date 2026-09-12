"""A seventh criterion type: experience in a field.

Revision ID: e7c95a2f1b08
Revises: d4e81c30ab97

Why this exists. `EXPERIENCE_MIN` asks only how long somebody has worked, so on
an accounting vacancy four years of retail answered it exactly as well as four
years of accounting. `EXPERIENCE_IN_FIELD` restricts the same arithmetic to the
dated roles whose own CV entry evidences a supported skill.

Two changes:

1. **One new enum value** on `requirement_spec_type`.

2. **A new branch in `spec_is_complete`.** This is the first criterion type that
   carries a subject *and* a threshold, and the existing constraint was written
   when no type did: every branch demanded one or the other and forbade the
   pair, so a well-formed row of the new type would have been refused outright.
   The constraint is dropped and recreated rather than amended, because
   PostgreSQL has no ALTER for a CHECK expression.

Written by hand. `alembic check` sees neither change: it does not compare the
values of a PostgreSQL enum against the Python one, and it does not read a CHECK
constraint expressed as text.

On `ALTER TYPE ... ADD VALUE` inside a transaction: PostgreSQL 12 and later
allow it provided the value is not *used* in the same transaction. The
constraint below names it inside a string compared against `spec_type::text`,
never as an enum literal, so nothing here uses it.
"""

from __future__ import annotations

from alembic import op

revision = "e7c95a2f1b08"
down_revision = "d4e81c30ab97"
branch_labels = None
depends_on = None


#: A row is either fully structured or fully legacy. Identical to the constraint
#: `d4e81c30ab97` created, plus the EXPERIENCE_IN_FIELD branch.
#:
#: `spec_type` is cast to text throughout so the branch naming the value added
#: above is legal in this same transaction. CASE rather than a chain of ORs for
#: the reason the previous migration records: `spec_type IN (...)` is NULL when
#: `spec_type` is NULL, and PostgreSQL accepts a CHECK that evaluates to NULL.
_VALID_SPEC = """
CASE
    WHEN spec_type IS NULL THEN
        subject IS NULL AND threshold_value IS NULL AND threshold_scale IS NULL
    WHEN spec_type::text IN ('EDUCATION_MIN', 'SKILL', 'LANGUAGE_PRESENT') THEN
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

#: The constraint as `d4e81c30ab97` left it, for the downgrade.
_VALID_SPEC_WITHOUT_FIELD = """
CASE
    WHEN spec_type IS NULL THEN
        subject IS NULL AND threshold_value IS NULL AND threshold_scale IS NULL
    WHEN spec_type::text IN ('EDUCATION_MIN', 'SKILL', 'LANGUAGE_PRESENT') THEN
        subject IS NOT NULL AND threshold_value IS NULL AND threshold_scale IS NULL
    WHEN spec_type::text = 'GPA_MIN' THEN
        subject IS NULL AND threshold_value IS NOT NULL
    WHEN spec_type::text = 'EXPERIENCE_MIN' THEN
        subject IS NULL AND threshold_value IS NOT NULL AND threshold_scale IS NULL
    WHEN spec_type::text = 'INTERNSHIP_MIN' THEN
        subject IS NULL AND threshold_scale IS NULL
    ELSE FALSE
END
"""


def upgrade() -> None:
    op.execute("ALTER TYPE requirement_spec_type ADD VALUE IF NOT EXISTS 'EXPERIENCE_IN_FIELD'")
    op.drop_constraint("spec_is_complete", "requirement", type_="check")
    op.create_check_constraint("spec_is_complete", "requirement", _VALID_SPEC)


def downgrade() -> None:
    # Any row of the new type has to go before the constraint that forbids it
    # is restored, and deleting one is the only option: there is no older shape
    # for it to become. Safe in this direction only because a downgrade also
    # removes the ability to create one.
    op.execute("DELETE FROM requirement WHERE spec_type::text = 'EXPERIENCE_IN_FIELD'")

    op.drop_constraint("spec_is_complete", "requirement", type_="check")
    op.create_check_constraint("spec_is_complete", "requirement", _VALID_SPEC_WITHOUT_FIELD)

    # The enum value itself stays: PostgreSQL has no `ALTER TYPE ... DROP
    # VALUE`, and rebuilding the type would mean rewriting every requirement
    # row. An unused label is harmless, and a downgrade to base drops the type
    # outright, so the round trip CI exercises still ends with a clean schema.
