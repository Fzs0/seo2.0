"""Stable response envelope for Codex-facing strategy APIs."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse


def request_id_for(request: Request) -> str:
    return (
        request.headers.get("X-Request-ID")
        or getattr(request.state, "trace_id", None)
        or str(uuid4())
    )


def success(request: Request, data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": True,
            "request_id": request_id_for(request),
            "data": data,
            "error": None,
        },
    )


def failure(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "request_id": request_id_for(request),
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            },
        },
    )


class ContractRoute(APIRoute):
    """Keep validation failures inside the same stable API contract."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def contract_handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError as error:
                return failure(
                    request,
                    status_code=422,
                    code="REQUEST_VALIDATION_FAILED",
                    message="Request validation failed.",
                    details={"errors": error.errors()},
                )

        return contract_handler


__all__ = ["ContractRoute", "failure", "request_id_for", "success"]
