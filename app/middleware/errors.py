"""RFC 7807 problem+json 统一异常响应。"""
import uuid
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


def problem(
    *,
    type_: str,
    title: str,
    status: int,
    detail: str,
    instance: str,
    extras: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": type_,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
    }
    if extras:
        body.update(extras)
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        instance = f"/errors/{uuid.uuid4()}"
        logger.warning("request_validation_error", instance=instance, errors=exc.errors())
        return problem(
            type_="https://errors.seo-workbench/validation",
            title="Request validation failed",
            status=422,
            detail=str(exc.errors()),
            instance=instance,
        )

    @app.exception_handler(Exception)
    async def on_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        instance = f"/errors/{uuid.uuid4()}"
        logger.exception("unhandled_exception", instance=instance, error=str(exc))
        return problem(
            type_="https://errors.seo-workbench/internal",
            title="Internal server error",
            status=500,
            detail="An unexpected error occurred. The trace_id can help correlate logs.",
            instance=instance,
        )