from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
