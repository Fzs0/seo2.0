"""PostgreSQL runtime compatibility gate."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


SUPPORTED_POSTGRESQL_MAJOR = 17


class PostgreSQLVersionError(RuntimeError):
    """Raised before application startup when PostgreSQL is unsupported."""


async def require_postgresql_17(engine: Any) -> int:
    """Verify the connected server is PostgreSQL 17 and return its major."""
    async with engine.connect() as connection:
        version_num = int(
            (await connection.execute(text("SHOW server_version_num"))).scalar_one()
        )
    major = version_num // 10000
    if major != SUPPORTED_POSTGRESQL_MAJOR:
        raise PostgreSQLVersionError(
            "SEO autonomous operations requires PostgreSQL 17; "
            f"connected server reports major version {major} "
            f"(server_version_num={version_num})."
        )
    return major
