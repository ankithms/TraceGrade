"""Pure data contracts; no database, FastAPI, Temporal, or application execution."""

import json
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from tracegrade_evaluators.config import EvaluatorType, validate_config

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Metric = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
Target = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,62}$")]


def bounded_json(value, limit):
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 64:
            raise ValueError("JSON must not exceed 64 levels of nesting")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limit:
        raise ValueError("JSON payload exceeds size limit")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def validate_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Only integer schema_version 1 is supported")
        return value


class RunConfig(Contract):
    schema_version: Literal[1] = 1
    target: Target
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    max_concurrency: int = Field(default=5, strict=True, ge=1, le=20)
    application_timeout_seconds: int = Field(default=60, strict=True, ge=1, le=300)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value):
        return bounded_json(value, 16 * 1024)


class ApplicationRequest(Contract):
    schema_version: Literal[1] = 1
    experiment_id: UUID
    test_case_id: UUID
    version_label: Name
    input: JsonValue
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def idempotency_key(self) -> str:
        # Stable across activity attempts and process restarts.
        return f"tracegrade:{self.experiment_id}:{self.test_case_id}"

    @field_validator("input")
    @classmethod
    def validate_input(cls, value):
        return bounded_json(value, 64 * 1024)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value):
        return bounded_json(value, 16 * 1024)


class ApplicationResponse(Contract):
    schema_version: Literal[1] = 1
    output: JsonValue
    model: Name | None = None
    prompt_tokens: Metric | None = None
    completion_tokens: Metric | None = None
    total_tokens: Metric | None = None
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=10)

    @field_validator("output")
    @classmethod
    def validate_output(cls, value):
        return bounded_json(value, 64 * 1024)


class CaseSnapshot(Contract):
    schema_version: Literal[1] = 1
    name: Name
    input: JsonValue
    expected_output: JsonValue = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("input", "expected_output")
    @classmethod
    def validate_payload(cls, value):
        return bounded_json(value, 64 * 1024)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value):
        return bounded_json(value, 16 * 1024)


class DatasetSnapshot(Contract):
    schema_version: Literal[1] = 1
    name: Name
    description: str | None = Field(default=None, max_length=4096)


class EvaluatorSnapshot(Contract):
    schema_version: Literal[1] = 1
    name: Name
    type: EvaluatorType
    config: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_configuration(self):
        bounded_json(self.config, 16 * 1024)
        self.config = validate_config(self.type, self.config).model_dump()
        return self
