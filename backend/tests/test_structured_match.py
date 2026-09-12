"""Structured requirement matching, end to end over invented CVs.

All names and employers here are fictional. The final section carries the
adversarial cases from the deterministic-engine benchmark forward as permanent
regression coverage: they are the mistakes that a keyword matcher makes and this
engine is built not to.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.enums import MatchVerdict
from app.services.cv_facts import extract_facts
from app.services.structured_match import (
    EDUCATION_MIN,
    EXPERIENCE_IN_FIELD,
    EXPERIENCE_MIN,
    GPA_MIN,
    INTERNSHIP_MIN,
    LANGUAGE_PRESENT,
    SKILL,
    RequirementSpec,
    _partial_floor,
    describe,
    match,
)

AS_OF = {"as_of_year": 2026, "as_of_month": 1}

FULL_CV = """Ada Lovelace
Senior Backend Engineer

EXPERIENCE
Northwind Analytics (fictional) - Backend Engineer, January 2023 - Present
Developed and operated REST services in production.
Cobalt Systems (fictional) - Software Engineering Intern, June 2022 - December 2022
Maintained reporting tools.

SKILLS
Python, FastAPI, Postgres, Docker, pytest

LANGUAGES
English, Indonesian

EDUCATION
BSc Computer Science, University of the Fictional Midlands, 2022
IPK: 3,62 / 4.00
"""


def verdict(spec: RequirementSpec, cv: str = FULL_CV) -> MatchVerdict:
    return match(spec, extract_facts(cv, **AS_OF)).verdict


def outcome(spec: RequirementSpec, cv: str = FULL_CV):
    return match(spec, extract_facts(cv, **AS_OF))


# --------------------------------------------------------------------------
# Education
# --------------------------------------------------------------------------


def test_a_bachelor_satisfies_an_s1_requirement() -> None:
    assert verdict(RequirementSpec(EDUCATION_MIN, subject="S1")) is MatchVerdict.MATCHED


def test_a_higher_degree_satisfies_a_lower_requirement() -> None:
    cv = "EDUCATION\nS2 Magister Informatika, 2023"
    assert verdict(RequirementSpec(EDUCATION_MIN, subject="S1"), cv) is MatchVerdict.MATCHED


def test_a_lower_degree_is_not_partial_evidence_of_a_higher_one() -> None:
    """A BSc does not part-satisfy a Master's; the document does not show one."""
    assert verdict(RequirementSpec(EDUCATION_MIN, subject="S2")) is MatchVerdict.NO_EVIDENCE


def test_no_qualification_is_no_evidence() -> None:
    cv = "SKILLS\nPython"
    assert verdict(RequirementSpec(EDUCATION_MIN, subject="S1"), cv) is MatchVerdict.NO_EVIDENCE


def test_an_unrecognised_degree_name_needs_review() -> None:
    assert verdict(RequirementSpec(EDUCATION_MIN, subject="Habilitation")) is (
        MatchVerdict.NEEDS_REVIEW
    )


# --------------------------------------------------------------------------
# GPA -- the canonical NEEDS_REVIEW territory
# --------------------------------------------------------------------------


