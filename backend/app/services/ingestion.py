from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.models import ExecutionStatus, Span, Trace
from app.schemas.trace import SpanIngest, TraceIngest, utc_datetime


def comparable(value):
    return utc_datetime(value) if isinstance(value, datetime) else value


async def upsert_resource(
    session: AsyncSession,
    model: type[Trace] | type[Span],
    values: dict,
    identity_fields: tuple[str, ...],
) -> tuple[Trace | Span, bool]:
    # The unique constraint arbitrates concurrent first deliveries. Lock the row
    # before updating so a stale running snapshot cannot undo a completed one.
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else postgres_insert
    inserted_id = await session.scalar(
        insert(model)
        .values(id=uuid4(), **values)
        .on_conflict_do_nothing(index_elements=["project_id", "external_id"])
        .returning(model.id)
    )
    record = await session.scalar(
        select(model)
        .where(model.project_id == values["project_id"], model.external_id == values["external_id"])
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None:
        # A concurrent project deletion may remove an existing conflicting row
        # between ON CONFLICT and SELECT. Return a controlled conflict.
        raise ApiError(409, "ingestion_conflict", "Operation was removed during ingestion")
    if inserted_id is not None:
        return record, True

    if any(comparable(getattr(record, key)) != values[key] for key in identity_fields):
        raise ApiError(409, "idempotency_conflict", "External ID belongs to a different operation")

    if record.status != ExecutionStatus.RUNNING:
        if values["status"] == ExecutionStatus.RUNNING:
            return record, False
        if any(comparable(getattr(record, key)) != value for key, value in values.items()):
            raise ApiError(409, "idempotency_conflict", "Completed operation cannot be overwritten")
        return record, False

    for key, value in values.items():
        setattr(record, key, value)
    await session.flush()
    return record, False


async def ingest_trace(
    session: AsyncSession, project_id: UUID, payload: TraceIngest
) -> tuple[Trace, bool]:
    return await upsert_resource(
        session, Trace, {"project_id": project_id, **payload.model_dump()}, ("name", "started_at")
    )


async def ingest_span(
    session: AsyncSession, project_id: UUID, trace_id: UUID, payload: SpanIngest
) -> tuple[Span, bool]:
    # Serialize span writes with trace deletion and parent lookup. Parent links
    # cannot change on replay, so requiring an existing parent prevents cycles.
    trace = await session.scalar(
        select(Trace).where(Trace.id == trace_id, Trace.project_id == project_id).with_for_update()
    )
    if trace is None:
        raise ApiError(404, "trace_not_found", "Trace not found")
    if payload.parent_span_id is not None:
        parent = await session.scalar(
            select(Span.id).where(
                Span.id == payload.parent_span_id,
                Span.project_id == project_id,
                Span.trace_id == trace_id,
            )
        )
        if parent is None:
            raise ApiError(404, "parent_span_not_found", "Parent span not found in this trace")

    return await upsert_resource(
        session,
        Span,
        {"project_id": project_id, "trace_id": trace_id, **payload.model_dump()},
        ("trace_id", "parent_span_id", "kind", "name", "started_at"),
    )
