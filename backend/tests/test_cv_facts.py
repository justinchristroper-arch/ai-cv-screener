"""Deterministic CV fact extraction.

Every CV in this file is invented. The names are fictional, the employers are
marked fictional, and none of it comes from a real applicant (ADR-0003 and the
repository's standing rule about real documents).

The cases are organised by the mistake they prevent rather than by function,
because the value of this module is entirely in what it *refuses* to read as
evidence.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.cv_facts import _sections, extract_facts, normalize_text, total_months

AS_OF = {"as_of_year": 2026, "as_of_month": 1}


def facts(text: str):
    return extract_facts(text, **AS_OF)


def skill_names(text: str) -> set[str]:
    return {item.name for item in facts(text).skills}


# --------------------------------------------------------------------------
# Skills: the mention is only evidence when the sentence supports it
# --------------------------------------------------------------------------


def test_a_skill_listed_under_skills_is_evidence() -> None:
    assert "Python" in skill_names("SKILLS\nPython, FastAPI, Postgres")


def test_a_denied_skill_is_not_evidence() -> None:
    """ "I have no Python experience" must never credit Python."""
    assert "Python" not in skill_names("EXPERIENCE\nI have no Python experience.")


def test_an_indonesian_denial_is_not_evidence() -> None:
    assert "Docker" not in skill_names("PENGALAMAN\nBelum pernah memakai Docker.")


def test_advert_text_pasted_into_a_cv_is_not_evidence() -> None:
    """A CV quoting the job advert would otherwise match every requirement."""
    assert "Python" not in skill_names("EXPERIENCE\nPython is required for this role.")


def test_a_job_posting_line_is_not_evidence() -> None:
    assert "Python" not in skill_names("EXPERIENCE\nLooking for Python developers.")


def test_a_doing_verb_makes_a_mention_evidence() -> None:
    assert "Python" in skill_names("EXPERIENCE\nDeveloped document-processing services in Python.")


def test_indonesian_doing_verb_makes_a_mention_evidence() -> None:
    assert "Python" in skill_names(
        "PENGALAMAN\nMembangun layanan backend dengan Python selama tiga tahun."
    )


def test_exposure_is_recorded_but_not_substantive() -> None:
    """ "Familiar with" is a real thing the CV says -- just not the same thing."""
    found = [item for item in facts("SKILLS\nFamiliar with Python").skills if item.name == "Python"]
    assert found and found[0].substantive is False


def test_a_course_is_not_substantive_evidence() -> None:
    found = [
        item
        for item in facts("EDUCATION\nCompleted a Python course in 2024.").skills
        if item.name == "Python"
    ]
    assert all(item.substantive is False for item in found)


# --------------------------------------------------------------------------
# Skills: short and common-word names
# --------------------------------------------------------------------------


def test_a_two_letter_skill_counts_inside_a_skills_list() -> None:
    assert "Go" in skill_names("SKILLS\nR, Go, Python")


def test_ordinary_english_go_is_not_the_language() -> None:
    """The word "go" appears constantly in prose; the language rarely does."""
    assert "Go" not in skill_names("EXPERIENCE\nAsked to go deep on latency problems.")


def test_single_letter_skills_count_inside_a_skills_list() -> None:
    names = skill_names("SKILLS\nR, C, Python")
    assert {"R", "C"} <= names


def test_a_skill_mentioned_only_in_a_summary_paragraph_needs_a_verb() -> None:
    """Prose of unknown subject is not a claim about this person."""
    assert "Kubernetes" not in skill_names(
        "Backend engineer interested in Kubernetes and distributed systems."
    )


def test_evidence_span_is_a_real_line_of_the_cv() -> None:
    cv = "SKILLS\nPython, FastAPI, Postgres"
    for item in facts(cv).skills:
        assert item.span.text in cv


# --------------------------------------------------------------------------
# Education
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "level"),
    [
        ("EDUCATION\nS1 Teknik Informatika, 2021", 3),
        ("EDUCATION\nBachelor of Computer Science, 2021", 3),
        ("EDUCATION\nBSc Computer Science, 2021", 3),
        ("EDUCATION\nSarjana Komputer, 2021", 3),
        ("EDUCATION\nS2 Magister Informatika, 2023", 4),
        ("EDUCATION\nMaster of Science in Data Science, 2023", 4),
        ("EDUCATION\nS3 Doktor Ilmu Komputer, 2026", 5),
        ("EDUCATION\nD3 Manajemen Informatika, 2019", 2),
    ],
)
def test_degree_levels_in_both_languages(line: str, level: int) -> None:
    highest = facts(line).highest_education
    assert highest is not None and highest.level == level


def test_a_required_degree_is_not_the_candidates_degree() -> None:
    assert facts("EXPERIENCE\nBachelor's degree required for this role.").education == []


def test_a_degree_in_progress_is_not_a_degree_held() -> None:
    """Reading towards a degree is a different fact from holding one."""
    assert (
        facts("EDUCATION\nCurrently pursuing a Bachelor's degree in Informatics.").education == []
    )


def test_the_highest_degree_wins() -> None:
    cv = "EDUCATION\nS1 Teknik Informatika, 2019\nS2 Magister Informatika, 2022"
    highest = facts(cv).highest_education
    assert highest is not None and highest.level == 4


# --------------------------------------------------------------------------
# GPA
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "value", "scale"),
    [
        ("EDUCATION\nIPK 3.62", Decimal("3.62"), None),
        ("EDUCATION\nIPK: 3,62", Decimal("3.62"), None),
        ("EDUCATION\nGPA: 3.8/4.0", Decimal("3.8"), Decimal("4.0")),
        ("EDUCATION\nGPA 3.9 / 5.0", Decimal("3.9"), Decimal("5.0")),
        ("EDUCATION\nCumulative GPA 3.45", Decimal("3.45"), None),
    ],
)
def test_gpa_forms(line: str, value: Decimal, scale: Decimal | None) -> None:
    found = facts(line).gpa
    assert len(found) == 1
    assert found[0].value == value
    assert found[0].scale == scale


@pytest.mark.parametrize(
    "line",
    [
        "SKILLS\nPython 3.12, Django 5.0",
        "EXPERIENCE\nShipped version 3.5 of the platform.",
        "EXPERIENCE\nMaintained a 4.5 star rating.",
        "EXPERIENCE\nReduced latency to 3.2 seconds.",
    ],
)
def test_bare_decimals_are_never_read_as_a_grade(line: str) -> None:
    """A grade is only read where the document says it is one."""
    assert facts(line).gpa == []


def test_cum_laude_without_a_number_invents_no_grade() -> None:
    assert facts("EDUCATION\nGraduated cum laude, 2021").gpa == []


def test_gpa_evidence_span_is_the_source_line() -> None:
    cv = "EDUCATION\nIPK: 3,62 / 4.00"
    found = facts(cv).gpa
    assert found[0].span.text == "IPK: 3,62 / 4.00"


# --------------------------------------------------------------------------
# Experience: durations and the overlap bug
# --------------------------------------------------------------------------


def test_a_dated_role_yields_its_length_in_months() -> None:
    """Both endpoints count: January 2023 through January 2025 is 25 months.

    This expected 24 until an audit traced the same off-by-one to a real
    failure: a one-month job counted as zero, so a criterion of "at least 1
    month" reported no evidence against a CV that plainly stated a month of
    work. Somebody who worked January through December worked twelve months.
    """
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer, January 2023 - January 2025"
    assert total_months(facts(cv).roles) == 25


def test_present_is_measured_against_the_supplied_date_not_a_clock() -> None:
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer, January 2024 - Present"
    # January 2024 through the supplied `as_of` of January 2026, inclusive.
    assert total_months(facts(cv).roles) == 25


def test_overlapping_roles_are_not_double_counted() -> None:
    """A promotion or a concurrent contract must not double a career.

    Jan 2023 - Dec 2024 and Jun 2024 - Jan 2025 cover 25 calendar months
    between them, not the 33 that summing the two ranges would give.
    """
    cv = (
        "EXPERIENCE\n"
        "Northwind (fictional) - Engineer, January 2023 - December 2024\n"
        "Northwind (fictional) - Tech Lead, June 2024 - January 2025\n"
    )
    assert total_months(facts(cv).roles) == 25


def test_separate_roles_add_up() -> None:
    cv = (
        "EXPERIENCE\n"
        "Northwind (fictional) - Engineer, January 2020 - January 2021\n"
        "Cobalt (fictional) - Engineer, January 2023 - January 2024\n"
    )
    # Two non-overlapping spells of 13 months each, both endpoints counted.
    assert total_months(facts(cv).roles) == 26


def test_an_undated_role_contributes_nothing() -> None:
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer\nBuilt services."
    assert total_months(facts(cv).roles) == 0


def test_a_reversed_range_is_ignored_rather_than_counted_negative() -> None:
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer, January 2025 - January 2023"
    assert facts(cv).roles == []


def test_dates_outside_the_experience_section_are_not_roles() -> None:
    cv = "EDUCATION\nBSc Computer Science, 2018 - 2022"
    assert facts(cv).roles == []


# --------------------------------------------------------------------------
# Internship
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineering Intern",
        "Backend Internship",
        "Engineering Trainee",
        "Apprentice Developer",
        "Magang Backend Engineer",
    ],
)
def test_internship_titles_are_recognised(title: str) -> None:
    cv = f"EXPERIENCE\nNorthwind (fictional) - {title}, June 2022 - December 2022"
    roles = facts(cv).roles
    assert roles and roles[0].is_internship


def test_internal_tooling_is_not_an_internship() -> None:
    """The regression this rule exists for: "internal" contains "intern"."""
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer, June 2022 - December 2022\n"
    assert facts(cv).roles[0].is_internship is False

    cv2 = (
        "EXPERIENCE\n"
        "Northwind (fictional) - Engineer, June 2022 - December 2022, internal tooling\n"
    )
    assert facts(cv2).roles[0].is_internship is False


def test_an_internship_is_not_inferred_from_seniority() -> None:
    cv = "EXPERIENCE\nNorthwind (fictional) - Junior Developer, June 2022 - December 2022"
    assert facts(cv).roles[0].is_internship is False


def test_internship_duration_is_measured_like_any_other_role() -> None:
    cv = (
        "EXPERIENCE\nNorthwind (fictional) - Software Engineering Intern, June 2022 - December 2022"
    )
    internships = [role for role in facts(cv).roles if role.is_internship]
    # June through December inclusive.
    assert total_months(internships) == 7


# --------------------------------------------------------------------------
# Languages: presence only
# --------------------------------------------------------------------------


def test_a_language_section_entry_is_evidence() -> None:
    assert {item.name for item in facts("LANGUAGES\nEnglish, Indonesian").languages} == {
        "English",
        "Indonesian",
    }


def test_indonesian_endonym_is_recognised() -> None:
    names = {item.name for item in facts("BAHASA\nBahasa Inggris, Bahasa Indonesia").languages}
    assert "English" in names


def test_a_language_named_with_a_test_score_is_evidence() -> None:
    names = {item.name for item in facts("EDUCATION\nTOEFL iBT 98 (English)").languages}
    assert "English" in names


def test_english_premier_league_is_not_a_language_claim() -> None:
    """The demonym appears in ordinary prose far more often than as a skill."""
    cv = (
        "EXPERIENCE\nBuilt a statistics platform covering the English Premier "
        "League and several other competitions."
    )
    assert facts(cv).languages == []


def test_a_denied_language_is_not_evidence() -> None:
    assert facts("LANGUAGES\nNo Japanese.").languages == []


def test_no_proficiency_level_is_ever_recorded() -> None:
    """Presence only. A level is not deterministically readable (ADR-0012)."""
    found = facts("LANGUAGES\nEnglish - Fluent").languages
    assert found and not hasattr(found[0], "level")


# --------------------------------------------------------------------------
# A nationality is not a language, and its line must never become evidence
# --------------------------------------------------------------------------

#: Indonesian CVs commonly print exactly this block. "Indonesian" is both a
#: language name and a nationality, and these lines are comma-separated, which
#: is what a pre-audit build mistook for a language list.
PERSONAL_DETAILS = [
    "Nationality: Indonesian, Religion: Islam",
    "Nationality: Indonesian, Gender: Female",
    "Kewarganegaraan: Indonesia, Agama: Islam",
    "Date of birth: 14 March 1994, Nationality: Indonesian",
]


@pytest.mark.parametrize("line", PERSONAL_DETAILS)
def test_a_personal_details_line_is_not_a_language_claim(line: str) -> None:
    assert facts(f"PERSONAL DETAILS\n{line}\n").languages == []


@pytest.mark.parametrize("line", PERSONAL_DETAILS)
def test_no_span_anywhere_quotes_a_protected_characteristic(line: str) -> None:
    """The reason this matters, stated as the invariant rather than the symptom.

    A span becomes the quotation displayed beside a verdict and stored in
    `evidence_span`. A span quoting someone's religion, gender or nationality
    would put a protected characteristic into the screening record, which
    ADR-0003 excludes by construction rather than by prompt.
    """
    result = facts(f"PERSONAL DETAILS\n{line}\n\nSKILLS\nPython, Docker\n")

    spans = [
        item.span.text
        for group in (result.skills, result.languages, result.education, result.gpa, result.roles)
        for item in group
    ]
    for text in spans:
        assert line not in text, f"a span quoted a personal-details line: {text!r}"


def test_the_guard_does_not_suppress_a_genuine_language_line() -> None:
    """The fix must refuse the personal-details line and nothing else."""
    cv = (
        "PERSONAL DETAILS\nNationality: Indonesian, Gender: Female\n\n"
        "LANGUAGES\nEnglish, Indonesian\n"
    )

    found = facts(cv)

    assert {item.name for item in found.languages} == {"English", "Indonesian"}
    assert all("Nationality" not in item.span.text for item in found.languages)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_extraction_is_repeatable() -> None:
    cv = (
        "Ada Lovelace\nEXPERIENCE\n"
        "Northwind (fictional) - Engineer, January 2023 - Present\n"
        "Developed services in Python.\n"
        "SKILLS\nPython, Docker\nEDUCATION\nS1 Informatika, IPK 3,40\n"
        "LANGUAGES\nEnglish\n"
    )
    first = facts(cv)
    second = facts(cv)
    assert [(s.name, s.span.start) for s in first.skills] == [
        (s.name, s.span.start) for s in second.skills
    ]
    assert [(g.value, g.scale) for g in first.gpa] == [(g.value, g.scale) for g in second.gpa]
    assert total_months(first.roles) == total_months(second.roles)


def test_a_candidates_name_never_becomes_a_fact() -> None:
    """Sensitive and identifying material is not part of screening (ADR-0003)."""
    cv = "Ada Lovelace\nFemale, 29, Jakarta\nSKILLS\nPython"
    extracted = facts(cv)
    assert {item.name for item in extracted.skills} == {"Python"}
    assert extracted.education == []
    assert extracted.gpa == []


# --------------------------------------------------------------------------
# Organisational and educational lines are not employment
# --------------------------------------------------------------------------

#: A synthetic Indonesian school-leaver CV, written to reproduce the shape of a
#: real one that read catastrophically wrong: 58 months of "professional
#: experience" out of about 14. Everything here is invented.
#:
#: Four things about it matter, and all four were defects:
#:
#: * `PENGALAMAN` lists paid work and school-society activity together, which
#:   is normal in Indonesia and which the CV labels itself;
#: * `SERTIFIKAT` and `PRESTASI` were not section headers, so `PENGALAMAN`
#:   stayed open to the end of the document;
#: * a two-column layout flattens to put an education line (`Jurusan IPA`)
#:   after the experience header;
#: * the one short job is dated `Juni-July 2024` -- two months, one year --
#:   which the range pattern could not read at all.
SCHOOL_LEAVER_CV = """BUDI SANTOSO
KONTAK
budi.santoso@example.invalid

