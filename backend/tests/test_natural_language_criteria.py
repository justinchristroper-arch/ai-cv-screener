"""Screening criteria written the way a recruiter actually writes them.

Extraction used to assume a formal job description. It no longer does: the input
is whatever the recruiter typed, in whatever language and register they think in.
Two consequences are tested here.

**The first is that the pipeline has to survive it.** Indonesian, English, and
the two mixed in one sentence; abbreviations; no bullet points; a preference and
a hard minimum in the same clause. These tests assert what *this application*
does with such input — that it validates, persists, keeps the recruiter's own
language and gets `must_have` from the words they used. They are driven by
recorded fixtures, so they say nothing about how well a live model reads
Indonesian. That question needs a live provider and is listed as unmeasurable in
evaluation/RESULTS.md.

**The second is new risk.** Free text can ask for things a screening system must
never screen on. "Wanita, maksimal 25 tahun" is a sentence someone will type, and
some CVs really do print "Gender: Female", so without a guard the pipeline would
have found evidence for it. The guard is at the confirmation gate, because every
screening stage passes through that gate and nothing passes around it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.enums import JdSourceType, RequirementCategory
from app.core.errors import ConflictError
from app.core.protected_attributes import names_a_protected_attribute, scan
from app.llm.fixtures import fixture_jd_text
from app.services import jd_extraction, jobs, requirements


def _extract(db: Session, replay_client, fixture_name: str):
    job = jobs.create_job(db, title="Criteria test")
    jobs.set_description(
        db,
        job.id,
        raw_text=fixture_jd_text(fixture_name),
        source_type=JdSourceType.PASTED,
    )
    return job, jd_extraction.extract_requirements(db, job.id, replay_client)


# --------------------------------------------------------------------------
# 1. The scanner, on its own
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "attribute"),
    [
        ("Wanita, maksimal 25 tahun", "gender"),
        ("Usia maksimal 30 tahun", "age"),
        ("Under 30 years old", "age"),
        ("Belum menikah", "marital_status"),
        ("Marital status: single", "marital_status"),
        ("Muslim", "religion"),
        ("Suku Jawa", "ethnicity"),
        ("WNI", "nationality"),
        ("Lampirkan foto terbaru", "appearance"),
        ("Sehat jasmani dan rohani", "health"),
        ("Female candidates only", "gender"),
    ],
)
def test_a_criterion_about_a_person_is_flagged(text: str, attribute: str) -> None:
    assert attribute in {flag.attribute for flag in scan(text)}


@pytest.mark.parametrize(
    "text",
    [
        # The whole point of the narrowness: these are ordinary criteria and one
        # of them ("minimal 2 tahun") contains the same word an age limit does.
        "Minimal 2 tahun pengalaman",
        "At least 2 years of experience",
        "Pendidikan S1",
        "IPK di atas 3",
        "Lulusan universitas yang termasuk 10 besar PTN/PTS",
        "Bisa berbahasa Inggris",
        "Fluent in English and Mandarin",
        "Pernah bekerja dengan Python",
        "Able to work with postgres",
        "Bachelor's degree in Computer Science",
        "Experience debugging race conditions",
        "Comfortable with an on-call rotation",
    ],
)
def test_an_ordinary_criterion_is_not_flagged(text: str) -> None:
    assert scan(text) == []
    assert not names_a_protected_attribute(text)


def test_a_flag_carries_the_phrase_it_found() -> None:
    (flag,) = scan("Pendidikan S1, usia maksimal 27")

    assert flag.attribute == "age"
    assert flag.label == "age or date of birth"
    assert "usia" in flag.excerpt


def test_each_characteristic_is_reported_once_however_often_it_appears() -> None:
    flags = scan("Usia 25. Usia maksimal 25 tahun. Berusia muda.")

    assert [flag.attribute for flag in flags] == ["age"]


# --------------------------------------------------------------------------
# 2. Extraction, from criteria a recruiter typed
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_indonesian_criteria_extract_into_indonesian_requirements(
    db_session: Session, replay_client
) -> None:
    """Four informal Indonesian criteria, none of them a job description."""
    _job, result = _extract(db_session, replay_client, "criteria_indonesian")

    texts = [item.text for item in result.requirements]
    assert texts == [
        "Lulusan universitas yang termasuk 10 besar PTN/PTS",
        "Pendidikan S1",
        "Bisa berbahasa Inggris",
        "IPK di atas 3",
    ]
    assert result.requirements[1].must_have is True  # "harus s1"
    assert result.requirements[3].must_have is True  # "ipk di atas 3" is a threshold
    assert result.requirements[0].must_have is False  # "saya mau" is a preference

    # A language a candidate can use is a skill, not a personal characteristic,
    # so it must survive both the categoriser and the guard.
    assert result.requirements[2].category is RequirementCategory.SOFT_SKILL_OTHER
    assert scan(result.requirements[2].text) == []


@pytest.mark.requires_db
def test_mixed_language_criteria_keep_each_clause_in_its_own_language(
    db_session: Session, replay_client
) -> None:
    _job, result = _extract(db_session, replay_client, "criteria_mixed_language")

    by_text = {item.text: item for item in result.requirements}
    assert "Minimal 2 tahun pengalaman" in by_text
    assert by_text["Minimal 2 tahun pengalaman"].must_have is True

    # "kalau pernah AI/ML lebih bagus" is a preference, not a demand.
    assert by_text["Pernah mengerjakan AI/ML"].must_have is False

    # Technology names survive exactly as written.
    assert "Pernah bekerja dengan PostgreSQL" in by_text


@pytest.mark.requires_db
def test_informal_english_criteria_extract_without_a_job_description(
    db_session: Session, replay_client
) -> None:
    _job, result = _extract(db_session, replay_client, "criteria_english_informal")

    by_text = {item.text: item for item in result.requirements}
    assert by_text["At least 2 years of experience"].must_have is True
    assert by_text["Experience with aws"].must_have is False
    # Shorthand is kept, which is what routes this pair through the alias
    # matcher rather than the exact one.
    assert "Able to work with postgres" in by_text


@pytest.mark.requires_db
def test_criteria_of_a_single_short_line_are_not_padded_out(
    db_session: Session, replay_client
) -> None:
    """A brief that states five things produces five requirements, not twenty."""
    _job, result = _extract(db_session, replay_client, "criteria_mixed_language")

    assert len(result.requirements) == 5


# --------------------------------------------------------------------------
# 3. The confirmation gate refuses a protected characteristic
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_requirement_about_a_person_cannot_be_confirmed(
    db_session: Session, replay_client
) -> None:
    job, _result = _extract(db_session, replay_client, "criteria_indonesian")
    requirements.add_requirement(
        db_session,
        job.id,
        text="Wanita, usia maksimal 25 tahun",
        category=RequirementCategory.SOFT_SKILL_OTHER,
        must_have=True,
    )

    with pytest.raises(ConflictError) as caught:
        requirements.confirm_requirements(db_session, job.id)

    assert "cannot be confirmed" in str(caught.value)
    named = caught.value.details["requirements"]
    assert len(named) == 1
    assert named[0]["text"] == "Wanita, usia maksimal 25 tahun"
    assert set(named[0]["attributes"]) == {"gender", "age or date of birth"}

    db_session.refresh(job)
    assert job.requirements_confirmed_at is None


@pytest.mark.requires_db
def test_removing_the_offending_requirement_lets_confirmation_through(
    db_session: Session, replay_client
) -> None:
    job, _result = _extract(db_session, replay_client, "criteria_indonesian")
    offending = requirements.add_requirement(
        db_session,
        job.id,
        text="Belum menikah",
        category=RequirementCategory.SOFT_SKILL_OTHER,
        must_have=True,
    )

    with pytest.raises(ConflictError):
        requirements.confirm_requirements(db_session, job.id)

    requirements.delete_requirement(db_session, offending.id)
    confirmed = requirements.confirm_requirements(db_session, job.id)

    assert confirmed.requirements_confirmed_at is not None


@pytest.mark.requires_db
def test_ordinary_criteria_still_confirm(db_session: Session, replay_client) -> None:
    """The guard must not stand in the way of the normal path."""
    job, _result = _extract(db_session, replay_client, "criteria_mixed_language")

    confirmed = requirements.confirm_requirements(db_session, job.id)

    assert confirmed.requirements_confirmed_at is not None


@pytest.mark.requires_db
def test_the_offending_text_is_kept_exactly_as_the_recruiter_wrote_it(
    db_session: Session, replay_client
) -> None:
    """Refused, not rewritten. Editing someone's words is not this tool's job."""
    job = jobs.create_job(db_session, title="Criteria test")
    typed = "Backend engineer. Wanita, maksimal 25 tahun. Minimal S1."
    description = jobs.set_description(
        db_session, job.id, raw_text=typed, source_type=JdSourceType.PASTED
    )

    assert description.raw_text == typed


# --------------------------------------------------------------------------
# 4. The same facts over HTTP
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_the_api_flags_a_protected_characteristic_on_the_requirement(api) -> None:
    job = api.post("/api/jobs", json={"title": "Flagged"}).json()
    api.put(
        f"/api/jobs/{job['id']}/description",
        json={"raw_text": "Backend engineer, wanita, maksimal 25 tahun", "source_type": "PASTED"},
    )
    created = api.post(
        f"/api/jobs/{job['id']}/requirements",
        json={
            "text": "Wanita, maksimal 25 tahun",
            "category": "SOFT_SKILL_OTHER",
            "must_have": True,
        },
    )

    assert created.status_code == 201
    attributes = {flag["attribute"] for flag in created.json()["protected_attribute_flags"]}
    assert attributes == {"gender", "age"}

    refused = api.post(f"/api/jobs/{job['id']}/requirements/confirm")
    assert refused.status_code == 409
    assert "cannot be confirmed" in refused.json()["message"]


@pytest.mark.requires_db
def test_the_api_flags_the_criteria_text_before_extraction(api) -> None:
    """Said while the recruiter is looking at what they typed, not three steps later."""
    job = api.post("/api/jobs", json={"title": "Flagged criteria"}).json()

    stored = api.put(
        f"/api/jobs/{job['id']}/description",
        json={"raw_text": "Minimal S1. Belum menikah.", "source_type": "PASTED"},
    ).json()

    assert [flag["attribute"] for flag in stored["protected_attribute_flags"]] == ["marital_status"]
    assert stored["raw_text"] == "Minimal S1. Belum menikah."


@pytest.mark.requires_db
def test_an_ordinary_requirement_carries_no_flags_over_http(api) -> None:
    job = api.post("/api/jobs", json={"title": "Ordinary"}).json()
    created = api.post(
        f"/api/jobs/{job['id']}/requirements",
        json={
            "text": "Minimal 2 tahun pengalaman Python",
            "category": "EXPERIENCE",
            "must_have": True,
        },
    )

    assert created.json()["protected_attribute_flags"] == []


@pytest.mark.requires_db
def test_the_refusal_reads_correctly_for_one_and_for_several(
    db_session: Session, replay_client
) -> None:
    """A recruiter reads this message; "2 requirements asks" is not a sentence."""
    job, _result = _extract(db_session, replay_client, "criteria_indonesian")
    requirements.add_requirement(
        db_session,
        job.id,
        text="Belum menikah",
        category=RequirementCategory.SOFT_SKILL_OTHER,
        must_have=True,
    )

    with pytest.raises(ConflictError) as one:
        requirements.confirm_requirements(db_session, job.id)
    assert "1 requirement asks about" in str(one.value)
    assert "reword it" in str(one.value)

    requirements.add_requirement(
        db_session,
        job.id,
        text="Usia maksimal 25 tahun",
        category=RequirementCategory.SOFT_SKILL_OTHER,
        must_have=True,
    )

    with pytest.raises(ConflictError) as several:
        requirements.confirm_requirements(db_session, job.id)
    assert "2 requirements ask about" in str(several.value)
    assert "reword them" in str(several.value)
