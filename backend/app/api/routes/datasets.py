from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DbSession, get_current_project
from app.api.errors import ApiError
from app.models import Project
from app.models.dataset import Dataset, TestCase
from app.schemas.dataset import (
    DatasetCreate,
    DatasetListResponse,
    DatasetResponse,
    DatasetUpdate,
    TestCaseCreate,
    TestCaseListResponse,
    TestCaseResponse,
    TestCaseUpdate,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])
CurrentProject = Annotated[Project, Depends(get_current_project)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


async def get_dataset_or_404(dataset_id: UUID, project: Project, session: DbSession) -> Dataset:
    dataset = await session.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project.id)
    )
    if dataset is None:
        raise ApiError(404, "dataset_not_found", "Dataset not found")
    return dataset


async def get_case_or_404(
    dataset_id: UUID,
    case_id: UUID,
    project: Project,
    session: DbSession,
) -> TestCase:
    await get_dataset_or_404(dataset_id, project, session)
    case = await session.scalar(
        select(TestCase).where(TestCase.id == case_id, TestCase.dataset_id == dataset_id)
    )
    if case is None:
        raise ApiError(404, "test_case_not_found", "Test case not found")
    return case


async def save_dataset(dataset: Dataset, session: DbSession) -> Dataset:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ApiError(409, "dataset_conflict", "Dataset conflicts with existing data") from error
    await session.refresh(dataset)
    return dataset


@router.post("", response_model=DatasetResponse, status_code=201)
async def create_dataset(payload: DatasetCreate, project: CurrentProject, session: DbSession):
    dataset = Dataset(project_id=project.id, **payload.model_dump())
    session.add(dataset)
    return await save_dataset(dataset, session)


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    project: CurrentProject,
    session: DbSession,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    filters = Dataset.project_id == project.id
    items = await session.scalars(
        select(Dataset)
        .where(filters)
        .order_by(Dataset.created_at.desc(), Dataset.id.desc())
        .limit(limit)
        .offset(offset)
    )
    total = await session.scalar(select(func.count()).select_from(Dataset).where(filters))
    return DatasetListResponse(items=list(items), total=total or 0)


@router.get("/{dataset_id}", response_model=DatasetResponse)
async def get_dataset(dataset_id: UUID, project: CurrentProject, session: DbSession):
    return await get_dataset_or_404(dataset_id, project, session)


@router.patch("/{dataset_id}", response_model=DatasetResponse)
async def update_dataset(
    dataset_id: UUID,
    payload: DatasetUpdate,
    project: CurrentProject,
    session: DbSession,
):
    dataset = await get_dataset_or_404(dataset_id, project, session)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(dataset, field, value)
    return await save_dataset(dataset, session)


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(dataset_id: UUID, project: CurrentProject, session: DbSession):
    dataset = await get_dataset_or_404(dataset_id, project, session)
    await session.delete(dataset)
    await session.commit()
    return Response(status_code=204)


@router.post("/{dataset_id}/cases", response_model=TestCaseResponse, status_code=201)
async def create_case(
    dataset_id: UUID,
    payload: TestCaseCreate,
    project: CurrentProject,
    session: DbSession,
):
    await get_dataset_or_404(dataset_id, project, session)
    fields = payload.model_dump()
    fields["case_metadata"] = fields.pop("metadata")
    case = TestCase(dataset_id=dataset_id, **fields)
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


@router.get("/{dataset_id}/cases", response_model=TestCaseListResponse)
async def list_cases(
    dataset_id: UUID,
    project: CurrentProject,
    session: DbSession,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    await get_dataset_or_404(dataset_id, project, session)
    filters = TestCase.dataset_id == dataset_id
    items = await session.scalars(
        select(TestCase)
        .where(filters)
        .order_by(TestCase.created_at.desc(), TestCase.id.desc())
        .limit(limit)
        .offset(offset)
    )
    total = await session.scalar(select(func.count()).select_from(TestCase).where(filters))
    return TestCaseListResponse(items=list(items), total=total or 0)


@router.get("/{dataset_id}/cases/{case_id}", response_model=TestCaseResponse)
async def get_case(dataset_id: UUID, case_id: UUID, project: CurrentProject, session: DbSession):
    return await get_case_or_404(dataset_id, case_id, project, session)


@router.patch("/{dataset_id}/cases/{case_id}", response_model=TestCaseResponse)
async def update_case(
    dataset_id: UUID,
    case_id: UUID,
    payload: TestCaseUpdate,
    project: CurrentProject,
    session: DbSession,
):
    case = await get_case_or_404(dataset_id, case_id, project, session)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(case, "case_metadata" if field == "metadata" else field, value)
    await session.commit()
    await session.refresh(case)
    return case


@router.delete("/{dataset_id}/cases/{case_id}", status_code=204)
async def delete_case(dataset_id: UUID, case_id: UUID, project: CurrentProject, session: DbSession):
    case = await get_case_or_404(dataset_id, case_id, project, session)
    await session.delete(case)
    await session.commit()
    return Response(status_code=204)
