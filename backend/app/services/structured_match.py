"""Structured requirement + CV facts -> one verdict, with the line it came from.

Pure arithmetic and comparison over `cv_facts`. No model, no database, no clock.
This is the module ADR-0012 put in place of semantic matching, and the whole of
its judgement is visible in `match()`.

## The four verdicts, and the one that is new

``MATCHED``       the facts satisfy the requirement.
``PARTIAL``       the document evidences the right *kind* of thing, short of the
                  bar asked for -- two years against a three-year minimum, or a
                  skill supported only by "familiar with".
``NO_EVIDENCE``   the document shows nothing for this requirement. A statement
                  about the document, never about the person (ADR-0002).
``NEEDS_REVIEW``  the document says something here that cannot be resolved
                  safely -- a grade with no scale, or a skill outside the
                  vocabulary. Distinct from absence on purpose: reporting a
                  limit of our own reading as a fact about the candidate is the
                  failure this project exists to avoid.

`NEEDS_REVIEW` is never a quieter way of saying no. If the engine can decide, it
decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from app.core.enums import MatchVerdict
from app.services import skill_taxonomy
from app.services.cv_facts import CvFacts, Span, total_months

# --------------------------------------------------------------------------
# The vocabulary. Six types from ADR-0012, plus EXPERIENCE_IN_FIELD.
# --------------------------------------------------------------------------

EDUCATION_MIN = "EDUCATION_MIN"
GPA_MIN = "GPA_MIN"
EXPERIENCE_MIN = "EXPERIENCE_MIN"
SKILL = "SKILL"
INTERNSHIP_MIN = "INTERNSHIP_MIN"
LANGUAGE_PRESENT = "LANGUAGE_PRESENT"
EXPERIENCE_IN_FIELD = "EXPERIENCE_IN_FIELD"

SPEC_TYPES = (
    EDUCATION_MIN,
    GPA_MIN,
    EXPERIENCE_MIN,
    SKILL,
    INTERNSHIP_MIN,
    LANGUAGE_PRESENT,
    EXPERIENCE_IN_FIELD,
)

#: Degree names the recruiter can choose, and their rank. Mirrors the levels
#: `cv_facts` reads out of a CV so the two sides compare directly.
DEGREE_CHOICES: dict[str, int] = {
    "D3": 2,
    "S1": 3,
    "S2": 4,
    "S3": 5,
    "Diploma": 2,
    "Bachelor": 3,
    "Master": 4,
    "Doctorate": 5,
}


@dataclass(frozen=True)
class SpecShape:
    """What one criterion type collects.

    One table with three consumers: the request validator, the vocabulary
    endpoint, and the form the interface builds from it. Written down once
    because the same six rules expressed in three places is how a dropdown
    starts offering a field the API will reject.
    """

    #: What the recruiter sees in the "add criterion" menu.
    label: str
    #: Which controlled list the subject is chosen from, if the type takes one.
    subject_source: str | None = None
    #: What the number means, if the type takes one.
    threshold_unit: str | None = None
    #: Whether that number must be supplied.
    threshold_required: bool = False
    #: GPA only: the recruiter also states the scale, because we never assume one.
    needs_scale: bool = False


SPEC_SHAPES: dict[str, SpecShape] = {
    EDUCATION_MIN: SpecShape("Minimum degree", subject_source="degrees"),
    GPA_MIN: SpecShape(
        "Minimum GPA",
        threshold_unit="grade",
        threshold_required=True,
        needs_scale=True,
    ),
    EXPERIENCE_MIN: SpecShape(
        "Work experience duration",
        threshold_unit="months",
        threshold_required=True,
    ),
    SKILL: SpecShape("Skill", subject_source="skills"),
    # A threshold is optional here: "has done an internship" is a real criterion
    # on its own, and most graduate adverts ask for nothing more than that.
    INTERNSHIP_MIN: SpecShape("Internship", threshold_unit="months"),
    LANGUAGE_PRESENT: SpecShape("Language", subject_source="languages"),
    # The only type that collects a subject AND a number. Both are required:
    # a field with no duration is just the SKILL criterion, and a duration
    # with no field is just EXPERIENCE_MIN.
    EXPERIENCE_IN_FIELD: SpecShape(
        "Work experience in a field",
        subject_source="skills",
        threshold_unit="months",
        threshold_required=True,
    ),
}

#: How far below the bar still counts as partial. Applied to durations only,
#: where "nearly there" is a distinction a recruiter actually makes; a grade or
#: a degree is compared exactly.
PARTIAL_DURATION_RATIO = Decimal("0.6")


@dataclass(frozen=True)
class RequirementSpec:
    """One structured criterion. Exactly the shape the recruiter filled in."""

    spec_type: str
    subject: str | None = None
    threshold_value: Decimal | None = None
    #: GPA only: the scale the recruiter says the threshold is out of.
    scale: Decimal | None = None

    def __post_init__(self) -> None:
        if self.spec_type not in SPEC_TYPES:
            raise ValueError(f"unknown spec_type {self.spec_type!r}")


@dataclass(frozen=True)
class MatchOutcome:
    verdict: MatchVerdict
    #: The CV line supporting the verdict. None for NO_EVIDENCE by construction,
    #: and for NEEDS_REVIEW when there is nothing to point at.
    span: Span | None
    #: One sentence about the document. Never a claim about the person.
    reason: str


def _no_evidence(what: str) -> MatchOutcome:
    return MatchOutcome(MatchVerdict.NO_EVIDENCE, None, f"The CV shows no evidence of {what}.")


# --------------------------------------------------------------------------
# Per-type matchers
# --------------------------------------------------------------------------


def _match_education(spec: RequirementSpec, facts: CvFacts) -> MatchOutcome:
    required = DEGREE_CHOICES.get(spec.subject or "")
    if required is None:
        return MatchOutcome(
            MatchVerdict.NEEDS_REVIEW,
            None,
            f"{spec.subject!r} is not a degree this screener recognises.",
        )
    highest = facts.highest_education
    if highest is None:
        return _no_evidence("a completed qualification")
    if highest.level >= required:
        return MatchOutcome(
            MatchVerdict.MATCHED,
            highest.span,
            "The CV states a qualification at or above the level asked for.",
        )
    # A lower degree is not partial evidence of a higher one -- the document
    # simply does not show the qualification that was asked for.
    return _no_evidence(f"a qualification at {spec.subject} level or above")


def _match_gpa(spec: RequirementSpec, facts: CvFacts) -> MatchOutcome:
    if spec.threshold_value is None:
        return MatchOutcome(MatchVerdict.NEEDS_REVIEW, None, "No minimum grade was set.")
    if spec.scale is None:
        # A bare number is not a grade requirement. Without the scale the
        # recruiter meant, "at least 3.0" cannot be compared with anything: the
        # CV's own scale would be adopted by default, so a 3.2 out of 5 would
        # satisfy a minimum that was written thinking of 4 -- over-crediting on
        # an assumption nobody made. The API and `requirements.validate_spec`
        # both refuse such a criterion at creation; this is the same refusal
        # made by the matcher, so a row that reaches here some other way is
        # reported as unresolved rather than answered wrongly.
        return MatchOutcome(
            MatchVerdict.NEEDS_REVIEW,
            None,
            "The minimum grade was set without the scale it is out of, so no "
            "grade in the CV can be compared with it.",
        )
    if not facts.gpa:
        return _no_evidence("a grade point average")

    # Prefer an entry whose scale is stated; only fall back to an unscaled one.
    scaled = [item for item in facts.gpa if item.scale is not None]
    entry = scaled[0] if scaled else facts.gpa[0]

    if entry.scale is None:
        # 3.2 is strong out of 4 and ordinary out of 5. Assuming either would be
        # inventing the half of the fact the document withheld.
        return MatchOutcome(
            MatchVerdict.NEEDS_REVIEW,
            entry.span,
            "The CV states a grade but not the scale it is out of, so it cannot "
            "be compared with the minimum asked for.",
        )
    if spec.scale is not None and entry.scale != spec.scale:
        return MatchOutcome(
            MatchVerdict.NEEDS_REVIEW,
            entry.span,
            f"The CV states a grade out of {entry.scale}, but the minimum was set "
            f"out of {spec.scale}. Converting between scales would be a guess.",
        )
    if entry.value >= spec.threshold_value:
        return MatchOutcome(
            MatchVerdict.MATCHED, entry.span, "The CV states a grade at or above the minimum."
        )
    return MatchOutcome(
        MatchVerdict.PARTIAL, entry.span, "The CV states a grade below the minimum asked for."
    )


def _partial_floor(required: Decimal) -> int | None:
    """The shortest duration that still counts as partial, or None for no band.

    Measured in whole months, because a fraction of a month is not a thing a
    CV states or a recruiter means. The old form compared against
    ``required * 0.6`` directly, so a one-month minimum had a partial band
    starting at 0.6 months -- which made the criterion satisfiable by anything
    at all and was the reason a one-month minimum read as no minimum.

    Returns None when rounding leaves no room between the floor and the
    minimum: for a short bar there is no sensible "nearly", and the honest
    answer is that the candidate either has the month or does not.
    """
    floor = max(
        1, int((required * PARTIAL_DURATION_RATIO).to_integral_value(rounding=ROUND_CEILING))
    )
    return floor if floor < required else None


def _duration_outcome(
    months: int, required: Decimal, span: Span | None, subject: str
) -> MatchOutcome:
    if span is None:
        return _no_evidence(subject)
    if Decimal(months) >= required:
        return MatchOutcome(
            MatchVerdict.MATCHED, span, f"Dated entries total {months} months of {subject}."
        )
    floor = _partial_floor(required)
    if floor is not None and months >= floor:
        return MatchOutcome(
            MatchVerdict.PARTIAL,
            span,
            f"Dated entries total {months} months of {subject}, short of the minimum.",
        )
    return _no_evidence(f"{int(required)} months of {subject}")


def _match_experience(spec: RequirementSpec, facts: CvFacts) -> MatchOutcome:
    if spec.threshold_value is None:
        return MatchOutcome(MatchVerdict.NEEDS_REVIEW, None, "No minimum duration was set.")
    if not facts.roles:
        return _no_evidence("dated professional experience")
    months = total_months(facts.roles)
    return _duration_outcome(months, spec.threshold_value, facts.roles[0].span, "experience")


def _match_experience_in_field(spec: RequirementSpec, facts: CvFacts) -> MatchOutcome:
    """Duration, counting only the roles whose own entry evidences the field.

    The criterion EXPERIENCE_MIN could not express: four years of retail
    answered an accounting vacancy exactly as well as four years of accounting,
    because duration is all it asks.

    What makes this readable rather than a judgement about industries is where
    the field comes from. A role counts when the CV's own entry for it claims a
    supported skill -- and "claims" is decided by `extract_skills`, so a denial,
    a pasted advert or a requirement-shaped sentence inside the entry does not
    count, exactly as it does not count anywhere else.

    Three outcomes are kept apart on purpose. No dated work at all is absence.
    Dated work that never mentions the field is also absence -- of experience
    *in that field*, which is what was asked. Dated work in the field but short
    of the bar is partial, the same as any other duration.
    """
    name = spec.subject or ""
    if not skill_taxonomy.is_supported(name):
        return MatchOutcome(
            MatchVerdict.NEEDS_REVIEW,
            None,
            f"{name!r} is not in the skills this screener can evaluate, so "
            "experience in it cannot be measured.",
        )
    if spec.threshold_value is None:
        return MatchOutcome(MatchVerdict.NEEDS_REVIEW, None, "No minimum duration was set.")
    if not facts.roles:
        return _no_evidence("dated professional experience")

    in_field = [role for role in facts.roles if role.evidences(facts.skills, name)]
    if not in_field:
        # Two very different documents reach this point, and calling both
        # "no evidence" would be the mistake this engine exists to avoid.
        #
        # A CV that never mentions the skill at all shows nothing, and absence
        # is the honest answer. A CV that lists the skill under SKILLS and
        # separately lists four years of dated roles shows both halves and
        # simply does not join them up -- it never says which role used it.
        # That is not absence, it is a question the document does not answer,
        # and most CVs are written that way.
        claimed_elsewhere = any(item.name == name for item in facts.skills)
        if claimed_elsewhere:
            return MatchOutcome(
                MatchVerdict.NEEDS_REVIEW,
                next(item.span for item in facts.skills if item.name == name),
                f"The CV claims {name} and lists dated roles, but does not say which "
                f"role used it, so the time spent on {name} cannot be measured.",
            )
        return _no_evidence(f"dated experience in {name}")

    months = total_months(in_field)
    return _duration_outcome(months, spec.threshold_value, in_field[0].span, f"{name} experience")


def _match_internship(spec: RequirementSpec, facts: CvFacts) -> MatchOutcome:
    internships = [role for role in facts.roles if role.is_internship]
    if not internships:
        return _no_evidence("an internship")
    if spec.threshold_value is None:
        # Presence was all that was asked for.
        return MatchOutcome(
            MatchVerdict.MATCHED, internships[0].span, "The CV states an internship."
        )
    months = total_months(internships)
    return _duration_outcome(months, spec.threshold_value, internships[0].span, "internship")


def _match_skill(spec: RequirementSpec, facts: CvFacts) -> MatchOutcome:
    name = spec.subject or ""
    if not skill_taxonomy.is_supported(name):
        # The requirement can never be answered, which is not the same as the
        # candidate lacking the skill. Saying NO_EVIDENCE here would blame the
        # candidate for a gap in our vocabulary.
        return MatchOutcome(
            MatchVerdict.NEEDS_REVIEW,
            None,
            f"{name!r} is not in the skills this screener can evaluate.",
        )
    found = [item for item in facts.skills if item.name == name]
    if not found:
        return _no_evidence(name)
    substantive = [item for item in found if item.substantive]
    if substantive:
        return MatchOutcome(MatchVerdict.MATCHED, substantive[0].span, f"The CV evidences {name}.")
    return MatchOutcome(
        MatchVerdict.PARTIAL,
        found[0].span,
        f"The CV mentions {name} as exposure or training rather than applied work.",
    )


def _match_language(spec: RequirementSpec, facts: CvFacts) -> MatchOutcome:
    name = spec.subject or ""
    if name not in skill_taxonomy.LANGUAGES:
        return MatchOutcome(
            MatchVerdict.NEEDS_REVIEW,
            None,
            f"{name!r} is not in the languages this screener can evaluate.",
        )
    found = [item for item in facts.languages if item.name == name]
    if not found:
        return _no_evidence(f"the {name} language")
    return MatchOutcome(MatchVerdict.MATCHED, found[0].span, f"The CV names {name}.")


_MATCHERS = {
    EDUCATION_MIN: _match_education,
    GPA_MIN: _match_gpa,
    EXPERIENCE_MIN: _match_experience,
    SKILL: _match_skill,
    INTERNSHIP_MIN: _match_internship,
    LANGUAGE_PRESENT: _match_language,
    EXPERIENCE_IN_FIELD: _match_experience_in_field,
}


def match(spec: RequirementSpec, facts: CvFacts) -> MatchOutcome:
    """One structured requirement against one CV's facts."""
    return _MATCHERS[spec.spec_type](spec, facts)


