from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.db.session import database_is_ready

router = APIRouter(prefix="/health", tags=["health"])


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["tracegrade-api"] = "tracegrade-api"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "error"]]


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
)
async def ready(response: Response) -> ReadyResponse:
    if await database_is_ready():
        return ReadyResponse(status="ready", checks={"database": "ok"})

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="not_ready", checks={"database": "error"})
