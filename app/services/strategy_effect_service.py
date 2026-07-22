"""策略执行基线、效果检查和冷却状态，复用 seo_agent.tasks。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


POLICY_VERSION = "strategy_policy_v1"
CHECKPOINT_DAYS = (7, 14, 28, 56, 90)


def resolve_strategy_target_url(strategy: dict[str, Any]) -> str | None:
    """Return a known page URL for a strategy, never a connector placeholder."""
    evidence = strategy.get("evidence") or {}
    candidates = (
        strategy.get("target_url"),
        strategy.get("url"),
        (evidence.get("site_content") or {}).get("published_url"),
        (evidence.get("content_audit") or {}).get("target_url"),
        (evidence.get("content_audit") or {}).get("url"),
    )
    return next((str(value).strip() for value in candidates if _is_page_url(value)), None)


def _is_page_url(value: Any) -> bool:
    return isinstance(value, str) and str(value).strip().startswith(("https://", "http://", "/"))


def _hash(parts: list[Any]) -> str:
    value = "|".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def strategy_identity(
    business_id: str,
    *,
    site_id: Any,
    market: Any = None,
    language_code: Any = None,
    topic_cluster_id: Any = None,
    post_id: Any = None,
    article_id: Any = None,
    query: Any = None,
    action: Any = None,
    objective: Any = None,
    evidence: Any = None,
) -> dict[str, str]:
    target = (
        post_id or article_id or topic_cluster_id or query
        if action == "update_article"
        else topic_cluster_id or query or post_id or article_id
    )
    scope_key = _hash([business_id, site_id, market, language_code, target])
    strategy_fingerprint = _hash([business_id, site_id, market, language_code, target, action, objective])
    evidence_fingerprint = hashlib.sha256(
        json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "scope_key": scope_key,
        "strategy_fingerprint": strategy_fingerprint,
        "evidence_fingerprint": evidence_fingerprint,
        "policy_version": POLICY_VERSION,
    }


def _landing_pages(target_url: str | None) -> list[str]:
    if not target_url:
        return []
    parsed = urlsplit(target_url)
    path = parsed.path or "/"
    return list(dict.fromkeys([target_url.rstrip("/"), path.rstrip("/") or "/", path]))


async def capture_metrics(
    session: AsyncSession,
    *,
    site_id: str,
    query: str | None,
    target_url: str | None,
    metric_scope: dict[str, str] | None = None,
) -> dict[str, Any]:
    scope = metric_scope or {
        "gsc": "page" if target_url else ("query" if query else "site"),
        "ga4": "landing_page" if target_url else "site",
    }
    gsc = (
        await session.execute(
            text(
                """
                SELECT COALESCE(sum(clicks), 0) AS clicks,
                       COALESCE(sum(impressions), 0) AS impressions,
                       CASE WHEN sum(impressions) > 0
                            THEN round(sum(position * impressions) / sum(impressions), 2) ELSE 0 END AS avg_position
                  FROM seo_agent.gsc_query_daily
                 WHERE site_id = CAST(:site_id AS uuid)
                   AND date >= current_date - interval '28 days' AND date < current_date
                   AND (:gsc_scope = 'site'
                        OR (:gsc_scope = 'query' AND lower(query) = lower(:query))
                        OR (:gsc_scope = 'page' AND lower(rtrim(page, '/')) = lower(rtrim(:target_url, '/'))))
                """
            ),
            {"site_id": site_id, "gsc_scope": scope["gsc"], "query": query or "", "target_url": target_url or ""},
        )
    ).mappings().one()
    if scope["ga4"] == "landing_page":
        ga4 = (
            await session.execute(
                text(
                    """
                    SELECT COALESCE(sum(sessions), 0) AS sessions,
                           COALESCE(sum(conversions), 0) AS conversions
                      FROM seo_agent.ga4_landing_page_daily
                     WHERE site_id = CAST(:site_id AS uuid)
                       AND date >= current_date - interval '28 days' AND date < current_date
                       AND rtrim(landing_page, '/') = ANY(:landing_pages)
                    """
                ),
                {"site_id": site_id, "landing_pages": [value.rstrip("/") for value in _landing_pages(target_url)]},
            )
        ).mappings().one()
    else:
        ga4 = (
            await session.execute(
                text(
                    """
                    SELECT COALESCE(sum(sessions), 0) AS sessions,
                           COALESCE(sum(conversions), 0) AS conversions
                      FROM seo_agent.ga4_session_daily
                     WHERE site_id = CAST(:site_id AS uuid) AND channel = 'all'
                       AND date >= current_date - interval '28 days' AND date < current_date
                    """
                ),
                {"site_id": site_id},
            )
        ).mappings().one()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "window_days": 28,
        "metric_scope": scope,
        "gsc": {"clicks": int(gsc["clicks"] or 0), "impressions": int(gsc["impressions"] or 0), "avg_position": float(gsc["avg_position"] or 0)},
        "ga4": {"sessions": int(ga4["sessions"] or 0), "conversions": float(ga4["conversions"] or 0)},
    }


def metric_delta(baseline: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, float | None]:
    def change(before: float, after: float) -> float | None:
        if before == 0:
            return None if after == 0 else 1.0
        return round((after - before) / abs(before), 4)

    base_gsc, now_gsc = baseline.get("gsc") or {}, snapshot.get("gsc") or {}
    base_ga4, now_ga4 = baseline.get("ga4") or {}, snapshot.get("ga4") or {}
    before_position, now_position = float(base_gsc.get("avg_position") or 0), float(now_gsc.get("avg_position") or 0)
    return {
        "impressions": change(float(base_gsc.get("impressions") or 0), float(now_gsc.get("impressions") or 0)),
        "clicks": change(float(base_gsc.get("clicks") or 0), float(now_gsc.get("clicks") or 0)),
        "avg_position": round((before_position - now_position) / before_position, 4) if before_position and now_position else None,
        "sessions": change(float(base_ga4.get("sessions") or 0), float(now_ga4.get("sessions") or 0)),
        "conversions": change(float(base_ga4.get("conversions") or 0), float(now_ga4.get("conversions") or 0)),
    }


def classify_outcome(
    snapshot: dict[str, Any],
    delta: dict[str, float | None],
    *,
    contaminated: bool = False,
    day: int = 90,
) -> str:
    if contaminated:
        return "contaminated"
    if day < 28:
        return "inconclusive"
    if int((snapshot.get("gsc") or {}).get("impressions") or 0) < 200:
        return "inconclusive"
    signals = [delta.get(key) for key in ("clicks", "avg_position", "conversions")]
    meaningful = [value for value in signals if value is not None and abs(value) >= 0.2]
    if meaningful and all(value >= 0.2 for value in meaningful):
        return "winner"
    if meaningful and all(value <= -0.2 for value in meaningful):
        return "loser"
    return "neutral"


async def load_scope_locks(session: AsyncSession, *, business_id: str) -> dict[str, str]:
    rows = await session.execute(
        text(
            """
            SELECT status, payload->>'scope_key' AS scope_key, payload->>'action' AS action,
                   payload->>'target_url' AS target_url,
                   COALESCE((payload->>'cooldown_until')::timestamptz > now(), false) AS cooling
              FROM seo_agent.tasks
             WHERE task_type = 'review' AND payload->>'kind' = 'strategy_effect'
               AND payload->>'business_id' = :business_id
            UNION ALL
            SELECT status, payload->>'scope_key' AS scope_key,
                   payload#>>'{strategy,strategy_type}' AS action,
                   NULL AS target_url, false AS cooling
              FROM seo_agent.tasks
             WHERE task_type IN ('new_article', 'update_article') AND status IN ('queued', 'running')
               AND payload#>>'{strategy,business_id}' = :business_id
            """
        ),
        {"business_id": business_id},
    )
    locks: dict[str, str] = {}
    for row in rows.mappings().all():
        scope_key = str(row["scope_key"] or "")
        if not scope_key:
            continue
        if row["status"] in {"queued", "running"} and not row["target_url"]:
            locks[scope_key] = "同一页面或意图已有策略正在执行或观察"
        elif row["action"] == "update_article" and row["cooling"]:
            locks[scope_key] = "同一页面仍在 28 天更新冷却期"
        elif row["action"] == "new_article" and row["target_url"]:
            locks[scope_key] = "同一意图已有已发布页面，禁止重复新写"
    return locks


async def ensure_effect(
    session: AsyncSession,
    *,
    execution_task_id: str,
    strategy_task_id: str,
    site_id: str,
    article_id: str | None,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"strategy-effect:{execution_task_id}"})
    existing = (
        await session.execute(
            text(
                "SELECT id::text AS id, status, payload FROM seo_agent.tasks WHERE task_type = 'review' "
                "AND payload->>'kind' = 'strategy_effect' "
                "AND payload->>'execution_task_id' = :execution_task_id "
                "ORDER BY CASE WHEN status = 'canceled' THEN 1 ELSE 0 END, created_at DESC LIMIT 1"
            ),
            {"execution_task_id": execution_task_id},
        )
    ).mappings().first()
    if existing:
        if existing["status"] == "canceled":
            await session.execute(
                text(
                    "UPDATE seo_agent.tasks SET status = 'queued', finished_at = NULL, error_message = NULL, "
                    "payload = payload || CAST(:payload AS jsonb), "
                    "decision = (COALESCE(decision, '{}'::jsonb) - 'canceled_reason') || CAST(:decision AS jsonb), "
                    "updated_at = now() WHERE id = CAST(:id AS uuid) AND status = 'canceled'"
                ),
                {
                    "id": existing["id"],
                    "payload": json.dumps({"outcome": "observing"}),
                    "decision": json.dumps({"outcome": "observing"}),
                },
            )
        return {"id": existing["id"], **dict(existing["payload"] or {}), "outcome": "observing"}
    identity = {key: strategy.get(key) for key in ("scope_key", "strategy_fingerprint", "evidence_fingerprint", "policy_version")}
    if not all(identity.values()):
        raise ValueError("策略缺少生命周期指纹，请重新生成并审核策略")
    target_url = resolve_strategy_target_url(strategy)
    baseline = await capture_metrics(
        session,
        site_id=site_id,
        query=str(strategy.get("query") or "") or None,
        target_url=target_url,
    )
    payload = {
        "kind": "strategy_effect",
        "business_id": strategy.get("business_id"),
        "execution_task_id": execution_task_id,
        "strategy_task_id": strategy_task_id,
        **identity,
        "action": strategy.get("strategy_type"),
        "objective": strategy.get("recommended_action"),
        "query": strategy.get("query"),
        "target_url": target_url,
        "baseline": baseline,
        "checkpoints": [],
        "outcome": "observing",
        "cooldown_until": None,
        "next_checkpoint": None,
    }
    inserted = await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks
              (task_type, status, priority, site_id, article_id, target_url, title, payload, decision)
            VALUES
              ('review', 'queued', 'P2', CAST(:site_id AS uuid), CAST(:article_id AS uuid), :target_url,
               :title, CAST(:payload AS jsonb), CAST(:decision AS jsonb))
            RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "article_id": article_id,
            "target_url": target_url,
            "title": f"策略效果观察：{strategy.get('query') or strategy_task_id}",
            "payload": json.dumps(payload, ensure_ascii=False),
            "decision": json.dumps({"outcome": "observing"}),
        },
    )
    return {"id": str(inserted.scalar_one()), **payload}


async def cancel_unpublished_effect(session: AsyncSession, *, execution_task_id: str, reason: str) -> None:
    """释放失败执行留下的观察锁；已发布效果历史保持不变。"""
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now(), "
            "payload = payload || CAST(:payload AS jsonb), "
            "decision = decision || CAST(:decision AS jsonb) "
            "WHERE task_type = 'review' AND status IN ('queued', 'running') "
            "AND payload->>'kind' = 'strategy_effect' "
            "AND payload->>'execution_task_id' = :execution_task_id AND NOT (payload ? 'published_at')"
        ),
        {
            "execution_task_id": execution_task_id,
            "payload": json.dumps({"outcome": "inconclusive", "canceled_reason": reason}, ensure_ascii=False),
            "decision": json.dumps({"outcome": "inconclusive", "canceled_reason": reason}, ensure_ascii=False),
        },
    )


async def mark_effect_published(
    session: AsyncSession,
    *,
    execution_task_id: str,
    article_id: str,
    target_url: str | None,
    action: str | None = None,
) -> None:
    article = (
        await session.execute(
            text(
                "SELECT published_url, published_at, "
                "(SELECT payload FROM seo_agent.tasks WHERE task_type = 'review' "
                "AND payload->>'kind' = 'strategy_effect' "
                "AND payload->>'execution_task_id' = :execution_task_id LIMIT 1) AS effect_payload "
                "FROM seo_agent.articles WHERE id = CAST(:id AS uuid)"
            ),
            {"id": article_id, "execution_task_id": execution_task_id},
        )
    ).mappings().first()
    published_at = (article or {}).get("published_at") or datetime.now(timezone.utc)
    effect_target_url = ((article or {}).get("effect_payload") or {}).get("target_url")
    url = next((str(value).strip() for value in (target_url, (article or {}).get("published_url"), effect_target_url) if _is_page_url(value)), None)
    cooldown_until = published_at + timedelta(days=28) if action == "update_article" else None
    next_due = published_at + timedelta(days=CHECKPOINT_DAYS[0])
    baseline = dict(((article or {}).get("effect_payload") or {}).get("baseline") or {})
    if action == "new_article" and baseline:
        metric_scope = dict(baseline.get("metric_scope") or {})
        metric_scope["ga4"] = "landing_page"
        baseline.update({"metric_scope": metric_scope, "ga4": {"sessions": 0, "conversions": 0}})
    patch = {
        "article_id": article_id,
        "target_url": url,
        "published_at": published_at.isoformat(),
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
        "next_checkpoint": {"day": CHECKPOINT_DAYS[0], "due_at": next_due.isoformat()},
    }
    if baseline:
        patch["baseline"] = baseline
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET article_id = CAST(:article_id AS uuid), target_url = :target_url, run_after = :run_after,
                   payload = payload || CAST(:patch AS jsonb), updated_at = now()
             WHERE task_type = 'review' AND status = 'queued' AND payload->>'kind' = 'strategy_effect'
               AND payload->>'execution_task_id' = :execution_task_id
            """
        ),
        {
            "execution_task_id": execution_task_id,
            "article_id": article_id,
            "target_url": url,
            "run_after": next_due,
            "patch": json.dumps(patch, ensure_ascii=False),
        },
    )


