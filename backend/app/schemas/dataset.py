import json
from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.trace import utc_datetime

ResourceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]


def bound_json(value, limit: int):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > limit:
        raise ValueError(f"JSON payload exceeds {limit} bytes")
    return value


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetCreate(RequestModel):
    name: ResourceName
    description: str | None = Field(default=None, max_length=4096)


class DatasetUpdate(RequestModel):
    name: ResourceName | None = None
    description: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not self.model_fields_set or ("name" in self.model_fields_set and self.name is None):
            raise ValueError("Supply an update with a non-null name")
        return self


class TimestampResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamps(cls, value):
        return utc_datetime(value)


class DatasetResponse(TimestampResponse):
    project_id: UUID
    name: str
    description: str | None


class DatasetListResponse(BaseModel):
    items: list[DatasetResponse]
    total: int


class TestCaseCreate(RequestModel):
    __test__ = False
    name: ResourceName
    input: JsonValue
    expected_output: JsonValue = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("input", "expected_output")
    @classmethod
    def bound_payload(cls, value):
        return bound_json(value, 64 * 1024)

    @field_validator("metadata")
    @classmethod
    def bound_metadata(cls, value):
        return bound_json(value, 16 * 1024)


class TestCaseUpdate(TestCaseCreate):
    name: ResourceName | None = None
    input: JsonValue = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not self.model_fields_set or ("name" in self.model_fields_set and self.name is None):
            raise ValueError("Supply an update with a non-null name")
        return self


class TestCaseResponse(TimestampResponse):
    __test__ = False
    dataset_id: UUID
    name: str
    input: JsonValue
    expected_output: JsonValue
    metadata: dict[str, JsonValue] = Field(
        validation_alias=AliasChoices("case_metadata", "metadata")
    )


class TestCaseListResponse(BaseModel):
    __test__ = False
    items: list[TestCaseResponse]
    total: int
