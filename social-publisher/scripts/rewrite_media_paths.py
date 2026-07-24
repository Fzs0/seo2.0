from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402


async def rewrite(old_prefix: str, new_prefix: str) -> int:
    changed = 0
    async with SessionLocal() as session:
        result = await session.execute(
            text("SELECT id, media FROM social.content_package_versions")
        )
        for row in result.mappings():
            media = list(row["media"] or [])
            updated = False
            for item in media:
                if not isinstance(item, dict):
                    continue
                value = str(item.get("path") or "")
                if value.startswith(old_prefix):
                    item["path"] = new_prefix + value[len(old_prefix):].replace("\\", "/")
                    updated = True
            if updated:
                await session.execute(
                    text("""
                        UPDATE social.content_package_versions
                           SET media = CAST(:media AS jsonb)
                         WHERE id = :id
                    """),
                    {"id": row["id"], "media": json.dumps(media, ensure_ascii=False)},
                )
                changed += 1
        await session.commit()
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rewrite Windows media prefixes after moving videos to macOS."
    )
    parser.add_argument("old_prefix", help=r"Example: C:\Users\PC\Desktop\seo2.0\social-video")
    parser.add_argument("new_prefix", help="Example: /Users/me/Exdivo/social-video")
    args = parser.parse_args()
    count = asyncio.run(rewrite(args.old_prefix, args.new_prefix))
    print(f"updated {count} content package versions")


if __name__ == "__main__":
    main()
