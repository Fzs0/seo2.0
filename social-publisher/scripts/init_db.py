from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()
    database_url = settings.database_url.replace("+asyncpg", "")
    schema = (ROOT / "database" / "schema.sql").read_text(encoding="utf-8")
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(schema)
    finally:
        await connection.close()
    print("social publisher schema is ready")


if __name__ == "__main__":
    asyncio.run(main())
