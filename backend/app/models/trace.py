from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.span import Span


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


execution_status = Enum(
    ExecutionStatus,
    name="execution_status",
    values_callable=lambda enum: [item.value for item in enum],
    create_constraint=True,
    validate_strings=True,
)
json_payload = JSON().with_variant(JSONB(), "postgresql")


class Trace(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_traces_project_external_id"),
        UniqueConstraint("id", "project_id", name="uq_traces_id_project_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[ExecutionStatus] = mapped_column(execution_status)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict] = mapped_column(json_payload, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="traces")
    spans: Mapped[list["Span"]] = relationship(
        back_populates="trace",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="[Span.trace_id, Span.project_id]",
    )
