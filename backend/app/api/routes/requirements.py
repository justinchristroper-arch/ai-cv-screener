"""Endpoints addressing a single requirement by id.

Separate router from `jobs.py` because these paths are rooted at
`/api/requirements/{id}` rather than under a job — matching the API surface in
docs/architecture.md section 9.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import SessionDep
from app.schemas.api.jobs import RequirementResponse, RequirementUpdateRequest
from app.services import requirements

router = APIRouter(prefix="/api/requirements", tags=["requirements"])


@router.patch(
    "/{requirement_id}",
    response_model=RequirementResponse,
    summary="Edit a requirement",
    description=(
        "Weight and the must-have flag can always be edited, confirmed or not — "
        "they do not affect verdicts, only the arithmetic applied to them. Text "
        "and category are frozen once the job is confirmed, because changing "
        "them changes what a verdict means; unconfirm the job first."
    ),
    responses={
        404: {"description": "Requirement does not exist"},
        409: {"description": "Text or category edited while the job is confirmed"},
    },
)
def update_requirement(
    requirement_id: uuid.UUID, payload: RequirementUpdateRequest, db: SessionDep
) -> RequirementResponse:
    requirement = requirements.update_requirement(
        db,
        requirement_id,
        text=payload.text,
        category=payload.category,
        must_have=payload.must_have,
        weight=payload.weight,
    )
    return RequirementResponse.model_validate(requirement)


@router.delete(
    "/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a requirement",
    responses={
        404: {"description": "Requirement does not exist"},
        409: {"description": "Requirements are confirmed; unconfirm first"},
    },
)
def delete_requirement(requirement_id: uuid.UUID, db: SessionDep) -> Response:
    requirements.delete_requirement(db, requirement_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