def test_a_grade_above_the_minimum_matches() -> None:
    spec = RequirementSpec(GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00"))
    assert verdict(spec) is MatchVerdict.MATCHED


def test_a_grade_below_the_minimum_is_partial_not_absent() -> None:
    """The document does evidence a grade; it just falls short."""
    cv = "EDUCATION\nIPK: 2,80 / 4.00"
    spec = RequirementSpec(GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00"))
    assert verdict(spec, cv) is MatchVerdict.PARTIAL


def test_a_grade_without_a_stated_scale_needs_review() -> None:
    """3.2 is strong out of 4 and ordinary out of 5. Assuming is inventing."""
    cv = "EDUCATION\nIPK 3.20"
    spec = RequirementSpec(GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00"))
    result = outcome(spec, cv)
    assert result.verdict is MatchVerdict.NEEDS_REVIEW
    assert result.span is not None and "3.20" in result.span.text


def test_a_mismatched_scale_needs_review_rather_than_being_converted() -> None:
    cv = "EDUCATION\nGPA 3.9 / 5.0"
    spec = RequirementSpec(GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00"))
    assert verdict(spec, cv) is MatchVerdict.NEEDS_REVIEW


def test_a_cv_with_no_grade_is_no_evidence_not_review() -> None:
    cv = "EDUCATION\nBSc Computer Science, 2021"
    spec = RequirementSpec(GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00"))
    assert verdict(spec, cv) is MatchVerdict.NO_EVIDENCE


def test_a_minimum_with_no_scale_needs_review_rather_than_borrowing_the_cvs() -> None:
    """The other half of the same rule: the engine never assumes a scale.

    A pre-audit build read this case the wrong way round. With no scale on the
    criterion the comparison silently adopted whichever scale the CV stated, so
    a 3.2 out of 5 satisfied a minimum of 3.00 that was written thinking of 4 --
    over-crediting, which is the costlier direction. Both entry points refuse
    such a criterion at creation; this asserts the matcher refuses it too.
    """
    cv = "EDUCATION\nGPA: 3.2 / 5.0"
    spec = RequirementSpec(GPA_MIN, threshold_value=Decimal("3.00"))

    result = outcome(spec, cv)

    assert result.verdict is MatchVerdict.NEEDS_REVIEW
    assert "without the scale" in result.reason
    # And emphatically not the verdict the assumption produced.
    assert result.verdict is not MatchVerdict.MATCHED


# --------------------------------------------------------------------------
# Experience and internship
# --------------------------------------------------------------------------


def test_experience_above_the_minimum_matches() -> None:
    spec = RequirementSpec(EXPERIENCE_MIN, threshold_value=Decimal("24"))
    assert verdict(spec) is MatchVerdict.MATCHED


def test_experience_a_little_short_is_partial() -> None:
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer, January 2024 - January 2026"
    spec = RequirementSpec(EXPERIENCE_MIN, threshold_value=Decimal("36"))
    assert verdict(spec, cv) is MatchVerdict.PARTIAL


def test_experience_far_short_is_no_evidence() -> None:
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer, January 2025 - July 2025"
    spec = RequirementSpec(EXPERIENCE_MIN, threshold_value=Decimal("60"))
    assert verdict(spec, cv) is MatchVerdict.NO_EVIDENCE


def test_overlapping_roles_do_not_inflate_experience() -> None:
    cv = (
        "EXPERIENCE\n"
        "Northwind (fictional) - Engineer, January 2023 - December 2024\n"
        "Northwind (fictional) - Tech Lead, June 2024 - January 2025\n"
    )
    # 24 calendar months, not the 31 that summing both ranges would give.
    assert verdict(RequirementSpec(EXPERIENCE_MIN, threshold_value=Decimal("30")), cv) is (
        MatchVerdict.PARTIAL
    )


def test_internship_duration_matches() -> None:
    assert verdict(RequirementSpec(INTERNSHIP_MIN, threshold_value=Decimal("6"))) is (
        MatchVerdict.MATCHED
    )


def test_internship_presence_without_a_threshold_matches() -> None:
    assert verdict(RequirementSpec(INTERNSHIP_MIN)) is MatchVerdict.MATCHED


def test_no_internship_is_no_evidence() -> None:
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer, January 2023 - January 2025"
    assert verdict(RequirementSpec(INTERNSHIP_MIN), cv) is MatchVerdict.NO_EVIDENCE


# --------------------------------------------------------------------------
# Skills and languages
# --------------------------------------------------------------------------


def test_a_listed_skill_matches() -> None:
    assert verdict(RequirementSpec(SKILL, subject="Python")) is MatchVerdict.MATCHED


def test_a_skill_the_cv_does_not_mention_is_no_evidence() -> None:
    assert verdict(RequirementSpec(SKILL, subject="Kubernetes")) is MatchVerdict.NO_EVIDENCE


def test_a_sibling_technology_does_not_credit_the_requested_one() -> None:
    """Docker is not partial Kubernetes. Family adjacency is not evidence."""
    result = outcome(RequirementSpec(SKILL, subject="Kubernetes"))
    assert result.verdict is MatchVerdict.NO_EVIDENCE
    assert result.span is None


def test_an_unsupported_skill_needs_review_not_no_evidence() -> None:
    """Our vocabulary's gap must not be reported as the candidate's gap."""
    result = outcome(RequirementSpec(SKILL, subject="Svelte"))
    assert result.verdict is MatchVerdict.NEEDS_REVIEW
    assert "evaluate" in result.reason


def test_a_named_language_matches() -> None:
    assert verdict(RequirementSpec(LANGUAGE_PRESENT, subject="English")) is MatchVerdict.MATCHED


def test_an_unnamed_language_is_no_evidence() -> None:
    assert verdict(RequirementSpec(LANGUAGE_PRESENT, subject="Japanese")) is (
        MatchVerdict.NO_EVIDENCE
    )


def test_an_unsupported_language_needs_review() -> None:
    assert verdict(RequirementSpec(LANGUAGE_PRESENT, subject="Klingon")) is (
        MatchVerdict.NEEDS_REVIEW
    )


# --------------------------------------------------------------------------
# Evidence contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        RequirementSpec(SKILL, subject="Python"),
        RequirementSpec(EDUCATION_MIN, subject="S1"),
        RequirementSpec(EXPERIENCE_MIN, threshold_value=Decimal("24")),
        RequirementSpec(INTERNSHIP_MIN, threshold_value=Decimal("6")),
        RequirementSpec(LANGUAGE_PRESENT, subject="English"),
        RequirementSpec(GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00")),
    ],
)
def test_every_positive_verdict_quotes_a_real_line_of_the_cv(spec: RequirementSpec) -> None:
    result = outcome(spec)
    assert result.verdict is MatchVerdict.MATCHED
    assert result.span is not None
    assert result.span.text in FULL_CV


def test_no_evidence_never_carries_a_quote() -> None:
    result = outcome(RequirementSpec(SKILL, subject="Kubernetes"))
    assert result.verdict is MatchVerdict.NO_EVIDENCE
    assert result.span is None


def test_reasons_describe_the_document_not_the_person() -> None:
    """Absence is a fact about a CV, never a claim about a candidate."""
    result = outcome(RequirementSpec(SKILL, subject="Kubernetes"))
    assert result.reason.startswith("The CV")
    assert "candidate" not in result.reason.lower()


def test_matching_is_repeatable() -> None:
    spec = RequirementSpec(SKILL, subject="Python")
    first, second = outcome(spec), outcome(spec)
    assert (first.verdict, first.span) == (second.verdict, second.span)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (RequirementSpec(EDUCATION_MIN, subject="S1"), "Minimum degree: S1"),
        (
            RequirementSpec(GPA_MIN, threshold_value=Decimal("3.00"), scale=Decimal("4.00")),
            "Minimum GPA: 3.00 / 4.00",
        ),
        (
            RequirementSpec(EXPERIENCE_MIN, threshold_value=Decimal("24")),
            "At least 2 years of professional experience",
        ),
        (
            RequirementSpec(INTERNSHIP_MIN, threshold_value=Decimal("6")),
            "At least 6 months of internship experience",
        ),
        (RequirementSpec(SKILL, subject="Python"), "Skill: Python"),
        (RequirementSpec(LANGUAGE_PRESENT, subject="English"), "Language: English"),
    ],
)
def test_specs_render_for_display(spec: RequirementSpec, expected: str) -> None:
    assert describe(spec) == expected


def test_an_unknown_spec_type_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        RequirementSpec("SALARY_MAX")


# --------------------------------------------------------------------------
# Regression: the adversarial cases the deterministic engine was built against
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cv", "skill", "expected"),
    [
        ("EXPERIENCE\nI have no Python experience.", "Python", MatchVerdict.NO_EVIDENCE),
        ("EXPERIENCE\nPython is required for this role.", "Python", MatchVerdict.NO_EVIDENCE),
        ("EXPERIENCE\nLooking for Python developers.", "Python", MatchVerdict.NO_EVIDENCE),
        (
            "EXPERIENCE\nDeveloped backend services in Python for three years.",
            "Python",
            MatchVerdict.MATCHED,
        ),
        ("SKILLS\nFamiliar with Python", "Python", MatchVerdict.PARTIAL),
        ("SKILLS\nR, Go, Python", "Go", MatchVerdict.MATCHED),
        ("EXPERIENCE\nAsked to go deep on latency problems.", "Go", MatchVerdict.NO_EVIDENCE),
        ("PENGALAMAN\nBelum pernah memakai Docker.", "Docker", MatchVerdict.NO_EVIDENCE),
        (
            "PENGALAMAN\nMembangun layanan backend dengan Python selama 3 tahun.",
            "Python",
            MatchVerdict.MATCHED,
        ),
        ("SKILLS\nExcel, SQL, Tableau", "Excel", MatchVerdict.MATCHED),
    ],
)
def test_benchmark_adversarial_cases(cv: str, skill: str, expected: MatchVerdict) -> None:
    assert verdict(RequirementSpec(SKILL, subject=skill), cv) is expected


def test_ability_to_excel_is_not_the_spreadsheet() -> None:
    """The recruiter picks Excel from a list, so the collision can only come
    from the CV side -- an ordinary verb must not become a product mention."""
    cv = "EXPERIENCE\nAsked to excel under pressure in a fast-moving team."
    assert verdict(RequirementSpec(SKILL, subject="Excel"), cv) is MatchVerdict.NO_EVIDENCE


# --------------------------------------------------------------------------
# A short minimum is a real minimum
# --------------------------------------------------------------------------

SHORT_JOB_CV = "PENGALAMAN\nKasir di toko Fiktif Jaya (Juni-July 2024)\n"
NO_JOB_CV = "PENDIDIKAN\nSMA Negeri Fiktif 1, 2024\n"


def test_a_one_month_minimum_is_not_satisfied_by_nothing() -> None:
    """The partial band used to start at 0.6 months, which nothing can fail.

    A minimum of one month is a legitimate thing to ask a school-leaver for,
    and it has to mean something: either the CV shows a month of dated work or
    it does not.
    """
    spec = RequirementSpec(EXPERIENCE_MIN, threshold_value=Decimal("1"))
    assert verdict(spec, NO_JOB_CV) is MatchVerdict.NO_EVIDENCE
    assert verdict(spec, SHORT_JOB_CV) is MatchVerdict.MATCHED


def test_a_short_minimum_has_no_partial_band_at_all() -> None:
    """There is no sensible "nearly" below one or two months."""
    for required in ("1", "2"):
        assert _partial_floor(Decimal(required)) is None


@pytest.mark.parametrize(
    ("required", "floor"),
    [("3", 2), ("6", 4), ("12", 8), ("24", 15), ("48", 29)],
)
def test_the_partial_band_is_whole_months(required: str, floor: int) -> None:
    """A fraction of a month is not something a CV states or a recruiter means."""
    assert _partial_floor(Decimal(required)) == floor


# --------------------------------------------------------------------------
# Experience in a field: the criterion EXPERIENCE_MIN could not express
# --------------------------------------------------------------------------

#: Two dated roles: one plainly accounting, one plainly not. Invented.
MIXED_CAREER_CV = """PENGALAMAN
PT Fiktif Sejahtera - Staff Accounting, January 2022 - December 2023
Membuat jurnal umum dan melakukan rekonsiliasi bank setiap bulan.

Toko Fiktif Jaya - Kasir, January 2024 - December 2024
Melayani pembelian dan menghitung setoran harian.
"""


def field(subject: str, months: str) -> RequirementSpec:
    return RequirementSpec(EXPERIENCE_IN_FIELD, subject=subject, threshold_value=Decimal(months))


def test_unrelated_experience_does_not_answer_a_field_criterion() -> None:
    """The whole reason this type exists.

    The CV holds 36 months of dated work, of which 24 are accounting and 12
    are retail. A plain duration criterion cannot tell those apart; this one
    counts only the entry that evidences the field.
    """
    assert (
        verdict(RequirementSpec(EXPERIENCE_MIN, threshold_value=Decimal("36")), MIXED_CAREER_CV)
        is MatchVerdict.MATCHED
    )
    assert verdict(field("Accounting", "36"), MIXED_CAREER_CV) is MatchVerdict.PARTIAL


def test_the_months_counted_are_only_the_in_field_ones() -> None:
    result = outcome(field("Accounting", "24"), MIXED_CAREER_CV)
    assert result.verdict is MatchVerdict.MATCHED
    assert "24 months of Accounting experience" in result.reason


def test_a_field_never_worked_in_is_absence_not_uncertainty() -> None:
    """The CV has dated work, just none in this field. That is a real answer."""
    result = outcome(field("Python", "12"), MIXED_CAREER_CV)
    assert result.verdict is MatchVerdict.NO_EVIDENCE
    assert "dated experience in Python" in result.reason


def test_no_dated_work_at_all_is_absence_of_experience() -> None:
    cv = "SKILLS\nAkuntansi, Pembukuan\n"
    result = outcome(field("Accounting", "12"), cv)
    assert result.verdict is MatchVerdict.NO_EVIDENCE
    assert "dated professional experience" in result.reason


def test_an_unsupported_field_needs_review_rather_than_reporting_absence() -> None:
    """Our vocabulary's limit is not the candidate's gap (ADR-0012)."""
    result = outcome(field("Blockchain", "12"), MIXED_CAREER_CV)
    assert result.verdict is MatchVerdict.NEEDS_REVIEW
    assert "cannot be measured" in result.reason


def test_the_evidence_cites_an_entry_from_the_field() -> None:
    result = outcome(field("Accounting", "24"), MIXED_CAREER_CV)
    assert result.span is not None
    assert "Staff Accounting" in result.span.text


def test_a_field_claimed_in_a_bullet_rather_than_the_title_still_counts() -> None:
    """A CV names the field underneath the title far more often than in it."""
    cv = (
        "PENGALAMAN\n"
        "PT Fiktif Nusantara - Staff Administrasi, January 2022 - December 2023\n"
        "Menyusun laporan keuangan bulanan dan melakukan rekonsiliasi bank.\n"
    )
    assert verdict(field("Financial Reporting", "12"), cv) is MatchVerdict.MATCHED


def test_a_denial_inside_the_entry_does_not_credit_the_field() -> None:
    """Inherits every gate `extract_skills` applies, rather than keyword search."""
    cv = (
        "PENGALAMAN\n"
        "Toko Fiktif Jaya - Kasir, January 2022 - December 2023\n"
        "Belum pernah menangani akuntansi atau perpajakan.\n"
    )
    assert verdict(field("Accounting", "12"), cv) is MatchVerdict.NO_EVIDENCE


def test_a_field_criterion_renders_for_display() -> None:
    assert describe(field("Accounting", "24")) == "At least 2 years of Accounting experience"


def test_a_skill_listed_but_not_tied_to_a_role_is_unresolved_not_absent() -> None:
    """The shape most CVs actually have, and the distinction that matters.

    A CV listing Python under SKILLS and four years of dated roles shows both
    halves without joining them: it never says which role used Python. Calling
    that "no evidence of Python experience" would report a gap in the CV's
    formatting as a gap in the candidate. Calling it MATCHED would invent the
    connection. Unresolved is the only honest answer, and it keeps the
    criterion out of the score rather than counting it as a zero.
    """
    cv = (
        "EXPERIENCE\n"
        "Northwind (fictional) - Backend Engineer, January 2022 - December 2025\n"
        "Designed and operated REST services in production.\n"
        "\n"
        "SKILLS\n"
        "Python, Docker\n"
    )
    result = outcome(field("Python", "24"), cv)

    assert result.verdict is MatchVerdict.NEEDS_REVIEW
    assert "does not say which role used it" in result.reason
    # It still points at the line that made the claim.
    assert result.span is not None and "Python" in result.span.text


def test_a_skill_absent_from_the_whole_cv_is_still_absence() -> None:
    """The other half: nothing to resolve means the plain answer is absence."""
    cv = (
        "EXPERIENCE\n"
        "Northwind (fictional) - Backend Engineer, January 2022 - December 2025\n"
        "Designed and operated REST services in production.\n"
        "\n"
        "SKILLS\n"
        "Excel, SQL\n"
    )
    result = outcome(field("Python", "24"), cv)

    assert result.verdict is MatchVerdict.NO_EVIDENCE
    assert "dated experience in Python" in result.reason
