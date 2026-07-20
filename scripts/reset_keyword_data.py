"""安全清理关键词链路数据。

默认只预览，不会删除数据。

预览：
  python scripts/reset_keyword_data.py

执行：
  python scripts/reset_keyword_data.py --execute --confirm RESET_KEYWORD_DATA

脚本只清理：
- seo_agent.keywords 全部关键词；
- 关联的 serp_snapshots；
- 没有文章产物的关键词分析/审核/生文任务。

脚本保留：
- articles、posts、sites、products；
- 已有文章关联的 tasks；
- GSC、GA4、规则库和其他业务表。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal, engine

CONFIRMATION = "RESET_KEYWORD_DATA"


async def _count(session: Any, sql: str) -> int:
    return int((await session.execute(text(sql))).scalar_one() or 0)


async def _snapshot(session: Any) -> dict[str, int]:
    return {
        "keywords": await _count(session, "SELECT count(*) FROM seo_agent.keywords"),
        "serp_snapshots": await _count(
            session,
            "SELECT count(*) FROM seo_agent.serp_snapshots WHERE keyword_id IS NOT NULL",
        ),
        "deletable_tasks": await _count(
            session,
            """
            SELECT count(*)
              FROM seo_agent.tasks
             WHERE article_id IS NULL
               AND (
                 task_type IN ('keyword_review', 'serp_check')
                 OR keyword_id IS NOT NULL
               )
            """,
        ),
        "protected_article_tasks": await _count(
            session,
            "SELECT count(*) FROM seo_agent.tasks WHERE article_id IS NOT NULL",
        ),
        "active_keyword_tasks": await _count(
            session,
            """
            SELECT count(*)
             FROM seo_agent.tasks
             WHERE status IN ('queued', 'running')
               AND article_id IS NULL
               AND (
                 task_type IN ('keyword_review', 'serp_check')
                 OR keyword_id IS NOT NULL
               )
            """,
        ),
        "running_keyword_tasks": await _count(
            session,
            """
            SELECT count(*)
             FROM seo_agent.tasks
             WHERE status = 'running'
               AND article_id IS NULL
               AND (
                 task_type IN ('keyword_review', 'serp_check')
                 OR keyword_id IS NOT NULL
               )
            """,
        ),
        "articles": await _count(session, "SELECT count(*) FROM seo_agent.articles"),
        "posts": await _count(session, "SELECT count(*) FROM seo_agent.posts"),
        "sites": await _count(session, "SELECT count(*) FROM seo_agent.sites"),
        "products": await _count(session, "SELECT count(*) FROM seo_agent.products"),
    }


async def _active_tasks(session: Any) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, task_type, status, title, keyword_id, article_id
              FROM seo_agent.tasks
             WHERE status IN ('queued', 'running')
               AND article_id IS NULL
               AND (
                 task_type IN ('keyword_review', 'serp_check')
                 OR keyword_id IS NOT NULL
               )
             ORDER BY created_at
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


def _print_snapshot(snapshot: dict[str, int]) -> None:
    print("关键词链路清理预览：")
    for key in ("keywords", "serp_snapshots", "deletable_tasks", "active_keyword_tasks", "running_keyword_tasks"):
        print(f"  - {key}: {snapshot[key]}")
    print("保留数据基线：")
    for key in ("articles", "posts", "sites", "products", "protected_article_tasks"):
        print(f"  - {key}: {snapshot[key]}")


async def reset_keyword_data(*, execute: bool, confirmation: str) -> None:
    async with SessionLocal() as session:
        before = await _snapshot(session)
        _print_snapshot(before)
        if before["active_keyword_tasks"]:
            print("活动关键词相关任务：")
            for task in await _active_tasks(session):
                print(
                    f"  - {task['id']} | {task['task_type']} | {task['status']} | "
                    f"{task['title'] or '—'} | keyword={task['keyword_id'] or '—'}"
                )

        if not execute:
            print("\n当前为预览模式；如确认清理，请追加 --execute --confirm RESET_KEYWORD_DATA")
            return
        if confirmation != CONFIRMATION:
            raise SystemExit(f"执行清理必须提供 --confirm {CONFIRMATION}")
        if before["running_keyword_tasks"]:
            raise SystemExit("存在 running 关键词相关任务，请先停止后再执行清理")

        try:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('seo2.keyword-reset'))"))
            await session.execute(
                text(
                    """
                    CREATE TEMP TABLE keyword_reset_ids ON COMMIT DROP AS
                    SELECT id FROM seo_agent.keywords
                    """
                )
            )
            await session.execute(
                text(
                    """
                    DELETE FROM seo_agent.tasks
                     WHERE article_id IS NULL
                       AND (
                         task_type IN ('keyword_review', 'serp_check')
                         OR keyword_id IN (SELECT id FROM keyword_reset_ids)
                       )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    DELETE FROM seo_agent.serp_snapshots
                     WHERE keyword_id IN (SELECT id FROM keyword_reset_ids)
                    """
                )
            )
            await session.execute(text("DELETE FROM seo_agent.keywords"))
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        after = await _snapshot(session)
        print("\n清理完成：")
        print(f"  - keywords: {before['keywords']} -> {after['keywords']}")
        print(f"  - keyword serp_snapshots: {before['serp_snapshots']} -> {after['serp_snapshots']}")
        print(f"  - deletable tasks: {before['deletable_tasks']} -> {after['deletable_tasks']}")
        for key in ("articles", "posts", "sites", "products", "protected_article_tasks"):
            if after[key] != before[key]:
                raise RuntimeError(f"保护表 {key} 数量发生变化：{before[key]} -> {after[key]}")
        print("  - articles/posts/sites/products/文章关联任务：数量未变化")


def main() -> None:
    parser = argparse.ArgumentParser(description="安全清理 SEO Workbench 关键词链路数据")
    parser.add_argument("--execute", action="store_true", help="真正执行删除；默认只预览")
    parser.add_argument("--confirm", default="", help=f"执行确认词：{CONFIRMATION}")
    args = parser.parse_args()

    async def run() -> None:
        try:
            await reset_keyword_data(execute=args.execute, confirmation=args.confirm)
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
