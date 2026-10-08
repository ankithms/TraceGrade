import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DbSession, SettingsDependency, require_admin_key
from app.api.errors import ApiError
from app.core.security import generate_api_key
from app.models import ApiKey, Project
from app.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyListResponse,
    ApiKeyResponse,
)
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    dependencies=[Depends(require_admin_key)],
)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")[:63]


async def get_project_or_404(project_id: UUID, session: DbSession) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", "Project not found")
    return project


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, session: DbSession) -> Project:
    slug = payload.slug or slugify(payload.name)
    if not slug:
        raise ApiError(422, "invalid_slug", "A valid project slug is required")

    project = Project(name=payload.name, slug=slug)
    session.add(project)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ApiError(409, "project_slug_conflict", "Project slug already exists") from error

    await session.refresh(project)
    return project


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProjectListResponse:
    projects = list(
        await session.scalars(
            select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
        )
    )
    total = await session.scalar(select(func.count()).select_from(Project))
    return ProjectListResponse(items=projects, total=total or 0)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: UUID, session: DbSession) -> Project:
    return await get_project_or_404(project_id, session)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: DbSession,
) -> Project:
    project = await get_project_or_404(project_id, session)
    project.name = payload.name
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: UUID, session: DbSession) -> Response:
    project = await get_project_or_404(project_id, session)
    await session.delete(project)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["api-keys"],
)
async def create_api_key(
    project_id: UUID,
    payload: ApiKeyCreate,
    session: DbSession,
    settings: SettingsDependency,
) -> ApiKeyCreatedResponse:
    await get_project_or_404(project_id, session)

    for _attempt in range(3):
        generated = generate_api_key(settings.api_key_hash_secret)
        api_key = ApiKey(
            project_id=project_id,
            name=payload.name,
            prefix=generated.prefix,
            secret_hash=generated.secret_hash,
        )
        session.add(api_key)
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
    else:
        raise ApiError(503, "api_key_generation_failed", "Could not generate an API key")

    await session.refresh(api_key)
    return ApiKeyCreatedResponse(
        id=api_key.id,
        project_id=api_key.project_id,
        name=api_key.name,
        prefix=api_key.prefix,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
        created_at=api_key.created_at,
        key=generated.plaintext,
    )


@router.get(
    "/{project_id}/api-keys",
    response_model=ApiKeyListResponse,
    tags=["api-keys"],
)
async def list_api_keys(
    project_id: UUID,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApiKeyListResponse:
    await get_project_or_404(project_id, session)
    filters = ApiKey.project_id == project_id
    api_keys = list(
        await session.scalars(
            select(ApiKey)
            .where(filters)
            .order_by(ApiKey.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    total = await session.scalar(select(func.count()).select_from(ApiKey).where(filters))
    return ApiKeyListResponse(
        items=[ApiKeyResponse.model_validate(api_key) for api_key in api_keys],
        total=total or 0,
    )


@router.delete(
    "/{project_id}/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["api-keys"],
)
async def revoke_api_key(project_id: UUID, key_id: UUID, session: DbSession) -> Response:
    api_key = await session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == project_id)
    )
    if api_key is None:
        raise ApiError(404, "api_key_not_found", "API key not found")

    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
