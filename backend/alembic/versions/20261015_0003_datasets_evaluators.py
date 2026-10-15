"""Create datasets, test cases, and evaluators.

Revision ID: 20261015_0003
Revises: 20261008_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261015_0003"
down_revision: str | None = "20261008_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

evaluator_type = postgresql.ENUM(
    "exact_match",
    "contains",
    "json_match",
    "gemini_judge",
    name="evaluator_type",
    create_type=False,
)


def timestamps():
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    evaluator_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("project_id", "name", name="uq_datasets_project_name"),
    )
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"])
    op.create_table(
        "test_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Uuid(),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("input", postgresql.JSONB(), nullable=False),
        sa.Column("expected_output", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        *timestamps(),
    )
    op.create_index("ix_test_cases_dataset_id", "test_cases", ["dataset_id"])
    op.create_table(
        "evaluators",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", evaluator_type, nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("project_id", "name", name="uq_evaluators_project_name"),
    )
    op.create_index("ix_evaluators_project_id", "evaluators", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluators_project_id", table_name="evaluators")
    op.drop_table("evaluators")
    op.drop_index("ix_test_cases_dataset_id", table_name="test_cases")
    op.drop_table("test_cases")
    op.drop_index("ix_datasets_project_id", table_name="datasets")
    op.drop_table("datasets")
    evaluator_type.drop(op.get_bind(), checkfirst=True)
