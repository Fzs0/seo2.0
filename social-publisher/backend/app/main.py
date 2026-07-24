from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.social import router as social_router
from app.core.config import get_settings
from app.core.database import SessionLocal, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


settings = get_settings()
app = FastAPI(
    title="Exdivo Social Publisher",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_origin,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(social_router, prefix="/api/v1")


@app.get("/api/health")
async def health() -> dict[str, str]:
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "service": "exdivo-social-publisher"}
