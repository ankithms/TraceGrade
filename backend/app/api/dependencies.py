import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.core.config import Settings, get_settings
from app.core.security import extract_api_key_prefix, hash_api_key
from app.db.session import get_session
from app.models import ApiKey, Project

DbSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
bearer_scheme = HTTPBearer(auto_error=False)


async def require_admin_key(
    settings: SettingsDependency,
    admin_key: Annotated[str | None, Header(alias="X-TraceGrade-Admin-Key")] = None,
) -> None:
    if admin_key is None or not secrets.compare_digest(admin_key, settings.admin_key):
        raise ApiError(401, "invalid_admin_key", "A valid admin key is required")


async def authenticate_project_api_key(
    api_key: str,
    session: AsyncSession,
    settings: Settings,
) -> Project:
    prefix = extract_api_key_prefix(api_key)
    candidate_hash = hash_api_key(api_key, settings.api_key_hash_secret)
    row = None

    if prefix is not None:
        result = await session.execute(
            select(ApiKey, Project)
            .join(Project, Project.id == ApiKey.project_id)
            .where(ApiKey.prefix == prefix)
        )
        row = result.one_or_none()

    api_key_record, project = row if row is not None else (None, None)
    stored_hash = api_key_record.secret_hash if api_key_record is not None else "0" * 64
    valid_hash = secrets.compare_digest(candidate_hash, stored_hash)
    if (
        api_key_record is None
        or project is None
        or api_key_record.revoked_at is not None
        or not valid_hash
    ):
        raise ApiError(401, "invalid_api_key", "A valid project API key is required")

    api_key_record.last_used_at = datetime.now(UTC)
    await session.commit()
    return project


async def get_current_project(
    session: DbSession,
    settings: SettingsDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Project:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "invalid_api_key", "A valid project API key is required")
    return await authenticate_project_api_key(credentials.credentials, session, settings)
