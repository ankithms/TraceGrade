from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def error_body(request: Request, code: str, message: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request.state.request_id,
        }
    }


async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error_body(request, error.code, error.message),
    )


async def validation_error_handler(
    request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body(request, "validation_error", "Request validation failed"),
    )


async def http_error_handler(
    request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    code = "not_found" if error.status_code == 404 else "http_error"
    message = "Resource not found" if error.status_code == 404 else str(error.detail)
    return JSONResponse(
        status_code=error.status_code,
        content=error_body(request, code, message),
        headers=error.headers,
    )
