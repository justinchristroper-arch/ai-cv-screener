"""The structured screening API: the published vocabulary and the typed create.

Two endpoints, one contract. `GET /api/criteria/vocabulary` is the only thing
the interface is allowed to offer, and `POST /api/jobs/{id}/criteria` is the
only thing that accepts it. The tests below tie the two together deliberately:
anything the vocabulary advertises must be postable, and anything outside it
must be refused with a reason rather than quietly stored and answered
NEEDS_REVIEW forever (ADR-0012).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.services import skill_taxonomy, structured_match

VOCABULARY = "/api/criteria/vocabulary"


def _create_job(api: TestClient, title: str = "Backend Engineer") -> str:
    response = api.post("/api/jobs", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _criterion(api: TestClient, job_id: str, **payload: object):
    body = {"must_have": False, **payload}
    return api.post(f"/api/jobs/{job_id}/criteria", json=body)


# --------------------------------------------------------------------------
# The published vocabulary
# --------------------------------------------------------------------------


def test_the_vocabulary_needs_no_database(client: TestClient) -> None:
    """It depends on no job, no candidate and no row, so it is always available.

    Worth pinning: the criteria builder cannot render at all without this, so
    it must not become the one screen that breaks when the database is down.
    """
    response = client.get(VOCABULARY)
    assert response.status_code == 200, response.text


def test_the_vocabulary_offers_exactly_the_types_the_engine_has(client: TestClient) -> None:
    body = client.get(VOCABULARY).json()
    offered = [item["spec_type"] for item in body["spec_types"]]
    assert offered == list(structured_match.SPEC_TYPES)


def test_every_type_that_takes_a_subject_names_a_list_in_the_same_response(
    client: TestClient,
) -> None:
    """A dropdown with nowhere to read its options from is a broken form."""
    body = client.get(VOCABULARY).json()
    for item in body["spec_types"]:
        source = item["subject_source"]
        if source is None:
            continue
        assert body[source], f"{item['spec_type']} points at an empty list {source!r}"


def test_only_gpa_asks_the_recruiter_for_a_scale(client: TestClient) -> None:
    """The engine never assumes 4.0 or 5.0, so exactly one type collects it."""
    body = client.get(VOCABULARY).json()
    needing_scale = [item["spec_type"] for item in body["spec_types"] if item["needs_scale"]]
    assert needing_scale == ["GPA_MIN"]


def test_a_type_that_needs_a_scale_also_requires_its_threshold(client: TestClient) -> None:
    body = client.get(VOCABULARY).json()
    for item in body["spec_types"]:
        if item["needs_scale"]:
            assert item["threshold_required"], item["spec_type"]
            assert item["threshold_unit"] is not None


def test_the_published_skills_are_the_whole_taxonomy(client: TestClient) -> None:
    """Published in full on purpose: the boundary is a product fact, not a secret."""
    body = client.get(VOCABULARY).json()
    assert [item["name"] for item in body["skills"]] == skill_taxonomy.supported_skills()
    for item in body["skills"]:
        assert item["family"] == skill_taxonomy.family_of(item["name"])


def test_the_published_languages_are_the_whole_list(client: TestClient) -> None:
    body = client.get(VOCABULARY).json()
    assert body["languages"] == skill_taxonomy.supported_languages()


def test_degrees_carry_the_rank_the_comparison_actually_uses(client: TestClient) -> None:
    """Equal ranks are the same level under two naming conventions."""
    body = client.get(VOCABULARY).json()
    ranks = {item["name"]: item["rank"] for item in body["degrees"]}
    assert ranks == structured_match.DEGREE_CHOICES
    assert ranks["S1"] == ranks["Bachelor"]
    assert ranks["S3"] > ranks["S2"] > ranks["S1"]


def test_skill_group_presets_only_contain_supported_skills(client: TestClient) -> None:
    """A preset is a shortcut for adding ordinary rows, never a way past the list."""
    body = client.get(VOCABULARY).json()
    published = {item["name"] for item in body["skills"]}
    assert body["skill_groups"]
    for group in body["skill_groups"]:
        assert group["skills"]
        assert set(group["skills"]) <= published, group["name"]


def test_the_vocabulary_does_not_offer_a_group_as_a_criterion_type(client: TestClient) -> None:
    """AI/ML is a preset, not a criterion type of its own (ADR-0012)."""
    body = client.get(VOCABULARY).json()
    offered = {item["spec_type"] for item in body["spec_types"]}
    for group in body["skill_groups"]:
        assert group["name"] not in offered


def test_the_vocabulary_is_stable_between_calls(client: TestClient) -> None:
    """Static for a build, so a client may fetch it once and cache it."""
    assert client.get(VOCABULARY).json() == client.get(VOCABULARY).json()


# --------------------------------------------------------------------------
# Creating a structured criterion
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_skill_criterion_is_stored_with_its_structured_fields(api: TestClient) -> None:
    job_id = _create_job(api)
    response = _criterion(api, job_id, spec_type="SKILL", subject="PostgreSQL", must_have=True)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["spec_type"] == "SKILL"
    assert body["subject"] == "PostgreSQL"
    assert body["threshold_value"] is None
    assert body["threshold_scale"] is None
    # The readable rendering is still written, so every existing display keeps
    # working against a structured row without knowing it is one.
    assert body["text"] == "Skill: PostgreSQL"
    assert body["category"] == "TECHNICAL_SKILL"
    assert body["origin"] == "HR_ADDED"
    assert body["must_have"] is True


@pytest.mark.requires_db
def test_a_gpa_criterion_keeps_the_scale_the_recruiter_stated(api: TestClient) -> None:
    job_id = _create_job(api)
    response = _criterion(
        api, job_id, spec_type="GPA_MIN", threshold_value="3.25", threshold_scale="4.0"
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["spec_type"] == "GPA_MIN"
    assert body["subject"] is None
    assert float(body["threshold_value"]) == 3.25
    assert float(body["threshold_scale"]) == 4.0
    assert "3.25" in body["text"]


@pytest.mark.requires_db
def test_an_internship_criterion_may_ask_for_presence_alone(api: TestClient) -> None:
    """Having done an internship at all is a complete criterion on its own."""
    job_id = _create_job(api)
    response = _criterion(api, job_id, spec_type="INTERNSHIP_MIN")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["threshold_value"] is None
    assert body["text"] == "Internship experience"


@pytest.mark.requires_db
def test_every_published_criterion_type_can_actually_be_created(api: TestClient) -> None:
    """The form the interface builds must be one the API accepts.

    This is the test that keeps the two endpoints honest as a pair: it reads
    the vocabulary, fills each type from the lists that same response names,
    and posts it.
    """
    vocabulary = api.get(VOCABULARY).json()
    subjects = {
        "degrees": vocabulary["degrees"][0]["name"],
        "skills": vocabulary["skills"][0]["name"],
        "languages": vocabulary["languages"][0],
    }
    job_id = _create_job(api)

    for item in vocabulary["spec_types"]:
        payload: dict[str, object] = {"spec_type": item["spec_type"]}
        # Subject and threshold are filled independently. They were mutually
        # exclusive across the original six types, and this loop said `elif`
        # until EXPERIENCE_IN_FIELD arrived needing both -- at which point it
        # built a payload the API rightly refused, which is what a test pairing
        # the published form with the accepting endpoint is for.
        if item["subject_source"]:
            payload["subject"] = subjects[item["subject_source"]]
        if item["threshold_required"]:
            payload["threshold_value"] = "3.0" if item["threshold_unit"] == "grade" else "24"
        if item["needs_scale"]:
            payload["threshold_scale"] = "4.0"

        response = _criterion(api, job_id, **payload)
        assert response.status_code == 201, f"{item['spec_type']}: {response.text}"
        assert response.json()["spec_type"] == item["spec_type"]

    listing = api.get(f"/api/jobs/{job_id}/requirements").json()
    assert len(listing["requirements"]) == len(vocabulary["spec_types"])


@pytest.mark.requires_db
def test_a_created_criterion_comes_back_on_the_requirement_listing(api: TestClient) -> None:
    job_id = _create_job(api)
    _criterion(api, job_id, spec_type="EXPERIENCE_MIN", threshold_value="36", must_have=True)

    listing = api.get(f"/api/jobs/{job_id}/requirements")
    assert listing.status_code == 200
    (row,) = listing.json()["requirements"]
    assert row["spec_type"] == "EXPERIENCE_MIN"
    assert float(row["threshold_value"]) == 36
    assert row["text"] == "At least 3 years of professional experience"


# --------------------------------------------------------------------------
# Refusals: the shape of the row
# --------------------------------------------------------------------------


@pytest.mark.requires_db
@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"spec_type": "EXPERIENCE_MIN"}, "a duration criterion with no duration"),
        ({"spec_type": "GPA_MIN", "threshold_value": "3.4"}, "a grade with no scale"),
        ({"spec_type": "GPA_MIN", "threshold_scale": "4.0"}, "a scale with no grade"),
        (
            {"spec_type": "GPA_MIN", "threshold_value": "4.5", "threshold_scale": "4.0"},
            "a minimum above its own scale",
        ),
        ({"spec_type": "SKILL"}, "a skill criterion naming no skill"),
        ({"spec_type": "SKILL", "subject": "   "}, "a blank subject"),
        (
            {"spec_type": "SKILL", "subject": "Python", "threshold_value": "12"},
            "a skill with a duration attached",
        ),
        (
            {"spec_type": "EXPERIENCE_MIN", "threshold_value": "12", "subject": "Python"},
            "a duration with a subject attached",
        ),
        ({"spec_type": "LANGUAGE_PRESENT"}, "a language criterion naming no language"),
        ({"spec_type": "EDUCATION_MIN"}, "a degree criterion naming no degree"),
        ({"spec_type": "NOT_A_TYPE", "subject": "Python"}, "a type that does not exist"),
        (
            {"spec_type": "SKILL", "subject": "Python", "proficiency": "expert"},
            "a field the contract does not have",
        ),
    ],
)
def test_an_ill_formed_criterion_is_refused_at_the_boundary(
    api: TestClient, payload: dict, why: str
) -> None:
    job_id = _create_job(api)
    response = _criterion(api, job_id, **payload)
    assert response.status_code == 422, f"{why} was accepted: {response.text}"


@pytest.mark.requires_db
def test_a_negative_duration_is_refused(api: TestClient) -> None:
    job_id = _create_job(api)
    response = _criterion(api, job_id, spec_type="EXPERIENCE_MIN", threshold_value="-6")
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Refusals: outside the supported vocabulary
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_an_unsupported_skill_is_refused_while_the_recruiter_is_still_writing(
    api: TestClient,
) -> None:
    """Not NO_EVIDENCE, and not silently stored.

    A criterion the engine can never answer is not a screening criterion. The
    refusal happens here so the recruiter learns it now rather than after a
    screening run reports a gap in our vocabulary as a gap in the candidate.
    """
    job_id = _create_job(api)
    response = _criterion(api, job_id, spec_type="SKILL", subject="Blockchain")

    assert response.status_code == 409, response.text
    assert "Blockchain" in response.json()["message"]

    assert api.get(f"/api/jobs/{job_id}/requirements").json()["requirements"] == []


@pytest.mark.requires_db
@pytest.mark.parametrize(
    ("spec_type", "subject"),
    [
        ("SKILL", "Quantum Computing"),
        ("LANGUAGE_PRESENT", "Klingon"),
        ("EDUCATION_MIN", "PhD-equivalent"),
        # Case matters: the subject is chosen from a list, not typed freely.
        ("SKILL", "python"),
        ("LANGUAGE_PRESENT", "english"),
    ],
)
def test_a_subject_outside_the_published_list_is_refused(
    api: TestClient, spec_type: str, subject: str
) -> None:
    job_id = _create_job(api)
    response = _criterion(api, job_id, spec_type=spec_type, subject=subject)
    assert response.status_code == 409, response.text


@pytest.mark.requires_db
def test_a_skill_group_name_is_not_accepted_as_a_skill(api: TestClient) -> None:
    """The preset expands into real skills; the group name itself is not one."""
    job_id = _create_job(api)
    for name in skill_taxonomy.SKILL_GROUPS:
        response = _criterion(api, job_id, spec_type="SKILL", subject=name)
        assert response.status_code == 409, f"{name} was accepted as a skill"


# --------------------------------------------------------------------------
# Refusals: the gate and the job
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_criterion_cannot_be_added_after_confirmation(api: TestClient) -> None:
    """The confirmation gate applies to structured criteria too (ADR-0004)."""
    job_id = _create_job(api)
    assert _criterion(api, job_id, spec_type="SKILL", subject="Python").status_code == 201
    assert api.post(f"/api/jobs/{job_id}/requirements/confirm").status_code == 200

    response = _criterion(api, job_id, spec_type="SKILL", subject="Docker")
    assert response.status_code == 409, response.text

    api.delete(f"/api/jobs/{job_id}/requirements/confirm")
    assert _criterion(api, job_id, spec_type="SKILL", subject="Docker").status_code == 201


@pytest.mark.requires_db
def test_a_criterion_on_an_unknown_job_is_a_404(api: TestClient) -> None:
    response = _criterion(api, str(uuid.uuid4()), spec_type="SKILL", subject="Python")
    assert response.status_code == 404


@pytest.mark.requires_db
def test_the_weight_defaults_follow_the_must_have_flag(api: TestClient) -> None:
    job_id = _create_job(api)
    must = _criterion(api, job_id, spec_type="SKILL", subject="Python", must_have=True).json()
    nice = _criterion(api, job_id, spec_type="SKILL", subject="Docker", must_have=False).json()
    explicit = _criterion(
        api, job_id, spec_type="SKILL", subject="Redis", must_have=False, weight="7"
    ).json()

    assert float(must["weight"]) == 3
    assert float(nice["weight"]) == 1
    assert float(explicit["weight"]) == 7


# --------------------------------------------------------------------------
# The type that carries a subject and a threshold together
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_field_experience_criterion_stores_both_halves(api: TestClient) -> None:
    job_id = _create_job(api)
    response = _criterion(
        api,
        job_id,
        spec_type="EXPERIENCE_IN_FIELD",
        subject="Accounting",
        threshold_value="24",
        must_have=True,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["spec_type"] == "EXPERIENCE_IN_FIELD"
    assert body["subject"] == "Accounting"
    assert float(body["threshold_value"]) == 24
    assert body["threshold_scale"] is None
    assert body["text"] == "At least 2 years of Accounting experience"
    assert body["category"] == "EXPERIENCE"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    ("payload", "why", "status"),
    [
        ({"spec_type": "EXPERIENCE_IN_FIELD", "subject": "Accounting"}, "no duration", 422),
        ({"spec_type": "EXPERIENCE_IN_FIELD", "threshold_value": "24"}, "no field", 422),
        (
            {
                "spec_type": "EXPERIENCE_IN_FIELD",
                "subject": "Accounting",
                "threshold_value": "24",
                "threshold_scale": "4.0",
            },
            "a scale on a duration",
            422,
        ),
        (
            {"spec_type": "EXPERIENCE_IN_FIELD", "subject": "Blockchain", "threshold_value": "24"},
            "a field outside the vocabulary",
            409,
        ),
    ],
)
def test_an_ill_formed_field_criterion_is_refused(
    api: TestClient, payload: dict, why: str, status: int
) -> None:
    """Either half alone is a criterion that already exists, so neither is enough."""
    job_id = _create_job(api)
    response = _criterion(api, job_id, **payload)
    assert response.status_code == status, f"{why}: {response.text}"
