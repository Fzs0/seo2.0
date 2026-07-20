"""Backfill Semrush trend series for rows imported before trend_data existed."""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal


def series(value: str | None) -> list[float]:
    result: list[float] = []
    for part in str(value or "").split(","):
        cleaned = re.sub(r"[$%\s]", "", part)
        try:
            result.append(float(cleaned))
        except ValueError:
            continue
    return result


async def main() -> None:
    async with SessionLocal() as session:
        rows = (await session.execute(text("SELECT id, raw FROM seo_agent.keywords WHERE raw ? 'trend'"))).mappings().all()
        updates = []
        for row in rows:
            values = series((row["raw"] or {}).get("trend"))
            if values:
                updates.append({"id": row["id"], "trend": values[-1], "trend_data": str(values).replace("'", '"')})
        await session.execute(
            text("UPDATE seo_agent.keywords SET trend = :trend, trend_data = CAST(:trend_data AS jsonb), updated_at = now() WHERE id = :id"),
            updates,
        )
        await session.commit()
        print(f"updated={len(updates)}")


if __name__ == "__main__":
    asyncio.run(main())
