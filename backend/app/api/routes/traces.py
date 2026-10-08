from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DbSession, get_current_project
from app.api.errors import ApiError
from app.models import Project
from app.schemas.trace import SpanIngest, SpanResponse, TraceIngest, TraceResponse
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
