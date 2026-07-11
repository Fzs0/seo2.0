"""规则版本管理服务：写入 rule_sets + audit_log；切换 active 版本。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class RuleServiceError(Exception):
    """规则服务层的所有错误统一抛出。"""


async def save_rule_set(
    session: AsyncSession,
    *,
    name: str,
    version: str,
    source: str,
    payload: dict[str, Any],
    actor: str,
    notes: str | None = None,
    set_active: bool = False,
) -> dict[str, Any]:
    """写入新版本；可选同时激活。

    - 写入前 payload 必须是 dict（rule_sets_payload_object_chk）
    - version 必须符合 SemVer（rule_sets_version_chk）
    - source ∈ file/db/api（rule_sets_source_chk）
    - set_active=True 时，先把同 family 的其他 active 行 deactivate，再激活这一行
    """
    if not isinstance(payload, dict):
        raise RuleServiceError("payload 必须是 dict")
    if source not in ("file", "db", "api"):
        raise RuleServiceError("source 必须是 file / db / api 之一")

    # 同 (name, version) 已存在则报错
    existing = (
        await session.execute(
            text(
                "SELECT id FROM seo_agent.rule_sets WHERE name = :name AND version = :version"
            ),
            {"name": name, "version": version},
        )
    ).first()
    if existing:
        raise RuleServiceError(f"已存在同 ({name}, {version}) 版本")

    inserted = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.rule_sets
                  (name, version, source, payload, is_active, effective_at, created_by, notes)
                VALUES
                  (:name, :version, :source, CAST(:payload AS jsonb), :is_active, now(), :actor, :notes)
                RETURNING id, effective_at, created_at
                """
            ),
            {
                "name": name,
                "version": version,
                "source": source,
                "payload": json.dumps(payload, ensure_ascii=False),
                "is_active": False,
                "actor": actor,
                "notes": notes,
            },
        )
    ).mappings().first()
    new_id = inserted["id"]

    await session.execute(
        text(
            """
            INSERT INTO seo_agent.rule_audit_log (rule_set_id, action, actor, reason, snapshot_diff)
            VALUES (:rid, 'create', :actor, :reason, CAST(:diff AS jsonb))
            """
        ),
        {
            "rid": new_id,
            "actor": actor,
            "reason": f"Wrote version {version} from {source}",
            "diff": json.dumps({"added_keys": sorted(payload.keys()), "source": source}, ensure_ascii=False),
        },
    )

    if set_active:
        await _activate(session, new_id, actor, reason=f"Set version {version} active via API")

    return {"id": new_id, "version": version, "is_active": set_active}


async def _activate(
    session: AsyncSession,
    rule_set_id: int,
    actor: str,
    reason: str,
) -> None:
    """先把同 family 的 active 行 deactivate，再 activate 给定 id。"""
    current = (
        await session.execute(
            text(
                """
                SELECT id, name FROM seo_agent.rule_sets
                 WHERE name = (SELECT name FROM seo_agent.rule_sets WHERE id = :rid)
                   AND is_active = true
                """
            ),
            {"rid": rule_set_id},
        )
    ).mappings().all()
    for row in current:
        if row["id"] == rule_set_id:
            continue
        await session.execute(
            text("UPDATE seo_agent.rule_sets SET is_active = false WHERE id = :id"),
            {"id": row["id"]},
        )
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.rule_audit_log (rule_set_id, action, actor, reason, snapshot_diff)
                VALUES (:rid, 'deactivate', :actor, 'Superseded by new active version', '{}'::jsonb)
                """
            ),
            {"rid": row["id"], "actor": actor},
        )

    await session.execute(
        text(
            "UPDATE seo_agent.rule_sets SET is_active = true, effective_at = now() WHERE id = :rid"
        ),
        {"rid": rule_set_id},
    )
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.rule_audit_log (rule_set_id, action, actor, reason, snapshot_diff)
            VALUES (:rid, 'activate', :actor, :reason, '{}'::jsonb)
            """
        ),
        {"rid": rule_set_id, "actor": actor, "reason": reason},
    )