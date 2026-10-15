from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, model_validator

from app.schemas.dataset import RequestModel, ResourceName, TimestampResponse, bound_json
from tracegrade_evaluators.config import EvaluatorType, validate_config


class EvaluatorCreate(RequestModel):
    name: ResourceName
    type: EvaluatorType
    config: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        bound_json(self.config, 16 * 1024)
        self.config = validate_config(self.type, self.config).model_dump()
        return self


class EvaluatorUpdate(RequestModel):
    name: ResourceName | None = None
    config: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Supply non-null update fields")
        if self.config is not None:
            bound_json(self.config, 16 * 1024)
        return self


class EvaluatorResponse(TimestampResponse):
    project_id: UUID
    name: str
    type: EvaluatorType
    config: dict[str, JsonValue]


class EvaluatorListResponse(BaseModel):
    items: list[EvaluatorResponse]
    total: int