async def backfill_missing_effect_target_urls(session: AsyncSession, *, business_id: str) -> int:
    """Repair legacy effect records when their completed execution retained a known page URL.

    This is deliberately conservative: only a syntactically valid page URL from
    the original strategy evidence or persisted article can repair a record.
    Connector placeholders are never copied into effect measurement.
    """
    rows = await session.execute(
        text(
            """
            SELECT effect.id::text AS id, effect.target_url, effect.payload,
                   execution.payload->'strategy' AS strategy,
                   article.published_url AS article_url
              FROM seo_agent.tasks effect
              LEFT JOIN seo_agent.tasks execution
                ON execution.id::text = effect.payload->>'execution_task_id'
              LEFT JOIN seo_agent.articles article ON article.id = effect.article_id
             WHERE effect.task_type = 'review' AND effect.status <> 'canceled'
               AND effect.payload->>'kind' = 'strategy_effect'
               AND effect.payload->>'business_id' = :business_id
               AND effect.payload ? 'published_at'
               AND NULLIF(trim(COALESCE(effect.target_url, effect.payload->>'target_url', '')), '') IS NULL
            """
        ),
        {"business_id": business_id},
    )
    repaired = 0
    for row in rows.mappings().all():
        strategy = dict(row["strategy"] or {})
        target_url = resolve_strategy_target_url(strategy)
        if not target_url and _is_page_url(row["article_url"]):
            target_url = str(row["article_url"]).strip()
        if not target_url:
            continue
        patch = {
            "target_url": target_url,
            "baseline_note": "初始基线创建时未绑定目标 URL；其中的 0 值不能作为可靠的更新前对比。",
        }
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET target_url = :target_url,
                       payload = payload || CAST(:patch AS jsonb),
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                   AND NULLIF(trim(COALESCE(target_url, payload->>'target_url', '')), '') IS NULL
                """
            ),
            {"id": row["id"], "target_url": target_url, "patch": json.dumps(patch, ensure_ascii=False)},
        )
        repaired += 1
    if repaired:
        await session.commit()
    return repaired


async def _is_contaminated(session: AsyncSession, *, site_id: str, article_id: str | None, target_url: str | None, published_at: datetime) -> bool:
    result = await session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM seo_agent.articles
                 WHERE id = CAST(:article_id AS uuid) AND updated_at > :published_at + interval '1 hour'
                UNION ALL
                SELECT 1 FROM seo_agent.posts
                 WHERE site_id = CAST(:site_id AS uuid) AND :target_url <> ''
                   AND lower(rtrim(url, '/')) = lower(rtrim(:target_url, '/'))
                   AND modified_at > :published_at + interval '1 hour'
            ) AS contaminated
            """
        ),
        {"site_id": site_id, "article_id": article_id, "target_url": target_url or "", "published_at": published_at},
    )
    return bool(result.scalar_one())


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def process_due_effects(session: AsyncSession, *, limit: int = 3) -> dict[str, Any]:
    rows = await session.execute(
        text(
            """
            SELECT id::text AS id, site_id::text AS site_id, article_id::text AS article_id,
                   target_url, payload
              FROM seo_agent.tasks
             WHERE task_type = 'review' AND status = 'queued'
               AND payload->>'kind' = 'strategy_effect'
               AND payload ? 'published_at' AND run_after <= now()
             ORDER BY run_after, created_at
             FOR UPDATE SKIP LOCKED
             LIMIT :limit
            """
        ),
        {"limit": max(1, min(limit, 10))},
    )
    processed: list[dict[str, Any]] = []
    for row in rows.mappings().all():
        payload = dict(row["payload"] or {})
        next_checkpoint = payload.get("next_checkpoint") or {}
        day = int(next_checkpoint.get("day") or CHECKPOINT_DAYS[0])
        published_at = _as_datetime(payload["published_at"])
        snapshot = await capture_metrics(
            session,
            site_id=row["site_id"],
            query=payload.get("query"),
            target_url=row["target_url"],
            metric_scope=(payload.get("baseline") or {}).get("metric_scope"),
        )
        contaminated = await _is_contaminated(
            session,
            site_id=row["site_id"],
            article_id=row["article_id"],
            target_url=row["target_url"],
            published_at=published_at,
        )
        delta = metric_delta(payload.get("baseline") or {}, snapshot)
        outcome = classify_outcome(snapshot, delta, contaminated=contaminated, day=day)
        checkpoint = {"day": day, "snapshot": snapshot, "delta": delta, "outcome": outcome}
        checkpoints = [*(payload.get("checkpoints") or []), checkpoint]
        finished = day >= CHECKPOINT_DAYS[-1]
        next_day = next((value for value in CHECKPOINT_DAYS if value > day), None)
        next_due = published_at + timedelta(days=next_day) if next_day else None
        payload.update(
            {
                "checkpoints": checkpoints,
                "outcome": outcome,
                "next_checkpoint": {"day": next_day, "due_at": next_due.isoformat()} if next_due else None,
            }
        )
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status = :status, run_after = :run_after, payload = CAST(:payload AS jsonb),
                       decision = CAST(:decision AS jsonb),
                       finished_at = CASE WHEN :finished THEN now() ELSE NULL END, updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "id": row["id"],
                "status": "done" if finished else "queued",
                "run_after": next_due,
                "payload": json.dumps(payload, ensure_ascii=False),
                "decision": json.dumps({"outcome": outcome, "checkpoint_day": day}, ensure_ascii=False),
                "finished": finished,
            },
        )
        processed.append({"id": row["id"], "day": day, "outcome": outcome, "finished": finished})
    await session.commit()
    return {"processed": len(processed), "items": processed}


