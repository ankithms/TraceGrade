"""Experiment-owned records. Source UUIDs identify frozen inputs, not live rows.

Live dataset/case/evaluator ownership is checked when taking the snapshot (the
next delivery slice). Avoiding live-source FKs lets terminal runs retain their
history when source resources are subsequently deleted.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class ExperimentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CaseStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScoreStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


def status_enum(enum, name):
    return Enum(
        enum,
        name=name,
        values_callable=lambda items: [item.value for item in items],
        create_constraint=True,
        validate_strings=True,
    )


json_payload = JSON().with_variant(JSONB(), "postgresql")


class Experiment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_experiments_id_project"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_experiments_project_key"),
        CheckConstraint("total_cases > 0", name="positive_total_cases"),
        CheckConstraint("completed_cases >= 0", name="completed_cases_nonnegative"),
        CheckConstraint("failed_cases >= 0", name="failed_cases_nonnegative"),
        CheckConstraint("cancelled_cases >= 0", name="cancelled_cases_nonnegative"),
        CheckConstraint(
            "completed_cases + failed_cases + cancelled_cases <= total_cases",
            name="progress_within_total",
        ),
        Index("ix_experiments_project_created_at", "project_id", "created_at"),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    dataset_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    name: Mapped[str] = mapped_column(String(120))
    version_label: Mapped[str] = mapped_column(String(120))
    status: Mapped[ExperimentStatus] = mapped_column(
        status_enum(ExperimentStatus, "experiment_status"),
        default=ExperimentStatus.PENDING,
        server_default="pending",
    )
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    workflow_id: Mapped[str] = mapped_column(String(255), unique=True)
    run_config: Mapped[dict] = mapped_column(json_payload)
    dataset_snapshot: Mapped[dict] = mapped_column(json_payload)
    total_cases: Mapped[int] = mapped_column(Integer)
    completed_cases: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_cases: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancelled_cases: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperimentEvaluator(Base):
    __tablename__ = "experiment_evaluators"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "project_id"],
            ["experiments.id", "experiments.project_id"],
            name="fk_experiment_evaluators_experiment_project",
            ondelete="CASCADE",
        ),
    )

    experiment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    evaluator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    # Includes schema version, name, evaluator type and fully validated config.
    config_snapshot: Mapped[dict] = mapped_column(json_payload)


class ExperimentResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "experiment_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "project_id"],
            ["experiments.id", "experiments.project_id"],
            name="fk_experiment_results_experiment_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint("experiment_id", "test_case_id", name="uq_experiment_results_case"),
        UniqueConstraint("id", "experiment_id", name="uq_experiment_results_id_experiment"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        *(
            CheckConstraint(f"{metric} >= 0", name=f"{metric}_nonnegative")
            for metric in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "latency_ms",
                "estimated_cost_usd",
            )
        ),
        Index("ix_experiment_results_experiment_status", "experiment_id", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    experiment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    test_case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    # Includes schema version, name, input, expected_output and metadata.
    case_snapshot: Mapped[dict] = mapped_column(json_payload)
    status: Mapped[CaseStatus] = mapped_column(
        status_enum(CaseStatus, "experiment_case_status"),
        default=CaseStatus.PENDING,
        server_default="pending",
    )
    output: Mapped[Any] = mapped_column(json_payload, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    # Distinguishes a saved JSON-null output from an application call not yet saved.
    output_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationScore(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_scores"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_result_id", "experiment_id"],
            ["experiment_results.id", "experiment_results.experiment_id"],
            name="fk_evaluation_scores_result_experiment",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["experiment_id", "evaluator_id"],
            ["experiment_evaluators.experiment_id", "experiment_evaluators.evaluator_id"],
            name="fk_evaluation_scores_evaluator_snapshot",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "experiment_result_id", "evaluator_id", name="uq_evaluation_scores_result_evaluator"
        ),
        CheckConstraint("score >= 0 AND score <= 1", name="score_in_range"),
        CheckConstraint("attempt_count >= 1", name="attempt_count_positive"),
        CheckConstraint(
            "(status = 'completed' AND score IS NOT NULL AND passed IS NOT NULL "
            "AND error_message IS NULL) OR (status = 'failed' AND score IS NULL "
            "AND passed IS NULL AND error_message IS NOT NULL)",
            name="score_status_consistent",
        ),
        Index("ix_evaluation_scores_experiment_id", "experiment_id"),
    )

    experiment_result_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    experiment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    evaluator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[ScoreStatus] = mapped_column(status_enum(ScoreStatus, "evaluation_score_status"))
    score: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    rationale: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
