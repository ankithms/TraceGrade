"""Coordinate snapshot reads with live-source mutations within one project."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.models import Experiment, ExperimentEvaluator, ExperimentStatus, Project

ACTIVE_EXPERIMENT_STATUSES = (ExperimentStatus.PENDING, ExperimentStatus.RUNNING)


async def lock_source_project(session: AsyncSession, project_id: UUID) -> None:
    # All source writers lock before lookup/mutation and keep the lock to commit.
    # PostgreSQL READ COMMITTED then sees a stable set of cooperating source rows
    # across multiple SELECTs. The project FK also serializes project deletion.
    found = await session.scalar(
        select(Project.id).where(Project.id == project_id).with_for_update()
    )
    if found is None:
        raise ApiError(404, "project_not_found", "Project not found")


async def require_dataset_deletable(
    session: AsyncSession,
    project_id: UUID,
    dataset_id: UUID,
) -> None:
    """Check while holding the project source lock through the eventual delete."""
    active = await session.scalar(
        select(Experiment.id)
        .where(
            Experiment.project_id == project_id,
            Experiment.dataset_id == dataset_id,
            Experiment.status.in_(ACTIVE_EXPERIMENT_STATUSES),
        )
        .limit(1)
    )
    if active is not None:
        raise ApiError(409, "dataset_in_use", "Dataset is used by an active experiment")


async def require_evaluator_deletable(
    session: AsyncSession,
    project_id: UUID,
    evaluator_id: UUID,
) -> None:
    """Check while holding the project source lock through the eventual delete."""
    active = await session.scalar(
        select(Experiment.id)
        .join(
            ExperimentEvaluator,
            ExperimentEvaluator.experiment_id == Experiment.id,
        )
        .where(
            Experiment.project_id == project_id,
            ExperimentEvaluator.evaluator_id == evaluator_id,
            Experiment.status.in_(ACTIVE_EXPERIMENT_STATUSES),
        )
        .limit(1)
    )
    if active is not None:
        raise ApiError(409, "evaluator_in_use", "Evaluator is used by an active experiment")
