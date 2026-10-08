import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models import ExecutionStatus, SpanKind

ExternalId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
OperationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
TokenCount = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]


def utc_datetime(value: datetime) -> datetime:
    # SQLite does not retain timezone information; stored timestamps are UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TraceIngest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: ExternalId
    name: OperationName
    status: ExecutionStatus = ExecutionStatus.RUNNING
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("started_at", "ended_at")
    @classmethod
    def normalize_timestamps(cls, value):
        return utc_datetime(value) if value is not None else None

    @field_validator("attributes")
    @classmethod
    def bound_attributes(cls, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 16 * 1024:
            raise ValueError("Attributes must be at most 16 KiB of JSON")
        return value

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        return self


class SpanIngest(TraceIngest):
    parent_span_id: UUID | None = None
    kind: SpanKind
    input: JsonValue = None
    output: JsonValue = None
    model: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    prompt_tokens: TokenCount | None = None
    completion_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None
    latency_ms: TokenCount | None = None
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=10)
    error_message: str | None = Field(default=None, max_length=4096)

    @field_validator("input", "output")
    @classmethod
    def bound_payload(cls, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ValueError("Input/output must each be at most 64 KiB of JSON")
        return value


class TraceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    external_id: str
    name: str
    status: ExecutionStatus
    started_at: datetime
    ended_at: datetime | None
    attributes: dict[str, JsonValue]
    created_at: datetime

    @field_validator("started_at", "ended_at", "created_at")
    @classmethod
    def normalize_timestamps(cls, value):
        return utc_datetime(value) if value is not None else None


class SpanResponse(TraceResponse):
    trace_id: UUID
    parent_span_id: UUID | None
    kind: SpanKind
    input: JsonValue
    output: JsonValue
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    estimated_cost_usd: Decimal | None
    error_message: str | None
