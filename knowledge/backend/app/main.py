from __future__ import annotations

import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .api import router
from .config import get_settings
from .batch_ingestion import BatchWorker
from .database import dispose_engine, get_session_factory
from .quality_backfill import QualityReviewWorker
from .signal_crawler import SignalCrawlWorker
from .errors import (
    ConflictError,
    DatabaseUnavailableError,
    InvalidInputError,
    NotFoundError,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker_tasks: list[asyncio.Task] = []
    if get_settings().database_url:
        factory = get_session_factory()
        worker_tasks = [
            asyncio.create_task(BatchWorker(factory).run_forever()),
            asyncio.create_task(QualityReviewWorker(factory).run_forever()),
            asyncio.create_task(SignalCrawlWorker(factory).run_forever()),
        ]
    try:
        yield
    finally:
        for worker_task in worker_tasks:
            worker_task.cancel()
        for worker_task in worker_tasks:
            with suppress(asyncio.CancelledError):
                await worker_task
        await dispose_engine()


app = FastAPI(
    title="Local Knowledge System",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_settings().cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": jsonable_encoder(exc.errors())})


@app.exception_handler(InvalidInputError)
async def invalid_input(_: Request, exc: InvalidInputError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(NotFoundError)
async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(DatabaseUnavailableError)
async def database_not_configured(
    _: Request, __: DatabaseUnavailableError
) -> JSONResponse:
    return JSONResponse(
        status_code=503, content={"detail": "knowledge database is unavailable"}
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(_: Request, __: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(
        status_code=503, content={"detail": "knowledge database is unavailable"}
    )


if __name__ == "__main__":
    # Local-only by design. Do not change this to 0.0.0.0 without adding auth.
    import uvicorn

    uvicorn.run("knowledge.backend.app.main:app", host="127.0.0.1", port=8010)
