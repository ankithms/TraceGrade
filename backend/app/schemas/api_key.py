from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(ApiKeyResponse):
    key: str


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyResponse]
    total: int
