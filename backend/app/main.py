from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    validation_error_handler,
)
from app.api.router import api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.application_name,
        version=settings.application_version,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, http_error_handler)

    @application.middleware("http")
    async def request_id_middleware(request, call_next):
        request_id = request.headers.get("X-Request-ID")
        try:
            request_id = str(UUID(request_id)) if request_id is not None else str(uuid4())
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    application.include_router(api_router, prefix=settings.api_v1_prefix)
    return application


app = create_app()
