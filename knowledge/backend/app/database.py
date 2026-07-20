from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings
from .errors import DatabaseUnavailableError


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_engine_url: str | None = None


def get_engine() -> AsyncEngine:
    global _engine, _engine_url

    database_url = get_settings().database_url
    if not database_url:
        raise DatabaseUnavailableError("KNOWLEDGE_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+asyncpg://"):
        raise DatabaseUnavailableError(
            "KNOWLEDGE_DATABASE_URL must use the postgresql+asyncpg driver"
        )
    if _engine is None or _engine_url != database_url:
        _engine = create_async_engine(database_url, pool_pre_ping=True)
        _engine_url = database_url
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    engine = get_engine()
    if _session_factory is None or _session_factory.kw.get("bind") is not engine:
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


async def get_knowledge_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _session_factory, _engine_url
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
    _engine_url = None
