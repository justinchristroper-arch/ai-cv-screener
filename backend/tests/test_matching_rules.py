"""The deterministic half of matching, tested as the pure functions it is.

Everything here runs with no database, no network and no clock: the duration
arithmetic takes its reference date as an argument precisely so that it stays a
pure function of its inputs and a test can pin it. That is what
docs/architecture.md section 4.1 means by "makes the easy cases perfectly
reproducible".
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.enums import DatePrecision
from app.models.profile import ProfileExperience
from app.services.matching import (
    describe_months,
    normalize_skill_name,
    parse_required_months,
    skill_alias_forms,
    total_experience_months,
)

ALIASES = {
    "postgres": "postgresql",
    "psql": "postgresql",
    "postgre": "postgresql",
    "k8s": "kubernetes",
    "js": "javascript",
}


# --------------------------------------------------------------------------
# Skill normalization
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PostgreSQL", "postgresql"),
        ("  Python  ", "python"),
        ("REST APIs", "rest apis"),
        ("Node.js", "node.js"),
        ("C++", "c++"),
        ("C#", "c#"),
        (".NET", ".net"),
        ("CI/CD", "ci cd"),
        ("Amazon Web Services (AWS)", "amazon web services aws"),
        ("machine-learning", "machine-learning"),
    ],
)
def test_normalization_keeps_what_distinguishes_a_skill(raw: str, expected: str) -> None:
    """`+`, `#` and `.` survive on purpose.

    Stripping them would turn "C++", "C#" and "Node.js" into "c", "c" and
    "node js" -- collapsing three different skills into two wrong ones.
    """
    assert normalize_skill_name(raw) == expected


def test_normalization_is_idempotent() -> None:
    once = normalize_skill_name("  Node.JS / TypeScript  ")
    assert normalize_skill_name(once) == once


# --------------------------------------------------------------------------
# Aliases
# --------------------------------------------------------------------------


def test_an_alias_resolves_to_its_canonical_name() -> None:
    """CV says "Postgres", the job description says "PostgreSQL"."""
    assert "postgresql" in skill_alias_forms("postgres", ALIASES)


def test_a_canonical_name_resolves_back_to_its_aliases() -> None:
    """CV says "PostgreSQL", the job description says "Postgres"."""
    forms = skill_alias_forms("postgresql", ALIASES)

    assert "postgres" in forms
    assert "psql" in forms


def test_two_aliases_of_the_same_skill_reach_each_other() -> None:
    assert "psql" in skill_alias_forms("postgres", ALIASES)


def test_a_skill_never_lists_itself_as_an_alias() -> None:
    assert "postgres" not in skill_alias_forms("postgres", ALIASES)


def test_an_unknown_skill_has_no_alias_forms() -> None:
    assert skill_alias_forms("elixir", ALIASES) == set()


# --------------------------------------------------------------------------
# Duration parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("At least 5 years of professional experience building backend services", 60),
        ("5+ years of backend engineering", 60),
        ("Minimum of 3 years in a data role", 36),
        ("2 yrs of production experience", 24),
        ("18 months of relevant experience", 18),
        ("3-5 years of experience with distributed systems", 36),
        ("3 to 5 years of experience", 36),
    ],
)
def test_a_stated_minimum_duration_is_read_from_the_requirement(text: str, expected: int) -> None:
    assert parse_required_months(text) == expected


def test_a_range_takes_its_lower_bound() -> None:
    """ "3-5 years" is met at three. Reading it as five invents a stricter job."""
    assert parse_required_months("3-5 years of experience") == 36


@pytest.mark.parametrize(
    "text",
    [
        "Strong experience with PostgreSQL",
        "Practical experience designing and operating REST APIs in production",
        "Bachelor's degree in Computer Science",
    ],
)
def test_a_requirement_without_a_duration_yields_none(text: str) -> None:
    """No duration means the duration matcher does not apply at all."""
    assert parse_required_months(text) is None


# --------------------------------------------------------------------------
# Experience arithmetic
# --------------------------------------------------------------------------


def _role(start: date | None, end: date | None, **kwargs) -> ProfileExperience:
    return ProfileExperience(
        role_title=kwargs.pop("role_title", "Engineer"),
        start_date=start,
        end_date=end,
        date_precision=kwargs.pop("date_precision", DatePrecision.MONTH),
        is_current=kwargs.pop("is_current", False),
        **kwargs,
    )


AS_OF = date(2026, 9, 1)


def test_consecutive_roles_add_up() -> None:
    roles = [
        _role(date(2021, 6, 1), date(2022, 2, 1)),
        _role(date(2022, 3, 1), date(2026, 2, 1)),
    ]

    total, imprecise = total_experience_months(roles, as_of=AS_OF)

    assert total == 8 + 47
    assert not imprecise


def test_overlapping_roles_are_merged_not_summed() -> None:
    """Two jobs held at once are not twice the experience."""
    roles = [
        _role(date(2020, 1, 1), date(2023, 1, 1)),
        _role(date(2021, 1, 1), date(2022, 1, 1)),
    ]

    total, _ = total_experience_months(roles, as_of=AS_OF)

    assert total == 36


def test_an_ongoing_role_is_measured_to_the_reference_date() -> None:
    roles = [_role(date(2024, 9, 1), None, is_current=True)]

    total, _ = total_experience_months(roles, as_of=AS_OF)

    assert total == 24


def test_a_role_with_no_start_date_contributes_nothing() -> None:
    """A role the CV never dated cannot be counted, in either direction."""
    roles = [_role(None, date(2024, 1, 1))]

    assert total_experience_months(roles, as_of=AS_OF) == (0, False)


def test_year_precision_is_reported_so_the_matcher_can_hedge() -> None:
    """ "2019-2024" is somewhere between four and six years, and says so."""
    roles = [
        _role(date(2019, 1, 1), date(2024, 1, 1), date_precision=DatePrecision.YEAR),
    ]

    total, imprecise = total_experience_months(roles, as_of=AS_OF)

    assert total == 60
    assert imprecise


def test_no_roles_at_all_is_zero_and_precise() -> None:
    assert total_experience_months([], as_of=AS_OF) == (0, False)


@pytest.mark.parametrize(
    ("months", "expected"),
    [(0, "0 months"), (1, "1 month"), (12, "1 year"), (55, "4 years 7 months"), (60, "5 years")],
)
def test_durations_are_described_the_way_a_recruiter_would_check_them(
    months: int, expected: str
) -> None:
    assert describe_months(months) == expected
