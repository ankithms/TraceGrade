from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.trace import ExecutionStatus, execution_status, json_payload

if TYPE_CHECKING:
    from app.models.trace import Trace


class SpanKind(StrEnum):
    LLM = "llm"
    OPERATION = "operation"


class Span(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "spans"
    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_spans_project_external_id"),
        UniqueConstraint("id", "trace_id", "project_id", name="uq_spans_id_trace_project"),
        ForeignKeyConstraint(
            ["trace_id", "project_id"],
            ["traces.id", "traces.project_id"],
            name="fk_spans_trace_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_span_id", "trace_id", "project_id"],
            ["spans.id", "spans.trace_id", "spans.project_id"],
            name="fk_spans_parent_trace_project",
            ondelete="CASCADE",
        ),
        CheckConstraint("parent_span_id != id", name="parent_not_self"),
        *(
            CheckConstraint(f"{field} >= 0", name=f"{field}_nonnegative")
            for field in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "latency_ms",
                "estimated_cost_usd",
            )
        ),
        Index("ix_spans_trace_id_started_at", "trace_id", "started_at"),
    )

    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    parent_span_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[SpanKind] = mapped_column(
        Enum(
            SpanKind,
            name="span_kind",
            values_callable=lambda enum: [item.value for item in enum],
            create_constraint=True,
            validate_strings=True,
        )
    )
    input: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        json_payload, nullable=True
    )
    output: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        json_payload, nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    total_tokens: Mapped[int | None]
    latency_ms: Mapped[int | None]
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    status: Mapped[ExecutionStatus] = mapped_column(execution_status)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict] = mapped_column(json_payload, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    trace: Mapped["Trace"] = relationship(
        back_populates="spans", foreign_keys=[trace_id, project_id]
    )