def describe(spec: RequirementSpec) -> str:
    """The human-readable rendering stored on `requirement.text`.

    Kept as text so every existing display, export and score breakdown keeps
    working unchanged against a structured requirement.
    """
    if spec.spec_type == EDUCATION_MIN:
        return f"Minimum degree: {spec.subject}"
    if spec.spec_type == GPA_MIN:
        scale = f" / {spec.scale}" if spec.scale is not None else ""
        return f"Minimum GPA: {spec.threshold_value}{scale}"
    if spec.spec_type == EXPERIENCE_MIN:
        return f"At least {_months(spec.threshold_value)} of professional experience"
    if spec.spec_type == INTERNSHIP_MIN:
        if spec.threshold_value is None:
            return "Internship experience"
        return f"At least {_months(spec.threshold_value)} of internship experience"
    if spec.spec_type == EXPERIENCE_IN_FIELD:
        return f"At least {_months(spec.threshold_value)} of {spec.subject} experience"
    if spec.spec_type == SKILL:
        return f"Skill: {spec.subject}"
    return f"Language: {spec.subject}"


def _months(value: Decimal | None) -> str:
    if value is None:
        return "any duration"
    months = int(value)
    if months % 12 == 0 and months >= 12:
        years = months // 12
        return f"{years} year" + ("s" if years != 1 else "")
    return f"{months} month" + ("s" if months != 1 else "")


__all__ = [
    "DEGREE_CHOICES",
    "EDUCATION_MIN",
    "EXPERIENCE_IN_FIELD",
    "EXPERIENCE_MIN",
    "GPA_MIN",
    "INTERNSHIP_MIN",
    "LANGUAGE_PRESENT",
    "SKILL",
    "SPEC_SHAPES",
    "SPEC_TYPES",
    "MatchOutcome",
    "RequirementSpec",
    "SpecShape",
    "describe",
    "match",
]
