"""What the screener can actually be asked (ADR-0012).

One endpoint, serving the closed vocabulary the criteria builder offers. It
exists so the boundary is visible in the interface rather than discovered by a
recruiter whose criterion silently comes back unanswerable: the dropdowns are
built from this list, and anything absent from it is shown as unsupported.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.enums import RequirementSpecType
from app.schemas.api.criteria import (
    CriteriaVocabularyResponse,
    CriterionTypeResponse,
    DegreeOption,
    SkillGroupResponse,
    SkillOption,
)
from app.services import skill_taxonomy, structured_match

router = APIRouter(prefix="/api/criteria", tags=["criteria"])


@router.get(
    "/vocabulary",
    response_model=CriteriaVocabularyResponse,
    summary="The supported screening vocabulary",
    description=(
        "Every criterion type, skill, language and degree level this screener "
        "can evaluate, plus the fields each type collects. The list is "
        "deliberately small and hand-curated: a term that is not here cannot be "
        "screened for, and the interface says so rather than accepting a "
        "criterion it could never answer.\n\n"
        "Static for a given build — it depends on no job, no candidate and no "
        "database — so a client may fetch it once and cache it."
    ),
)
def get_vocabulary() -> CriteriaVocabularyResponse:
    return CriteriaVocabularyResponse(
        spec_types=[
            CriterionTypeResponse(
                spec_type=RequirementSpecType(spec_type),
                label=shape.label,
                subject_source=shape.subject_source,
                threshold_unit=shape.threshold_unit,
                threshold_required=shape.threshold_required,
                needs_scale=shape.needs_scale,
            )
            for spec_type, shape in structured_match.SPEC_SHAPES.items()
        ],
        skills=[
            SkillOption(name=name, family=skill_taxonomy.family_of(name))
            for name in skill_taxonomy.supported_skills()
        ],
        languages=skill_taxonomy.supported_languages(),
        degrees=[
            DegreeOption(name=name, rank=rank)
            for name, rank in sorted(
                structured_match.DEGREE_CHOICES.items(),
                key=lambda item: (item[1], item[0]),
            )
        ],
        skill_groups=[
            SkillGroupResponse(name=name, skills=list(members))
            for name, members in skill_taxonomy.SKILL_GROUPS.items()
        ],
    )
