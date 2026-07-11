"""独立诊断脚本：直接调 RuleStore.load_from_db，模拟 /admin/rule-sets/active 的 SQL。

定位 500 错误的根因。
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from app.core.database import SessionLocal, engine


async def main() -> None:
    print("=" * 60)
    print("Test 1: SELECT FROM v_active_rule_set")
    print("=" * 60)
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT id, name, version, source, effective_at, created_at "
                "FROM seo_agent.v_active_rule_set LIMIT 1"
            )
        )
        row = result.mappings().first()
        print(f"row: {dict(row) if row else None}")

    print()
    print("=" * 60)
    print("Test 2: SELECT FROM rule_sets directly")
    print("=" * 60)
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT id, name, version, source, is_active, effective_at "
                "FROM seo_agent.rule_sets WHERE is_active = true LIMIT 1"
            )
        )
        row = result.mappings().first()
        print(f"row: {dict(row) if row else None}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())