PENDIDIKAN
SMA Negeri Fiktif 1

SKILLS
Dapat bekerja sama dengan orang lain

PENGALAMAN
Pernah bekerja sebagai kasir di toko Fiktif Jaya
 (Juni-July 2024)
Memiliki pengalaman berorganisasi yakni sebagai anggota aktif OSIS
SMA (2022-2023)
Pernah bekerja sebagai helper administrasi di bidang gas (2021-2022)
Memiliki pengalaman berorganisasi yakni sebagai asisten panitia
sekolah minggu di wihara fiktif (2018-2019)

SERTIFIKAT
Certificate of Attendance "Intro to Accounting" - 19 January 2024

PRESTASI
Rangking 3 besar di kelas 10,11,12 SMA
Jurusan IPA (2021-2023)
"""


def test_only_real_jobs_are_counted_as_experience() -> None:
    """Two paid jobs, not five dated lines."""
    roles = facts(SCHOOL_LEAVER_CV).roles

    quoted = " | ".join(role.span.text for role in roles)
    assert len(roles) == 2, quoted
    assert "kasir" in quoted
    assert "helper administrasi" in quoted


@pytest.mark.parametrize(
    "phrase",
    [
        "anggota aktif OSIS",  # a school society
        "asisten panitia",  # a committee
        "sekolah minggu",  # Sunday school
        "Jurusan IPA",  # a school stream, which is an education fact
    ],
)
def test_a_non_employment_line_never_becomes_a_role(phrase: str) -> None:
    for role in facts(SCHOOL_LEAVER_CV).roles:
        assert phrase.lower() not in role.span.text.lower()


def test_the_total_is_the_two_jobs_rather_than_every_dated_line() -> None:
    """The number a recruiter reads. It was 58 months; the CV holds about 14."""
    roles = facts(SCHOOL_LEAVER_CV).roles
    assert total_months(roles) < 30


def test_a_place_of_worship_does_not_reach_the_evidence_record() -> None:
    """ADR-0003, reached by a route the protected-attribute scanner cannot see.

    "asisten panitia sekolah minggu di wihara fiktif" was stored as a role, so
    the quotation shown beside an experience verdict named someone's place of
    worship. The scanner does not recognise a temple's name, and extending it
    to would be wrong -- a temple can be a genuine employer, and citing a real
    employer is correct. What fixes this is that Sunday-school volunteering is
    not employment, so it never becomes a role in the first place.
    """
    result = facts(SCHOOL_LEAVER_CV)
    spans = [
        item.span.text
        for group in (result.skills, result.roles, result.education, result.gpa, result.languages)
        for item in group
    ]
    for text in spans:
        assert "wihara" not in text.lower(), f"a span named a place of worship: {text!r}"


def test_a_certificate_section_closes_the_experience_section() -> None:
    """`SERTIFIKAT` and `PRESTASI` must end `PENGALAMAN`, not extend it."""
    text = normalize_text(SCHOOL_LEAVER_CV)
    kinds = {name for _, _, name in _sections(text)}
    assert "certifications" in kinds
    assert "achievements" in kinds


def test_a_short_engagement_dated_as_two_months_of_one_year_is_read() -> None:
    """ "Juni-July 2024" -- mixed-language months, one year. Previously invisible."""
    roles = [r for r in facts(SCHOOL_LEAVER_CV).roles if "kasir" in r.span.text]
    assert len(roles) == 1
    assert roles[0].end_month - roles[0].start_month + 1 == 2


def test_a_wrapped_entry_is_quoted_with_the_line_it_belongs_to() -> None:
    """A verdict citing "(Juni-July 2024)" is true, verifiable and useless."""
    (role,) = [r for r in facts(SCHOOL_LEAVER_CV).roles if "kasir" in r.span.text]
    assert "toko Fiktif Jaya" in role.span.text
    assert "Juni-July 2024" in role.span.text


def test_a_school_as_an_employer_is_still_a_job() -> None:
    """The denial list must not discard real work at an educational employer."""
    cv = "PENGALAMAN\nGuru matematika di Sekolah Fiktif Cikal (2020-2023)\n"
    roles = facts(cv).roles
    assert len(roles) == 1
    assert "Guru matematika" in roles[0].span.text


# --------------------------------------------------------------------------
# Accounting and finance skills
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cv", "expected"),
    [
        ("SKILLS\nAkuntansi, Pembukuan, Perpajakan", {"Accounting", "Bookkeeping", "Taxation"}),
        ("SKILLS\nLaporan Keuangan, Buku Besar", {"Financial Reporting", "General Ledger"}),
        ("SKILLS\nPPh 21, PPN, Faktur Pajak", {"PPh", "PPN", "Tax Invoice"}),
        ("SKILLS\nAccurate, Zahir, MYOB", {"Accurate", "Zahir", "MYOB"}),
        (
            "SKILLS\nRekonsiliasi Bank, Piutang, Hutang Dagang",
            {"Reconciliation", "Accounts Receivable", "Accounts Payable"},
        ),
        ("PENGALAMAN\nMengelola penggajian dan arus kas perusahaan.", {"Payroll", "Cash Flow"}),
        (
            "PENGALAMAN\nMembuat jurnal umum dan rekonsiliasi setiap bulan.",
            {"Journal Entry", "Reconciliation"},
        ),
    ],
)
def test_accounting_skills_are_read_from_realistic_lines(cv: str, expected: set[str]) -> None:
    assert expected <= skill_names(cv)


@pytest.mark.parametrize(
    ("cv", "not_expected"),
    [
        # "Accurate" the package versus "accurate" the adjective. This is the
        # whole reason the term is gated as a common word.
        ("PENGALAMAN\nBertanggung jawab atas accurate financial reporting.", "Accurate"),
        ("EXPERIENCE\nKnown for accurate and timely delivery.", "Accurate"),
        # "audit trail" is a software feature, not an auditing skill.
        ("EXPERIENCE\nAdded an audit trail to the billing service.", "Auditing"),
        # The advert, not the person.
        ("PENGALAMAN\nKualifikasi: menguasai perpajakan dan PPh 21.", "Taxation"),
        ("EXPERIENCE\nAccounting degree required for this role.", "Accounting"),
        # A denial.
        ("PENGALAMAN\nBelum pernah menggunakan Accurate.", "Accurate"),
    ],
)
def test_a_plausible_near_miss_is_not_an_accounting_skill(cv: str, not_expected: str) -> None:
    assert not_expected not in skill_names(cv)


def test_a_certificate_is_exposure_rather_than_applied_accounting() -> None:
    """Same rule the programming skills already follow (SHALLOW_CUES)."""
    found = [
        item
        for item in facts("SKILLS\nPelatihan Akuntansi Dasar, sertifikat Brevet A").skills
        if item.name == "Accounting"
    ]
    assert found and all(item.substantive is False for item in found)


def test_a_role_ending_in_the_future_is_counted_only_up_to_now() -> None:
    """Nobody has worked a month that has not happened.

    A CV written "2023 - 2026" states a year range whose end is December, and
    counting to December credited months still ahead -- over-crediting, in the
    direction that costs most. Such an entry means "until now", so that is how
    it is read.
    """
    cv = "EXPERIENCE\nHarbourline (fictional) - Engineer, 2023 - 2026\n"
    # January 2023 through the supplied as_of of January 2026, inclusive: 37.
    # Reading the stated December 2026 would have credited 48.
    assert total_months(facts(cv).roles) == 37


def test_a_stated_end_in_the_past_is_respected() -> None:
    cv = "EXPERIENCE\nNorthwind (fictional) - Engineer, January 2024 - June 2024\n"
    assert total_months(facts(cv).roles) == 6


# --------------------------------------------------------------------------
# "Learning" and "training" are ordinary words in this industry
# --------------------------------------------------------------------------


def test_a_machine_learning_degree_does_not_downgrade_the_skills_beside_it() -> None:
    """Found by the structured evaluation, not by review.

    `learning` was a bare shallow cue, so "MSc Machine Learning" -- a degree
    title within fifty characters of the skills list -- marked Python, Docker
    and SQL as things the candidate had merely been taught. Every machine
    learning CV was affected.
    """
    cv = (
        "EXPERIENCE\n"
        "Calder Systems (fictional) - ML Engineer, January 2020 - January 2026\n"
        "Trained and deployed ranking models served to internal tools.\n"
        "\n"
        "SKILLS\n"
        "Python, Docker, SQL\n"
        "\n"
        "EDUCATION\n"
        "MSc Machine Learning, Fictional Institute of Technology, 2019\n"
    )
    found = {item.name: item.substantive for item in facts(cv).skills}
    for name in ("Python", "Docker", "SQL"):
        assert found.get(name) is True, f"{name} was downgraded to exposure"


def test_building_training_pipelines_is_work_not_a_training_course() -> None:
    cv = "EXPERIENCE\nBuilt training pipelines in Python for the ranking models.\n"
    found = [item for item in facts(cv).skills if item.name == "Python"]
    assert found and found[0].substantive is True


@pytest.mark.parametrize(
    "line",
    [
        "SKILLS\nCurrently learning Rust",
        "SKILLS\nStill learning Kubernetes",
        "SKILLS\nCompleted training in Docker",
        "SKILLS\nAttended training on Kubernetes",
    ],
)
def test_genuinely_shallow_wording_is_still_caught(line: str) -> None:
    """The cue must keep doing the job it was added for."""
    found = facts(line).skills
    assert found, line
    assert all(item.substantive is False for item in found), line


# --------------------------------------------------------------------------
# Certifications: a credential is a claim, not a mention
# --------------------------------------------------------------------------


def cert_names(text: str) -> set[str]:
    return {item.name for item in facts(text).certifications}


@pytest.mark.parametrize(
    ("cv", "expected"),
    [
        ("SERTIFIKAT\nBrevet A & B Perpajakan, IAI - 2023", {"Brevet A", "Brevet B"}),
        ("SERTIFIKAT\nBrevet AB - 2022", {"Brevet A", "Brevet B"}),
        ("SERTIFIKAT\nBrevet A saja - 2021", {"Brevet A"}),
        ("CERTIFICATIONS\nCPA Indonesia - 2024", {"CPA"}),
        ("PENGALAMAN\nAWS Certified Solutions Architect, 2024", {"AWS Certified"}),
        ("PENDIDIKAN\nBersertifikat Brevet A dan Brevet B perpajakan", {"Brevet A", "Brevet B"}),
        ("SERTIFIKAT\nTOEFL ITP score 540 - 2024", {"TOEFL"}),
    ],
)
def test_a_credential_claim_is_read(cv: str, expected: set[str]) -> None:
    assert expected <= cert_names(cv)


@pytest.mark.parametrize(
    ("cv", "not_expected"),
    [
        # Three letters are not evidence. These are the ways that goes wrong.
        ("PENGALAMAN\nBekerja dengan tim CPA di kantor pusat.", "CPA"),
        ("PENGALAMAN\nKualifikasi: wajib memiliki CPA dan Brevet A.", "CPA"),
        ("PENGALAMAN\nKualifikasi: wajib memiliki CPA dan Brevet A.", "Brevet A"),
        ("SERTIFIKAT\nBelum memiliki Brevet A.", "Brevet A"),
        ("EXPERIENCE\nWe are looking for a CFA charterholder.", "CFA"),
    ],
)
def test_a_mention_that_is_not_a_claim_is_refused(cv: str, not_expected: str) -> None:
    """Awarding somebody a qualification they never claimed is the worst way
    this particular fact could fail, so the gate is deliberately strict."""
    assert not_expected not in cert_names(cv)


def test_a_certification_span_is_the_line_that_claimed_it() -> None:
    cv = "SERTIFIKAT\nBrevet A & B Perpajakan, IAI - 2023"
    found = [item for item in facts(cv).certifications if item.name == "Brevet A"]
    assert found and found[0].span.text == "Brevet A & B Perpajakan, IAI - 2023"


# --------------------------------------------------------------------------
# Found by the realistic-shape corpus in evaluation/data/structured.json
# --------------------------------------------------------------------------

#: One entry over three lines -- employer, title, then the dates alone -- which
#: is how a right-aligned date column comes out of extraction.
THREE_LINE_ENTRY_CV = """PENGALAMAN KERJA
PT Fiktif Sejahtera Abadi
Staf Akuntansi
2021 - 2024
Menyusun laporan keuangan bulanan dan melakukan rekonsiliasi bank.
"""


def test_a_role_is_quoted_verbatim() -> None:
    """A quotation is a slice of the document, not a reconstruction of it.

    The line above a date line was joined on with a space, so this entry was
    cited as "PT Fiktif Sejahtera Abadi Staf Akuntansi" -- a line the document
    never had. Whitespace-normalized verification happened to accept it, which
    is the verifier being lenient, not the quote being right.
    """
    (role,) = facts(THREE_LINE_ENTRY_CV).roles
    assert role.span.text in THREE_LINE_ENTRY_CV
    assert role.span.text == THREE_LINE_ENTRY_CV[role.span.start : role.span.end]


def test_a_role_is_quoted_with_the_dates_it_was_counted_from() -> None:
    """The month pattern could reach across a line break.

    "Akuntansi" at the end of the title line was taken as the month of "2021",
    so the match began on the line above the dates and the quotation left the
    dates out altogether.
    """
    (role,) = facts(THREE_LINE_ENTRY_CV).roles
    assert "2021 - 2024" in role.span.text
    assert "Staf Akuntansi" in role.span.text
    assert total_months([role]) == 48


def test_an_internship_whose_dates_sit_on_their_own_line_is_an_internship() -> None:
    """ "Juli 2025 - September 2025" is a date line and was not read as one.

    Two month names are more than the old pattern's two short words, so the
    line was not joined to "Magang, PT Fiktif Cahaya Abadi" above it and the
    internship term on that line was never seen.
    """
    cv = (
        "PENGALAMAN\n"
        "Magang, PT Fiktif Cahaya Abadi\n"
        "Juli 2025 - September 2025\n"
        "Membantu rekonsiliasi bank dan menyusun arus kas.\n"
    )
    (role,) = facts(cv).roles
    assert role.is_internship is True
    assert "Magang" in role.span.text
    assert total_months([role]) == 3


def test_a_line_with_words_of_its_own_is_its_own_entry() -> None:
    """Two short words passed for a date line, and got joined to the job above.

    Both entries then started at the same line, and the second was discarded as
    a duplicate of the first: a real job, silently not counted.
    """
    cv = "PENGALAMAN\nStaf Akuntansi, PT Fiktif Abadi (2019-2021)\nKasir Swalayan (2021-2022)\n"
    roles = facts(cv).roles
    assert len(roles) == 2
    assert all(role.span.text.count("(") == 1 for role in roles)


@pytest.mark.parametrize(
    "next_section",
    ["SERTIFIKAT\nBrevet A & B, 2024", "BAHASA\nBahasa Indonesia"],
)
def test_a_skill_is_not_judged_by_the_section_after_it(next_section: str) -> None:
    """The verdict depended on which heading happened to come next.

    `sertifikat` is a shallow cue, and the window it was searched for in ran
    fifty characters past the skill -- straight into the next section. A skills
    list followed by SERTIFIKAT had every skill in reach marked as training; the
    identical list followed by BAHASA did not.
    """
    cv = f"KEAHLIAN\nAkuntansi, Microsoft Excel\n\n{next_section}\n"
    found = {item.name: item.substantive for item in facts(cv).skills}
    assert found.get("Accounting") is True
    assert found.get("Excel") is True


@pytest.mark.parametrize(
    "cv",
    [
        "PENDIDIKAN\nS1 Akuntansi, Universitas Fiktif Mandiri\n"
        "Sedang menempuh semester 7, perkiraan lulus 2027\n",
        "EDUCATION\nBSc Accounting, University of the Fictional Midlands\n"
        "Expected graduation: 2027\n",
    ],
)
def test_a_degree_still_being_read_is_not_held_when_the_note_is_on_the_next_line(
    cv: str,
) -> None:
    """Over-crediting, on what is usually a must-have.

    The in-progress cues were searched for in a window ending fifty characters
    after the degree. "Sedang menempuh" on the next line fell four characters
    outside it, so a degree expected in 2027 was credited as held.
    """
    assert facts(cv).education == []


def test_a_note_below_one_degree_is_not_read_as_a_note_about_the_one_above() -> None:
    cv = (
        "PENDIDIKAN\n"
        "D3 Akuntansi, Politeknik Fiktif Jakarta, 2019\n"
        "S1 Akuntansi, Universitas Fiktif Mandiri\n"
        "Sedang menempuh semester 5\n"
    )
    assert {item.level for item in facts(cv).education} == {2}


def test_a_held_degree_is_not_undone_by_a_cue_in_a_later_section() -> None:
    """A guard on the fix rather than a regression: the entry ends where it ends."""
    cv = (
        "PENDIDIKAN\n"
        "S1 Akuntansi, Universitas Fiktif Mandiri, 2020\n"
        "PENGALAMAN\n"
        "Expected to lead the month-end close from 2027.\n"
    )
    assert {item.level for item in facts(cv).education} == {3}
