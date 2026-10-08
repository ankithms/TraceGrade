"""Create traces and spans

Revision ID: 20261008_0002
Revises: 20261006_0001
Create Date: 2026-10-08 21:15:37.697618
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261008_0002"
down_revision: str | None = "20261006_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Both tables share execution_status; manage PostgreSQL types explicitly so the
# second table does not try to create the same type again.
execution_status = postgresql.ENUM(
    "running", "ok", "error", name="execution_status", create_type=False
)
span_kind = postgresql.ENUM("llm", "operation", name="span_kind", create_type=False)


def upgrade() -> None:
    execution_status.create(op.get_bind(), checkfirst=True)
    span_kind.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "traces",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", execution_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attributes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_traces_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_traces")),
        sa.UniqueConstraint("id", "project_id", name="uq_traces_id_project_id"),
        sa.UniqueConstraint("project_id", "external_id", name="uq_traces_project_external_id"),
    )
    op.create_index(op.f("ix_traces_project_id"), "traces", ["project_id"], unique=False)
    op.create_table(
        "spans",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_span_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", span_kind, nullable=False),
        sa.Column(
            "input",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
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
        sa.Column("status", execution_status, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attributes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "completion_tokens >= 0", name=op.f("ck_spans_completion_tokens_nonnegative")
        ),
        sa.CheckConstraint(
            "estimated_cost_usd >= 0", name=op.f("ck_spans_estimated_cost_usd_nonnegative")
        ),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_spans_latency_ms_nonnegative")),
        sa.CheckConstraint("parent_span_id != id", name=op.f("ck_spans_parent_not_self")),
        sa.CheckConstraint("prompt_tokens >= 0", name=op.f("ck_spans_prompt_tokens_nonnegative")),
        sa.CheckConstraint("total_tokens >= 0", name=op.f("ck_spans_total_tokens_nonnegative")),
        sa.ForeignKeyConstraint(
            ["parent_span_id", "trace_id", "project_id"],
            ["spans.id", "spans.trace_id", "spans.project_id"],
            name="fk_spans_parent_trace_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_spans_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trace_id", "project_id"],
            ["traces.id", "traces.project_id"],
            name="fk_spans_trace_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spans")),
        sa.UniqueConstraint("id", "trace_id", "project_id", name="uq_spans_id_trace_project"),
        sa.UniqueConstraint("project_id", "external_id", name="uq_spans_project_external_id"),
    )
    op.create_index(op.f("ix_spans_project_id"), "spans", ["project_id"], unique=False)
    op.create_index(op.f("ix_spans_trace_id"), "spans", ["trace_id"], unique=False)
    op.create_index(
        "ix_spans_trace_id_started_at", "spans", ["trace_id", "started_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_spans_trace_id_started_at", table_name="spans")
    op.drop_index(op.f("ix_spans_trace_id"), table_name="spans")
    op.drop_index(op.f("ix_spans_project_id"), table_name="spans")
    op.drop_table("spans")
    op.drop_index(op.f("ix_traces_project_id"), table_name="traces")
    op.drop_table("traces")
    span_kind.drop(op.get_bind(), checkfirst=True)
    execution_status.drop(op.get_bind(), checkfirst=True)
