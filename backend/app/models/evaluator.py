from uuid import UUID

from sqlalchemy import JSON, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from tracegrade_evaluators.config import EvaluatorType


class Evaluator(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluators"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_evaluators_project_name"),)

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[EvaluatorType] = mapped_column(
        Enum(
            EvaluatorType,
            name="evaluator_type",
            values_callable=lambda items: [i.value for i in items],
        )
    )
    config: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
