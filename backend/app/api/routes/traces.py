from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DbSession, get_current_project
from app.api.errors import ApiError
from app.models import Project, Span, Trace
from app.schemas.trace import (
    SpanIngest,
    SpanResponse,
    TraceDetailResponse,
    TraceIngest,
    TraceListResponse,
    TraceResponse,
)
from app.services.ingestion import ingest_span, ingest_trace

router = APIRouter(prefix="/traces", tags=["traces"])
CurrentProject = Annotated[Project, Depends(get_current_project)]


@router.post("", response_model=TraceResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_trace(
    payload: TraceIngest, response: Response, session: DbSession, project: CurrentProject
) -> TraceResponse:
    try:
        trace, created = await ingest_trace(session, project.id, payload)
        result = TraceResponse.model_validate(trace)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ApiError(409, "ingestion_conflict", "Trace conflicts with existing data") from error
    except ApiError:
        await session.rollback()
        raise
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@router.post(
    "/{trace_id}/spans",
    response_model=SpanResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["spans"],
)
async def create_or_update_span(
    trace_id: UUID,
    payload: SpanIngest,
    response: Response,
    session: DbSession,
    project: CurrentProject,
) -> SpanResponse:
    try:
        span, created = await ingest_span(session, project.id, trace_id, payload)
        result = SpanResponse.model_validate(span)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ApiError(409, "ingestion_conflict", "Span conflicts with existing data") from error
    except ApiError:
        await session.rollback()
        raise
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("", response_model=TraceListResponse)
async def list_traces(
    session: DbSession,
    project: CurrentProject,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TraceListResponse:
    traces = await session.scalars(
        select(Trace)
        .where(Trace.project_id == project.id)
        .order_by(Trace.created_at.desc(), Trace.id.desc())
        .limit(limit)
        .offset(offset)
    )
    total = await session.scalar(
        select(func.count()).select_from(Trace).where(Trace.project_id == project.id)
    )
    return TraceListResponse(
        items=[TraceResponse.model_validate(trace) for trace in traces], total=total or 0
    )


@router.get("/{trace_id}", response_model=TraceDetailResponse)
async def get_trace(
    trace_id: UUID, session: DbSession, project: CurrentProject
) -> TraceDetailResponse:
    trace = await session.scalar(
        select(Trace).where(Trace.id == trace_id, Trace.project_id == project.id)
    )
    if trace is None:
        raise ApiError(404, "trace_not_found", "Trace not found")
    spans = await session.scalars(
        select(Span)
        .where(Span.trace_id == trace_id, Span.project_id == project.id)
        .order_by(Span.started_at, Span.id)
    )
    return TraceDetailResponse(
        **TraceResponse.model_validate(trace).model_dump(),
        spans=[SpanResponse.model_validate(span) for span in spans],
    )
