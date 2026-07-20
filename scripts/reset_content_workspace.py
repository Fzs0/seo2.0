"""清空 SEO 内容生产运行数据，保留配置与分析数据。

默认只预览，不删除任何数据。

预览：
  python scripts/reset_content_workspace.py

执行：
  python scripts/reset_content_workspace.py --execute --confirm RESET_CONTENT_WORKSPACE

清空：keywords、serp_snapshots、tasks、articles。
默认保留 posts；只有加 --clear-posts 才清空本地站点内容索引。
保留：sites、products、规则库、GSC、GA4 及同步日志。
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

CONFIRMATION = "RESET_CONTENT_WORKSPACE"
BASE_CLEAR_TABLES = ("tasks", "articles", "serp_snapshots", "keywords")
PRESERVE_TABLES = (
    "sites",
    "products",
    "rule_sets",
    "rule_field_map",
    "rule_audit_log",
    "google_sync_log",
    "gsc_query_daily",
    "ga4_session_daily",
    "ga4_landing_page_daily",
)


async def _count(session: Any, table: str) -> int:
    return int((await session.execute(text(f"SELECT count(*) FROM seo_agent.{table}"))).scalar_one() or 0)


async def _snapshot(session: Any) -> dict[str, int]:
    snapshot = {table: await _count(session, table) for table in (*BASE_CLEAR_TABLES, "posts", *PRESERVE_TABLES)}
    snapshot["running_tasks"] = int(
        (
            await session.execute(
                text("SELECT count(*) FROM seo_agent.tasks WHERE status = 'running'")
            )
        ).scalar_one()
        or 0
    )
    snapshot["strategy_tasks"] = int(
        (
            await session.execute(
                text("SELECT count(*) FROM seo_agent.tasks WHERE task_type = 'review' AND payload->>'kind' = 'seo_strategy'")
            )
        ).scalar_one()
        or 0
    )
    snapshot["article_tasks"] = int(
        (
            await session.execute(
                text("SELECT count(*) FROM seo_agent.tasks WHERE task_type IN ('new_article', 'update_article')")
            )
        ).scalar_one()
        or 0
    )
    return snapshot


def _print_snapshot(snapshot: dict[str, int]) -> None:
    print("内容生产数据清理预览：")
    for table in BASE_CLEAR_TABLES:
        print(f"  - {table}: {snapshot[table]}")
    print(f"  - posts（默认保留，可用 --clear-posts 清空）: {snapshot['posts']}")
    print(f"  - strategy_tasks: {snapshot['strategy_tasks']}")
    print(f"  - article_tasks: {snapshot['article_tasks']}")
    print(f"  - running_tasks: {snapshot['running_tasks']}")
    print("保留数据：")
    for table in PRESERVE_TABLES:
        print(f"  - {table}: {snapshot[table]}")


async def reset_content_workspace(*, execute: bool, confirmation: str, clear_posts: bool) -> None:
    async with SessionLocal() as session:
        before = await _snapshot(session)
        _print_snapshot(before)

        if not execute:
            print(f"\n当前为预览模式；如确认清理，请追加 --execute --confirm {CONFIRMATION}")
            return
        if confirmation != CONFIRMATION:
            raise SystemExit(f"执行清理必须提供 --confirm {CONFIRMATION}")
        if before["running_tasks"]:
            raise SystemExit("存在 running 任务，请先停止任务后再执行清理")

        try:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('seo2.content-workspace-reset'))"))
            running = int(
                (
                    await session.execute(
                        text("SELECT count(*) FROM seo_agent.tasks WHERE status = 'running'")
                    )
                ).scalar_one()
                or 0
            )
            if running:
                raise RuntimeError("清理期间出现 running 任务，已中止")
            clear_tables = (*BASE_CLEAR_TABLES, "posts") if clear_posts else BASE_CLEAR_TABLES
            deleted = {
                table: int((await session.execute(text(f"DELETE FROM seo_agent.{table}"))).rowcount or 0)
                for table in clear_tables
            }
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        after = await _snapshot(session)
        print("\n清理完成：")
        clear_tables = (*BASE_CLEAR_TABLES, "posts") if clear_posts else BASE_CLEAR_TABLES
        for table in clear_tables:
            print(f"  - {table}: {before[table]} -> {after[table]}，删除 {deleted[table]} 条")
        if not clear_posts:
            if after["posts"] != before["posts"]:
                raise RuntimeError(f"保护表 posts 数量发生变化：{before['posts']} -> {after['posts']}")
            print(f"  - posts: 保留 {after['posts']} 条本地内容索引")
        for table in PRESERVE_TABLES:
            if after[table] != before[table]:
                raise RuntimeError(f"保护表 {table} 数量发生变化：{before[table]} -> {after[table]}")
        print("  - 站点、产品、规则、GSC、GA4 及同步日志：数量未变化")


def main() -> None:
    parser = argparse.ArgumentParser(description="安全清空 SEO 内容生产运行数据")
    parser.add_argument("--execute", action="store_true", help="真正执行删除；默认只预览")
    parser.add_argument("--confirm", default="", help=f"执行确认词：{CONFIRMATION}")
    parser.add_argument("--clear-posts", action="store_true", help="同时清空本地 posts 内容索引；外部站点文章不受影响")
    args = parser.parse_args()

    async def run() -> None:
        try:
            await reset_content_workspace(execute=args.execute, confirmation=args.confirm, clear_posts=args.clear_posts)
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
