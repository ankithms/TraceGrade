"""Create experiment snapshots results and scores

Revision ID: 20261015_0004
Revises: 20261015_0003
Create Date: 2026-10-15 22:38:21.252704
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261015_0004"
down_revision: str | None = "20261015_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version_label", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "failed",
                "cancelled",
                name="experiment_status",
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column(
            "run_config",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "dataset_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("completed_cases", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_cases", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancelled_cases", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "cancelled_cases >= 0", name=op.f("ck_experiments_cancelled_cases_nonnegative")
        ),
        sa.CheckConstraint(
            "completed_cases + failed_cases + cancelled_cases <= total_cases",
            name=op.f("ck_experiments_progress_within_total"),
        ),
        sa.CheckConstraint(
            "completed_cases >= 0", name=op.f("ck_experiments_completed_cases_nonnegative")
        ),
        sa.CheckConstraint(
            "failed_cases >= 0", name=op.f("ck_experiments_failed_cases_nonnegative")
        ),
        sa.CheckConstraint("total_cases > 0", name=op.f("ck_experiments_positive_total_cases")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_experiments_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
        sa.UniqueConstraint("id", "project_id", name="uq_experiments_id_project"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_experiments_project_key"),
        sa.UniqueConstraint("workflow_id", name=op.f("uq_experiments_workflow_id")),
    )
    op.create_index(
        "ix_experiments_project_created_at",
        "experiments",
        ["project_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "experiment_evaluators",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("evaluator_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "config_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "project_id"],
            ["experiments.id", "experiments.project_id"],
            name="fk_experiment_evaluators_experiment_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "experiment_id", "evaluator_id", name=op.f("pk_experiment_evaluators")
        ),
    )
    op.create_table(
        "experiment_results",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("test_case_id", sa.Uuid(), nullable=False),
        sa.Column(
            "case_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "failed",
                "cancelled",
                name="experiment_case_status",
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "output",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("output_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_experiment_results_attempt_count_nonnegative")
        ),
        sa.CheckConstraint(
            "completion_tokens >= 0",
            name=op.f("ck_experiment_results_completion_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "estimated_cost_usd >= 0",
            name=op.f("ck_experiment_results_estimated_cost_usd_nonnegative"),
        ),
        sa.CheckConstraint(
            "latency_ms >= 0", name=op.f("ck_experiment_results_latency_ms_nonnegative")
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0", name=op.f("ck_experiment_results_prompt_tokens_nonnegative")
        ),
        sa.CheckConstraint(
            "total_tokens >= 0", name=op.f("ck_experiment_results_total_tokens_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "project_id"],
            ["experiments.id", "experiments.project_id"],
            name="fk_experiment_results_experiment_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_results")),
        sa.UniqueConstraint("experiment_id", "test_case_id", name="uq_experiment_results_case"),
        sa.UniqueConstraint("id", "experiment_id", name="uq_experiment_results_id_experiment"),
    )
    op.create_index(
        "ix_experiment_results_experiment_status",
        "experiment_results",
        ["experiment_id", "status"],
        unique=False,
    )
    op.create_table(
        "evaluation_scores",
        sa.Column("experiment_result_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("evaluator_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("completed", "failed", name="evaluation_score_status", create_constraint=True),
            nullable=False,
        ),
        sa.Column("score", sa.Numeric(precision=12, scale=10), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(status = 'completed' AND score IS NOT NULL AND passed IS NOT NULL "
            "AND error_message IS NULL) OR (status = 'failed' AND score IS NULL "
            "AND passed IS NULL AND error_message IS NOT NULL)",
            name=op.f("ck_evaluation_scores_score_status_consistent"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 1", name=op.f("ck_evaluation_scores_attempt_count_positive")
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1", name=op.f("ck_evaluation_scores_score_in_range")
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "evaluator_id"],
            ["experiment_evaluators.experiment_id", "experiment_evaluators.evaluator_id"],
            name="fk_evaluation_scores_evaluator_snapshot",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_result_id", "experiment_id"],
            ["experiment_results.id", "experiment_results.experiment_id"],
            name="fk_evaluation_scores_result_experiment",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_scores")),
        sa.UniqueConstraint(
            "experiment_result_id", "evaluator_id", name="uq_evaluation_scores_result_evaluator"
        ),
    )
    op.create_index(
        "ix_evaluation_scores_experiment_id", "evaluation_scores", ["experiment_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_scores_experiment_id", table_name="evaluation_scores")
    op.drop_table("evaluation_scores")
    op.drop_index("ix_experiment_results_experiment_status", table_name="experiment_results")
    op.drop_table("experiment_results")
    op.drop_table("experiment_evaluators")
    op.drop_index("ix_experiments_project_created_at", table_name="experiments")
    op.drop_table("experiments")
    # Native PostgreSQL enum types outlive their tables unless explicitly dropped.
    for name in ("evaluation_score_status", "experiment_case_status", "experiment_status"):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
