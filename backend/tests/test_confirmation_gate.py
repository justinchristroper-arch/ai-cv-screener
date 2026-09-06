"""The human confirmation gate (ADR-0004), at the service layer.

The invariant:

    extracted / edited requirements -> HR confirmation -> confirmed set
                                                            |
                                              eligible for screening

These tests exercise the gate where it is actually enforced. A gate that only
existed in the UI would not be a gate, so what matters here is that the refusal
comes from the service — the layer every future caller, route, and background
task has to go through.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.enums import JdSourceType, RequirementCategory, RequirementOrigin
from app.core.errors import (
    ConflictError,
    NotFoundError,
    RequirementsNotConfirmedError,
)
from app.llm.fixtures import fixture_jd_text
from app.services import jd_extraction, jobs, requirements

pytestmark = pytest.mark.requires_db


def _extracted_job(db: Session, replay_client):
    job = jobs.create_job(db, title="Senior Backend Engineer")
    jobs.set_description(
        db,
        job.id,
        raw_text=fixture_jd_text("jd_backend_engineer"),
        source_type=JdSourceType.PASTED,
    )
    jd_extraction.extract_requirements(db, job.id, replay_client)
    return job


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------


def test_downstream_cannot_read_unconfirmed_requirements(
    db_session: Session, replay_client
) -> None:
    """This is the load-bearing assertion of the whole phase."""
    job = _extracted_job(db_session, replay_client)

    assert job.requirements_confirmed_at is None
    with pytest.raises(RequirementsNotConfirmedError):
        requirements.get_confirmed_requirements(db_session, job.id)


def test_the_refusal_is_not_an_empty_list(db_session: Session, replay_client) -> None:
    """An empty list would be ambiguous and dangerous.

    A caller could read it as "this job has no requirements" and carry on
    screening against nothing. Raising makes the refusal impossible to miss.
    """
    job = _extracted_job(db_session, replay_client)

    with pytest.raises(RequirementsNotConfirmedError) as excinfo:
        requirements.get_confirmed_requirements(db_session, job.id)

    assert "not been confirmed" in excinfo.value.message
    assert excinfo.value.code == "requirements_not_confirmed"


def test_confirmation_opens_the_gate(db_session: Session, replay_client) -> None:
    job = _extracted_job(db_session, replay_client)

    requirements.confirm_requirements(db_session, job.id)

    confirmed = requirements.get_confirmed_requirements(db_session, job.id)
    assert len(confirmed) == 13


def test_inspection_is_allowed_before_confirmation(db_session: Session, replay_client) -> None:
    """HR has to be able to read what they are about to confirm."""
    job = _extracted_job(db_session, replay_client)

    assert len(requirements.list_requirements(db_session, job.id)) == 13


def test_confirmation_records_a_timestamp(db_session: Session, replay_client) -> None:
    job = _extracted_job(db_session, replay_client)

    confirmed = requirements.confirm_requirements(db_session, job.id)

    assert confirmed.requirements_confirmed_at is not None


def test_confirmation_is_idempotent_and_keeps_the_original_timestamp(
    db_session: Session, replay_client
) -> None:
    """The timestamp records when a human decided, not when someone last clicked."""
    job = _extracted_job(db_session, replay_client)

    first = requirements.confirm_requirements(db_session, job.id).requirements_confirmed_at
    second = requirements.confirm_requirements(db_session, job.id).requirements_confirmed_at

    assert first == second


def test_an_empty_requirement_set_cannot_be_confirmed(db_session: Session) -> None:
    job = jobs.create_job(db_session, title="Nothing to confirm")

    with pytest.raises(ConflictError) as excinfo:
        requirements.confirm_requirements(db_session, job.id)

    assert "no requirements" in excinfo.value.message.lower()


def test_unconfirming_closes_the_gate_again(db_session: Session, replay_client) -> None:
    job = _extracted_job(db_session, replay_client)
    requirements.confirm_requirements(db_session, job.id)

    requirements.unconfirm_requirements(db_session, job.id)

    with pytest.raises(RequirementsNotConfirmedError):
        requirements.get_confirmed_requirements(db_session, job.id)


def test_the_gate_rejects_an_unknown_job(db_session: Session) -> None:
    with pytest.raises(NotFoundError):
        requirements.get_confirmed_requirements(db_session, uuid.uuid4())


# --------------------------------------------------------------------------
# What confirmation freezes
# --------------------------------------------------------------------------


def test_confirmed_text_cannot_be_edited(db_session: Session, replay_client) -> None:
    """Text changes what a verdict means, so it is frozen."""
    job = _extracted_job(db_session, replay_client)
    target = requirements.list_requirements(db_session, job.id)[0]
    requirements.confirm_requirements(db_session, job.id)

    with pytest.raises(ConflictError) as excinfo:
        requirements.update_requirement(db_session, target.id, text="Something else entirely")

    assert "unconfirm" in excinfo.value.message.lower()


def test_confirmed_category_cannot_be_edited(db_session: Session, replay_client) -> None:
    job = _extracted_job(db_session, replay_client)
    target = requirements.list_requirements(db_session, job.id)[0]
    requirements.confirm_requirements(db_session, job.id)

    with pytest.raises(ConflictError):
        requirements.update_requirement(db_session, target.id, category=RequirementCategory.PROJECT)


def test_weight_stays_editable_while_confirmed(db_session: Session, replay_client) -> None:
    """Re-weighting is free and must not require unconfirming (ADR-0008).

    Weight does not affect verdicts, only the arithmetic applied to them, so
    the recruiter's most common repeated action costs nothing.
    """
    job = _extracted_job(db_session, replay_client)
    target = requirements.list_requirements(db_session, job.id)[0]
    requirements.confirm_requirements(db_session, job.id)

    updated = requirements.update_requirement(db_session, target.id, weight=Decimal("7"))

    assert updated.weight == Decimal("7")


def test_must_have_stays_editable_while_confirmed(db_session: Session, replay_client) -> None:
    job = _extracted_job(db_session, replay_client)
    target = requirements.list_requirements(db_session, job.id)[0]
    requirements.confirm_requirements(db_session, job.id)

    updated = requirements.update_requirement(db_session, target.id, must_have=False)

    assert updated.must_have is False


def test_editing_weight_does_not_close_the_gate(db_session: Session, replay_client) -> None:
    job = _extracted_job(db_session, replay_client)
    target = requirements.list_requirements(db_session, job.id)[0]
    requirements.confirm_requirements(db_session, job.id)

    requirements.update_requirement(db_session, target.id, weight=Decimal("5"))

    assert requirements.get_confirmed_requirements(db_session, job.id)


def test_a_requirement_cannot_be_added_while_confirmed(db_session: Session, replay_client) -> None:
    """Adding changes the set, and confirmation freezes the set."""
    job = _extracted_job(db_session, replay_client)
    requirements.confirm_requirements(db_session, job.id)

    with pytest.raises(ConflictError):
        requirements.add_requirement(
            db_session,
            job.id,
            text="An extra requirement slipped in after confirmation",
            category=RequirementCategory.TECHNICAL_SKILL,
            must_have=True,
        )


def test_a_requirement_cannot_be_deleted_while_confirmed(
    db_session: Session, replay_client
) -> None:
    job = _extracted_job(db_session, replay_client)
    target = requirements.list_requirements(db_session, job.id)[0]
    requirements.confirm_requirements(db_session, job.id)

    with pytest.raises(ConflictError):
        requirements.delete_requirement(db_session, target.id)


def test_re_extraction_is_blocked_while_confirmed(db_session: Session, replay_client) -> None:
    """Re-extraction replaces the whole set — the most structural change of all."""
    job = _extracted_job(db_session, replay_client)
    requirements.confirm_requirements(db_session, job.id)

    with pytest.raises(ConflictError) as excinfo:
        jd_extraction.extract_requirements(db_session, job.id, replay_client)

    assert "unconfirm" in excinfo.value.message.lower()


def test_unconfirm_then_edit_then_reconfirm(db_session: Session, replay_client) -> None:
    """The full correction loop a recruiter actually performs."""
    job = _extracted_job(db_session, replay_client)
    target = requirements.list_requirements(db_session, job.id)[0]
    requirements.confirm_requirements(db_session, job.id)

    requirements.unconfirm_requirements(db_session, job.id)
    edited = requirements.update_requirement(
        db_session, target.id, text="Bachelor's degree in a numerate discipline"
    )
    requirements.confirm_requirements(db_session, job.id)

    assert edited.text == "Bachelor's degree in a numerate discipline"
    # The original proposal is preserved, so the correction stays measurable.
    assert edited.proposed_text != edited.text
    assert len(requirements.get_confirmed_requirements(db_session, job.id)) == 13


# --------------------------------------------------------------------------
# Editing rules independent of confirmation
# --------------------------------------------------------------------------


def test_hand_added_requirements_carry_no_proposal(db_session: Session) -> None:
    """`proposed_*` must stay NULL: the model never proposed this one."""
    job = jobs.create_job(db_session, title="Manual only")

    added = requirements.add_requirement(
        db_session,
        job.id,
        text="Must hold a valid driving licence",
        category=RequirementCategory.SOFT_SKILL_OTHER,
        must_have=True,
    )

    assert added.origin is RequirementOrigin.HR_ADDED
    assert added.proposed_text is None
    assert added.proposed_category is None
    assert added.proposed_must_have is None


def test_hand_added_requirements_get_the_default_weight(db_session: Session) -> None:
    job = jobs.create_job(db_session, title="Manual only")

    must = requirements.add_requirement(
        db_session,
        job.id,
        text="Five years of Rust",
        category=RequirementCategory.EXPERIENCE,
        must_have=True,
    )
    nice = requirements.add_requirement(
        db_session,
        job.id,
        text="Conference speaking experience",
        category=RequirementCategory.SOFT_SKILL_OTHER,
        must_have=False,
    )

    assert must.weight == Decimal("3")
    assert nice.weight == Decimal("1")


def test_a_negative_weight_is_rejected(db_session: Session) -> None:
    job = jobs.create_job(db_session, title="Weights")

    with pytest.raises(ConflictError):
        requirements.add_requirement(
            db_session,
            job.id,
            text="Something",
            category=RequirementCategory.EXPERIENCE,
            must_have=True,
            weight=Decimal("-1"),
        )


def test_an_absurd_weight_is_rejected(db_session: Session) -> None:
    """One requirement must not be able to drown out every other by accident."""
    job = jobs.create_job(db_session, title="Weights")

    with pytest.raises(ConflictError):
        requirements.add_requirement(
            db_session,
            job.id,
            text="Something",
            category=RequirementCategory.EXPERIENCE,
            must_have=True,
            weight=Decimal("1000"),
        )


def test_blank_requirement_text_is_rejected(db_session: Session, replay_client) -> None:
    job = _extracted_job(db_session, replay_client)
    target = requirements.list_requirements(db_session, job.id)[0]

    with pytest.raises(ConflictError):
        requirements.update_requirement(db_session, target.id, text="   ")


# --------------------------------------------------------------------------
# Job description replacement
# --------------------------------------------------------------------------


def test_replacing_the_description_unconfirms_the_job(db_session: Session, replay_client) -> None:
    """A confirmation made against different text no longer means anything."""
    job = _extracted_job(db_session, replay_client)
    requirements.confirm_requirements(db_session, job.id)

    jobs.set_description(
        db_session,
        job.id,
        raw_text="A completely different role with different requirements.",
        source_type=JdSourceType.PASTED,
    )

    assert jobs.get_job(db_session, job.id).requirements_confirmed_at is None
    with pytest.raises(RequirementsNotConfirmedError):
        requirements.get_confirmed_requirements(db_session, job.id)


def test_replacing_the_description_discards_derived_requirements(
    db_session: Session, replay_client
) -> None:
    """Derived rows must not outlive their input (docs/data-model.md section 7)."""
    job = _extracted_job(db_session, replay_client)
    manual = requirements.add_requirement(
        db_session,
        job.id,
        text="Must be able to work UK hours",
        category=RequirementCategory.SOFT_SKILL_OTHER,
        must_have=True,
    )

    jobs.set_description(
        db_session,
        job.id,
        raw_text="A completely different role with different requirements.",
        source_type=JdSourceType.PASTED,
    )

    remaining = requirements.list_requirements(db_session, job.id)
    assert [item.id for item in remaining] == [manual.id], (
        "extracted requirements should go with the replaced text; hand-added ones should survive"
    )


def test_rewriting_identical_description_text_changes_nothing(
    db_session: Session, replay_client
) -> None:
    """A no-op save must not destroy work or revoke a confirmation."""
    job = _extracted_job(db_session, replay_client)
    requirements.confirm_requirements(db_session, job.id)
    same_text = fixture_jd_text("jd_backend_engineer")

    jobs.set_description(db_session, job.id, raw_text=same_text, source_type=JdSourceType.PASTED)

    assert jobs.get_job(db_session, job.id).requirements_confirmed_at is not None
    assert len(requirements.list_requirements(db_session, job.id)) == 13