async def list_effects(session: AsyncSession, *, business_id: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            """
            SELECT t.id, t.lifecycle_status, t.site_id, t.site_name, t.article_id,
                   t.target_url, t.title, t.payload, t.run_after, t.created_at, t.updated_at, t.finished_at
              FROM (
                SELECT DISTINCT ON (t.payload->>'execution_task_id')
                       t.id::text AS id, t.status AS lifecycle_status, t.site_id::text AS site_id,
                       s.name AS site_name, t.article_id::text AS article_id,
                       t.target_url, t.title, t.payload, t.run_after, t.created_at, t.updated_at, t.finished_at
                  FROM seo_agent.tasks t LEFT JOIN seo_agent.sites s ON s.id = t.site_id
                 WHERE t.task_type = 'review' AND t.status <> 'canceled'
                   AND t.payload->>'kind' = 'strategy_effect'
                   AND t.payload->>'business_id' = :business_id
                 ORDER BY t.payload->>'execution_task_id', t.created_at DESC, t.id DESC
              ) t
             ORDER BY t.created_at DESC LIMIT :limit
            """
        ),
        {"business_id": business_id, "limit": max(1, min(limit, 500))},
    )
    items = []
    for row in rows.mappings().all():
        item, payload = dict(row), dict(row["payload"] or {})
        item.pop("payload", None)
        item.update(
            {
                **payload,
                "status": payload.get("outcome") or "observing",
                "action_type": payload.get("action"),
                "next_check_at": (payload.get("next_checkpoint") or {}).get("due_at"),
                "baseline": payload.get("baseline") or {},
                "checkpoints": payload.get("checkpoints") or [],
                "cooldown_until": payload.get("cooldown_until"),
            }
        )
        items.append(item)
    return items


__all__ = [
    "CHECKPOINT_DAYS",
    "POLICY_VERSION",
    "cancel_unpublished_effect",
    "backfill_missing_effect_target_urls",
    "capture_metrics",
    "classify_outcome",
    "ensure_effect",
    "list_effects",
    "load_scope_locks",
    "mark_effect_published",
    "metric_delta",
    "process_due_effects",
    "resolve_strategy_target_url",
    "strategy_identity",
]
