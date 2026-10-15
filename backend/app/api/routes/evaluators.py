from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DbSession
from app.api.errors import ApiError
from app.api.routes.datasets import CurrentProject, PageLimit, PageOffset
from app.models.evaluator import Evaluator
from app.schemas.evaluator import (
    EvaluatorCreate,
    EvaluatorListResponse,
    EvaluatorResponse,
    EvaluatorUpdate,
)
from app.services.source_locks import lock_source_project, require_evaluator_deletable
from tracegrade_evaluators.config import validate_config

router = APIRouter(prefix="/evaluators", tags=["evaluators"])


async def get_evaluator_or_404(
    evaluator_id: UUID,
    project: CurrentProject,
    session: DbSession,
) -> Evaluator:
    evaluator = await session.scalar(
        select(Evaluator).where(Evaluator.id == evaluator_id, Evaluator.project_id == project.id)
    )
    if evaluator is None:
        raise ApiError(404, "evaluator_not_found", "Evaluator not found")
    return evaluator


async def save_evaluator(evaluator: Evaluator, session: DbSession) -> Evaluator:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ApiError(
            409, "evaluator_conflict", "Evaluator conflicts with existing data"
        ) from error
    await session.refresh(evaluator)
    return evaluator


@router.post("", response_model=EvaluatorResponse, status_code=201)
async def create_evaluator(payload: EvaluatorCreate, project: CurrentProject, session: DbSession):
    await lock_source_project(session, project.id)
    evaluator = Evaluator(project_id=project.id, **payload.model_dump())
    session.add(evaluator)
    return await save_evaluator(evaluator, session)


@router.get("", response_model=EvaluatorListResponse)
async def list_evaluators(
    project: CurrentProject,
    session: DbSession,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    filters = Evaluator.project_id == project.id
    items = await session.scalars(
        select(Evaluator)
        .where(filters)
        .order_by(Evaluator.created_at.desc(), Evaluator.id.desc())
        .limit(limit)
        .offset(offset)
    )
    total = await session.scalar(select(func.count()).select_from(Evaluator).where(filters))
    return EvaluatorListResponse(items=list(items), total=total or 0)


@router.get("/{evaluator_id}", response_model=EvaluatorResponse)
async def get_evaluator(evaluator_id: UUID, project: CurrentProject, session: DbSession):
    return await get_evaluator_or_404(evaluator_id, project, session)


@router.patch("/{evaluator_id}", response_model=EvaluatorResponse)
async def update_evaluator(
    evaluator_id: UUID,
    payload: EvaluatorUpdate,
    project: CurrentProject,
    session: DbSession,
):
    await lock_source_project(session, project.id)
    evaluator = await get_evaluator_or_404(evaluator_id, project, session)
    fields = payload.model_dump(exclude_unset=True)
    if "config" in fields:
        try:
            fields["config"] = validate_config(evaluator.type, fields["config"]).model_dump()
        except ValidationError as error:
            raise ApiError(422, "validation_error", "Invalid evaluator configuration") from error
    for field, value in fields.items():
        setattr(evaluator, field, value)
    return await save_evaluator(evaluator, session)


@router.delete("/{evaluator_id}", status_code=204)
async def delete_evaluator(evaluator_id: UUID, project: CurrentProject, session: DbSession):
    await lock_source_project(session, project.id)
    evaluator = await get_evaluator_or_404(evaluator_id, project, session)
    await require_evaluator_deletable(session, project.id, evaluator_id)
    await session.delete(evaluator)
    await session.commit()
    return Response(status_code=204)
