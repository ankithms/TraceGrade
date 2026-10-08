from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.models import ExecutionStatus, Span, Trace
from app.schemas.trace import (
    SpanBatchAccepted,
    SpanBatchError,
    SpanBatchIngest,
    SpanBatchResponse,
    SpanIngest,
    TraceIngest,
    utc_datetime,
)


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


async def ingest_span_batch(
    session: AsyncSession, project_id: UUID, trace_id: UUID, payload: SpanBatchIngest
) -> SpanBatchResponse:
    # Hold the trace lock for the whole transaction, including item savepoints.
    # A project deletion cannot remove accepted spans before the batch commits.
    trace = await session.scalar(
        select(Trace.id)
        .where(Trace.id == trace_id, Trace.project_id == project_id)
        .with_for_update()
    )
    if trace is None:
        raise ApiError(404, "trace_not_found", "Trace not found")

    result = SpanBatchResponse(accepted=[], errors=[])
    for index, snapshot in enumerate(payload.spans):
        try:
            span_payload = SpanIngest.model_validate(snapshot)
        except ValidationError:
            result.errors.append(
                SpanBatchError(
                    index=index,
                    status_code=422,
                    code="validation_error",
                    message="Span validation failed",
                )
            )
            continue
        try:
            async with session.begin_nested():
                span, created = await ingest_span(session, project_id, trace_id, span_payload)
                accepted = SpanBatchAccepted(
                    index=index, id=span.id, external_id=span.external_id, created=created
                )
            result.accepted.append(accepted)
        except ApiError as error:
            result.errors.append(
                SpanBatchError(
                    index=index,
                    status_code=error.status_code,
                    code=error.code,
                    message=error.message,
                )
            )
        except IntegrityError:
            # Only this savepoint is rolled back. Other valid items can commit.
            result.errors.append(
                SpanBatchError(
                    index=index,
                    status_code=409,
                    code="ingestion_conflict",
                    message="Span conflicts with existing data",
                )
            )
    return result
