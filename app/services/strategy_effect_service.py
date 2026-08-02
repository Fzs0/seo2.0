"""策略执行基线、效果检查和冷却状态，复用 seo_agent.tasks。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


POLICY_VERSION = "strategy_policy_v1"
CHECKPOINT_DAYS = (7, 14, 28, 56, 90)
DEFAULT_TOPIC_COOLDOWN_DAYS = 21
MIN_TOPIC_COOLDOWN_DAYS = 14
MAX_TOPIC_COOLDOWN_DAYS = 28
URL_CHANGE_BASELINE_NOTE = (
    "文章公开 URL 已切换为 canonical 地址；旧 URL 的发布前基线不可直接比较。"
)
LATE_BASELINE_NOTE = "发布前基线的采集时间不早于文章发布时间；该数据可能在发布后产生，不能用于前后对比。"
NEW_ARTICLE_ZERO_BASELINE_NOTE = (
    "新文章发布前不存在该公开页面；页面级基线为结构性零值，不是发布后补录的历史数据。"
)


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


def reconcile_effect_target_url(
    payload: dict[str, Any],
    *,
    current_target_url: Any,
    new_target_url: Any,
) -> dict[str, Any]:
    """Reconcile an effect with a newly confirmed public article URL.

    Update strategies measured the old page before publication, so changing
    that page identity invalidates the comparison baseline. New-article
    strategies intentionally start from a zero landing-page baseline and must
    remain valid.
    """
    reconciled = dict(payload or {})
    new_url = str(new_target_url or "").strip()
    if not new_url:
        return reconciled

    previous_url = str(
        current_target_url or reconciled.get("target_url") or ""
    ).strip()
    reconciled["target_url"] = new_url
    url_changed = _effect_url_key(previous_url) != _effect_url_key(new_url)
    initial_new_article = (
        reconciled.get("action") == "new_article"
        and not (reconciled.get("checkpoints") or [])
        and _has_structural_zero_baseline(reconciled)
    )
    if url_changed and not initial_new_article:
        reconciled["baseline_valid"] = False
        if previous_url and not reconciled.get("previous_target_url"):
            reconciled["previous_target_url"] = previous_url
        if not reconciled.get("baseline_note"):
            reconciled["baseline_note"] = URL_CHANGE_BASELINE_NOTE
    return reconciled


def _has_structural_zero_baseline(payload: dict[str, Any]) -> bool:
    return (payload.get("baseline") or {}).get("kind") == "structural_zero"


def _effect_url_key(value: Any) -> tuple[str, str, str]:
    raw = str(value or "").strip()
    if not raw:
        return ("", "", "")
    parsed = urlsplit(raw)
    return (
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        parsed.path.rstrip("/") or "/",
    )


def _effect_url_db_key(value: Any) -> str:
    scheme, host, path = _effect_url_key(value)
    return urlunsplit((scheme, host, path, "", ""))


def _is_page_url(value: Any) -> bool:
    return isinstance(value, str) and str(value).strip().startswith(("https://", "http://", "/"))


def _is_absolute_page_url(value: Any) -> bool:
    return isinstance(value, str) and str(value).strip().startswith(("https://", "http://"))


def _captured_before_publication(
    baseline: dict[str, Any],
    published_at: datetime,
) -> bool:
    raw = baseline.get("captured_at")
    if not raw:
        return False
    try:
        captured_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return captured_at < published_at


def _hash(parts: list[Any]) -> str:
    value = "|".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_host(value: str) -> str:
    host = value.casefold().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def normalize_canonical_url(
    value: Any,
    *,
    site_url: Any = None,
    site_domain: Any = None,
    query_policy: str = "drop",
) -> str:
    """Normalize a public page identity and validate its owning site."""
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("canonical URL must be an absolute HTTP(S) URL")
    host = _canonical_host(parsed.hostname)
    allowed_raw = str(site_url or site_domain or "").strip()
    if allowed_raw:
        allowed = urlsplit(
            allowed_raw if "://" in allowed_raw else f"https://{allowed_raw}"
        )
        if host != _canonical_host(allowed.hostname or ""):
            raise ValueError("canonical URL does not belong to the target site")
    port = parsed.port
    netloc = host if port in {None, 80, 443} else f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    if query_policy == "drop":
        query = ""
    elif query_policy == "keep":
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    else:
        raise ValueError("query_policy must be 'drop' or 'keep'")
    return urlunsplit(("https", netloc, path, query, ""))


def topic_cooldown_until(
    published_at: datetime, days: int = DEFAULT_TOPIC_COOLDOWN_DAYS
) -> datetime:
    if not MIN_TOPIC_COOLDOWN_DAYS <= int(days) <= MAX_TOPIC_COOLDOWN_DAYS:
        raise ValueError("topic cooldown must be between 14 and 28 days")
    return published_at + timedelta(days=int(days))


def should_lock_topic_cluster(
    *,
    relation: str | None,
    observation_active: bool,
    cannibalization_detected: bool = False,
) -> bool:
    if cannibalization_detected:
        return True
    return bool(observation_active and relation in {"same", "similar", "overlapping"})


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
    target_url: Any = None,
    site_url: Any = None,
    site_domain: Any = None,
    query_policy: str = "drop",
) -> dict[str, str]:
    is_update = action == "update_article"
    canonical_url = ""
    if is_update and target_url:
        canonical_url = normalize_canonical_url(
            target_url,
            site_url=site_url,
            site_domain=site_domain,
            query_policy=query_policy,
        )
    target = (
        canonical_url
        if is_update
        else topic_cluster_id or query or post_id or article_id
    )
    lock_scope = "url" if is_update and target else (
        "unresolved_url" if is_update else "topic_cluster"
    )
    scope_key = _hash([business_id, site_id, market, language_code, target]) if target else ""
    strategy_fingerprint = _hash([business_id, site_id, market, language_code, target, action, objective])
    evidence_fingerprint = hashlib.sha256(
        json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "scope_key": scope_key,
        "lock_scope": lock_scope,
        "lock_key": scope_key,
        "canonical_url": canonical_url,
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
        "target_url": target_url,
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
    baseline_valid: bool = True,
    day: int = 90,
) -> str:
    if contaminated:
        return "contaminated"
    if not baseline_valid:
        return "inconclusive"
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


async def load_scope_locks(
    session: AsyncSession,
    *,
    business_id: str,
    exclude_strategy_run_id: str | None = None,
) -> dict[str, str]:
    rows = await session.execute(
        text(
            """
            SELECT status, payload->>'scope_key' AS scope_key,
                   payload->>'lock_scope' AS lock_scope,
                   payload->>'lock_key' AS lock_key,
                   payload->>'action' AS action,
                   payload->>'target_url' AS target_url,
                   payload->>'topic_relation' AS topic_relation,
                   COALESCE((payload->>'cannibalization_detected')::boolean, false)
                       AS cannibalization_detected,
                   COALESCE((payload->>'cooldown_until')::timestamptz > now(), false) AS cooling,
                   false AS hard_active
              FROM seo_agent.tasks
             WHERE task_type = 'review' AND payload->>'kind' = 'strategy_effect'
               AND payload->>'business_id' = :business_id
            UNION ALL
            SELECT status, payload->>'scope_key' AS scope_key,
                   payload->>'lock_scope' AS lock_scope,
                   payload->>'lock_key' AS lock_key,
                   payload#>>'{strategy,strategy_type}' AS action,
                   NULL AS target_url,
                   payload#>>'{strategy,topic_relation}' AS topic_relation,
                   COALESCE((payload#>>'{strategy,cannibalization_detected}')::boolean, false)
                       AS cannibalization_detected,
                   false AS cooling,
                    true AS hard_active
              FROM seo_agent.tasks
             WHERE task_type IN ('new_article', 'update_article') AND status IN ('queued', 'running')
               AND payload#>>'{strategy,business_id}' = :business_id
            UNION ALL
            SELECT strategy.status, strategy.payload->>'scope_key' AS scope_key,
                   strategy.payload->>'lock_scope' AS lock_scope,
                   strategy.payload->>'lock_key' AS lock_key,
                   strategy.decision->>'strategy_type' AS action,
                   strategy.decision->>'target_url' AS target_url,
                   NULL AS topic_relation,
                   false AS cannibalization_detected,
                   false AS cooling,
                    true AS hard_active
              FROM seo_agent.tasks strategy
              JOIN seo_agent.tasks parent_run
                ON parent_run.id=CAST(strategy.payload->>'strategy_run_id' AS uuid)
               AND parent_run.task_type='review'
               AND parent_run.payload->>'kind'='strategy_run'
               AND parent_run.status IN ('queued', 'running', 'blocked')
             WHERE strategy.task_type='review'
               AND strategy.payload->>'kind'='seo_strategy'
               AND strategy.payload->>'business_id'=:business_id
               AND strategy.payload->>'schedule_class'='execute_now'
               AND strategy.status IN ('queued', 'running', 'blocked')
               AND (
                    CAST(:exclude_strategy_run_id AS text) IS NULL
                    OR strategy.payload->>'strategy_run_id'<>CAST(:exclude_strategy_run_id AS text)
               )
            UNION ALL
            SELECT status,
                   payload#>>'{strategy_decision,scope_key}' AS scope_key,
                   payload#>>'{strategy_decision,lock_scope}' AS lock_scope,
                   payload#>>'{strategy_decision,lock_key}' AS lock_key,
                   payload->>'action_type' AS action,
                   payload->>'target_url' AS target_url,
                   NULL AS topic_relation,
                   false AS cannibalization_detected,
                    false AS cooling,
                     CASE
                       WHEN payload->>'correction_status'='resolved' THEN false
                       WHEN status='blocked'
                        AND payload->>'recovery_status' IN (
                         'confirmed_not_applied','confirmed_absent'
                       ) THEN false
                      ELSE true
                    END AS hard_active
              FROM seo_agent.tasks
             WHERE task_type='review'
               AND payload->>'kind'='strategy_action'
               AND payload->>'business_id'=:business_id
               AND status IN ('queued', 'running', 'blocked')
               AND (
                    CAST(:exclude_strategy_run_id AS text) IS NULL
                    OR payload->>'run_id'<>CAST(:exclude_strategy_run_id AS text)
               )
            UNION ALL
            SELECT status, payload->>'scope_key' AS scope_key,
                   payload->>'lock_scope' AS lock_scope,
                   payload->>'lock_key' AS lock_key,
                   payload->>'action_type' AS action,
                   payload->>'target_url' AS target_url,
                   NULL AS topic_relation,
                   false AS cannibalization_detected,
                   false AS cooling,
                   true AS hard_active
              FROM seo_agent.tasks
             WHERE task_type='effect_check'
               AND payload->>'kind'='strategy_action_observation'
               AND payload->>'business_id'=:business_id
               AND status IN ('queued', 'running')
               AND (
                    CAST(:exclude_strategy_run_id AS text) IS NULL
                    OR payload->>'run_id'<>CAST(:exclude_strategy_run_id AS text)
               )
            """
        ),
        {
            "business_id": business_id,
            "exclude_strategy_run_id": exclude_strategy_run_id,
        },
    )
    locks: dict[str, str] = {}
    for row in rows.mappings().all():
        lock_scope = str(row.get("lock_scope") or "")
        lock_key = str(row.get("lock_key") or row["scope_key"] or "")
        if not lock_key or lock_scope == "unresolved_url":
            continue
        if bool(row.get("hard_active")) or (
            row["status"] in {"queued", "running"} and not row["target_url"]
        ):
            locks[lock_key] = "同一页面或意图已有策略正在执行或观察"
        elif row["action"] == "update_article" and row["cooling"]:
            locks[lock_key] = "同一页面仍在 28 天更新冷却期"
        elif row["action"] == "new_article" and should_lock_topic_cluster(
            relation=str(row.get("topic_relation") or "same"),
            observation_active=bool(row["cooling"]),
            cannibalization_detected=bool(row.get("cannibalization_detected")),
        ):
            locks[lock_key] = "主题仍在观察期，或已确认存在搜索意图重叠/关键词蚕食"
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
    target_url = resolve_strategy_target_url(strategy)
    if strategy.get("strategy_type") == "update_article" and not _is_absolute_page_url(target_url):
        raise ValueError("更新旧文章前必须确认正式公开地址，请先同步目标站点文章")
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
        existing_payload = dict(existing["payload"] or {})
        if strategy.get("strategy_type") == "update_article" and (
            _effect_url_key(existing_payload.get("target_url")) != _effect_url_key(target_url)
            or _effect_url_key((existing_payload.get("baseline") or {}).get("target_url"))
            != _effect_url_key(target_url)
            or (existing_payload.get("baseline") or {}).get("kind") != "measured"
        ):
            if existing_payload.get("published_at"):
                existing_payload = reconcile_effect_target_url(
                    existing_payload,
                    current_target_url=existing_payload.get("target_url"),
                    new_target_url=target_url,
                )
            else:
                baseline = await capture_metrics(
                    session,
                    site_id=site_id,
                    query=str(strategy.get("query") or "") or None,
                    target_url=target_url,
                )
                baseline.update({"kind": "measured", "target_url": target_url})
                existing_payload.update({
                    "target_url": target_url,
                    "baseline": baseline,
                    "baseline_valid": True,
                })
                existing_payload.pop("baseline_note", None)
                existing_payload.pop("previous_target_url", None)
            await session.execute(
                text(
                    "UPDATE seo_agent.tasks SET target_url = :target_url, "
                    "payload = CAST(:payload AS jsonb), updated_at = now() "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {
                    "id": existing["id"],
                    "target_url": target_url,
                    "payload": json.dumps(existing_payload, ensure_ascii=False),
                },
            )
        if existing["status"] == "canceled":
            existing_payload.pop("canceled_reason", None)
            await session.execute(
                text(
                    "UPDATE seo_agent.tasks SET status = 'blocked', finished_at = NULL, error_message = NULL, "
                    "payload = (COALESCE(payload, '{}'::jsonb) - 'canceled_reason') || CAST(:payload AS jsonb), "
                    "decision = (COALESCE(decision, '{}'::jsonb) - 'canceled_reason') || CAST(:decision AS jsonb), "
                    "updated_at = now() WHERE id = CAST(:id AS uuid) AND status = 'canceled'"
                ),
                {
                    "id": existing["id"],
                    "payload": json.dumps({"outcome": "pending_confirmation"}),
                    "decision": json.dumps({"outcome": "pending_confirmation"}),
                },
            )
            existing_payload["outcome"] = "pending_confirmation"
        outcome = (
            "pending_confirmation"
            if existing["status"] in {"blocked", "canceled"}
            else "observing"
        )
        return {"id": existing["id"], **existing_payload, "outcome": outcome}
    scope_key = strategy.get("scope_key")
    scope_lock_scope = strategy.get("lock_scope") or (
        "url"
        if strategy.get("strategy_type") == "update_article"
        else "topic_cluster"
    )
    scope_lock_key = strategy.get("lock_key") or scope_key
    if scope_lock_key:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {
                "key": (
                    "strategy-effect-scope:"
                    f"{strategy.get('business_id')}:{site_id}:"
                    f"{scope_lock_scope}:{scope_lock_key}"
                )
            },
        )
        active_conflict = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id,
                           payload->>'execution_task_id' AS execution_task_id
                      FROM seo_agent.tasks
                     WHERE task_type='review'
                       AND payload->>'kind'='strategy_effect'
                       AND status IN ('queued', 'running', 'blocked')
                       AND site_id::text=:site_id
                       AND payload->>'business_id'=:business_id
                       AND COALESCE(payload->>'execution_task_id', '')
                           <> :execution_task_id
                       AND (
                         payload->>'lock_key'=:lock_key
                         OR (
                           :action='update_article'
                           AND lower(regexp_replace(
                             COALESCE(target_url, payload->>'target_url', ''),
                             '/+$', ''
                           ))=:target_key
                         )
                       )
                     /* STRATEGY_EFFECT_ACTIVE_SCOPE */
                     ORDER BY created_at DESC
                     LIMIT 1
                     FOR UPDATE
                    """
                ),
                {
                    "site_id": site_id,
                    "business_id": str(strategy.get("business_id") or ""),
                    "execution_task_id": execution_task_id,
                    "lock_key": scope_lock_key,
                    "action": str(strategy.get("strategy_type") or ""),
                    "target_key": _effect_url_db_key(target_url),
                },
            )
        ).mappings().first()
        if (
            active_conflict
            and strategy.get("corrective_action_validated") is not True
        ):
            raise ValueError(
                "STRATEGY_EFFECT_TARGET_ALREADY_ACTIVE: "
                f"effect_id={active_conflict['id']}"
            )
    identity = {
        "scope_key": scope_key,
        # Legacy approved decisions remain executable; newly generated
        # decisions persist these fields explicitly.
        "lock_scope": strategy.get("lock_scope")
        or ("url" if strategy.get("strategy_type") == "update_article" else "topic_cluster"),
        "lock_key": strategy.get("lock_key") or scope_key,
        "strategy_fingerprint": strategy.get("strategy_fingerprint"),
        "evidence_fingerprint": strategy.get("evidence_fingerprint"),
        "policy_version": strategy.get("policy_version"),
    }
    if not all(identity.values()):
        raise ValueError("策略缺少生命周期指纹，请重新生成并审核策略")
    baseline = await capture_metrics(
        session,
        site_id=site_id,
        query=str(strategy.get("query") or "") or None,
        target_url=target_url,
    )
    baseline.update({"kind": "measured", "target_url": target_url})
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
        "baseline_valid": True,
        "checkpoints": [],
        "outcome": "pending_confirmation",
        "cooldown_until": None,
        "topic_relation": strategy.get("topic_relation"),
        "cannibalization_detected": bool(strategy.get("cannibalization_detected")),
        "next_checkpoint": None,
    }
    inserted = await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks
              (task_type, status, priority, site_id, article_id, target_url, title, payload, decision)
            VALUES
              ('review', 'blocked', 'P2', CAST(:site_id AS uuid), CAST(:article_id AS uuid), :target_url,
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
            "decision": json.dumps({"outcome": "pending_confirmation"}),
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
            "WHERE task_type = 'review' AND status IN ('queued', 'running', 'blocked') "
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
    topic_cooldown_days: int = DEFAULT_TOPIC_COOLDOWN_DAYS,
) -> None:
    article = (
        await session.execute(
            text(
                "SELECT site_id::text AS site_id, published_url, published_at, "
                "(SELECT payload FROM seo_agent.tasks WHERE task_type = 'review' "
                "AND payload->>'kind' = 'strategy_effect' "
                "AND payload->>'execution_task_id' = :execution_task_id LIMIT 1) AS effect_payload "
                "FROM seo_agent.articles WHERE id = CAST(:id AS uuid)"
            ),
            {"id": article_id, "execution_task_id": execution_task_id},
        )
    ).mappings().first()
    published_at = (article or {}).get("published_at") or datetime.now(timezone.utc)
    effect_payload = dict((article or {}).get("effect_payload") or {})
    effect_target_url = effect_payload.get("target_url")
    candidates = (
        (effect_target_url, (article or {}).get("published_url"), target_url)
        if action == "update_article"
        else ((article or {}).get("published_url"), target_url, effect_target_url)
    )
    url = next(
        (str(value).strip() for value in candidates if _is_page_url(value)),
        None,
    )
    if action == "new_article" and not url:
        raise ValueError("新文章发布后缺少正式公开地址，无法创建效果观察")
    if article and article.get("site_id") and url:
        superseded = {
            "outcome": "inconclusive",
            "contaminated": True,
            "canceled_reason": "superseded_by_confirmed_publication",
            "superseded_by_execution_task_id": execution_task_id,
        }
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status='canceled', run_after=NULL, finished_at=now(),
                       updated_at=now(),
                       payload=payload || CAST(:payload AS jsonb),
                       decision=decision || CAST(:decision AS jsonb)
                 WHERE task_type='review'
                   AND payload->>'kind'='strategy_effect'
                   AND status IN ('queued', 'running', 'blocked')
                   AND site_id::text=:site_id
                   AND COALESCE(payload->>'execution_task_id', '')
                       <> :execution_task_id
                   AND lower(regexp_replace(
                         COALESCE(target_url, payload->>'target_url', ''),
                         '/+$', ''
                       ))=:target_key
                 /* SUPERSEDE_ACTIVE_EFFECT_AFTER_CONFIRMED_PUBLICATION */
                """
            ),
            {
                "site_id": str(article["site_id"]),
                "execution_task_id": execution_task_id,
                "target_key": _effect_url_db_key(url),
                "payload": json.dumps(superseded, ensure_ascii=False),
                "decision": json.dumps(superseded, ensure_ascii=False),
            },
        )
    cooldown_until = (
        published_at + timedelta(days=28)
        if action == "update_article"
        else topic_cooldown_until(published_at, topic_cooldown_days)
        if action == "new_article"
        else None
    )
    next_due = published_at + timedelta(days=CHECKPOINT_DAYS[0])
    baseline = dict(effect_payload.get("baseline") or {})
    patch = {
        "article_id": article_id,
        "target_url": url,
        "published_at": published_at.isoformat(),
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
        "next_checkpoint": {"day": CHECKPOINT_DAYS[0], "due_at": next_due.isoformat()},
    }
    if action == "new_article":
        if baseline:
            patch["baseline_context"] = baseline
        patch["baseline"] = {
            "kind": "structural_zero",
            "effective_at": published_at.isoformat(),
            "target_url": url,
            "window_days": 0,
            "metric_scope": {"gsc": "page", "ga4": "landing_page"},
            "gsc": {"clicks": 0, "impressions": 0, "avg_position": 0},
            "ga4": {"sessions": 0, "conversions": 0},
        }
        patch["baseline_valid"] = True
        patch["baseline_note"] = NEW_ARTICLE_ZERO_BASELINE_NOTE
    elif baseline:
        baseline.setdefault("kind", "measured")
        patch["baseline"] = baseline
        if not _captured_before_publication(baseline, published_at):
            patch["baseline_valid"] = False
            patch["baseline_note"] = LATE_BASELINE_NOTE
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET article_id = CAST(:article_id AS uuid), target_url = :target_url, run_after = :run_after,
                   payload = (COALESCE(payload, '{}'::jsonb) - 'canceled_reason') || CAST(:patch AS jsonb),
                   decision = COALESCE(decision, '{}'::jsonb) - 'canceled_reason',
                   updated_at = now()
             WHERE task_type = 'review' AND status IN ('blocked', 'queued') AND payload->>'kind' = 'strategy_effect'
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


async def _is_contaminated(session: AsyncSession, *, site_id: str, article_id: str | None, target_url: str | None, published_at: datetime) -> bool:
    del article_id  # Local article bookkeeping is not evidence of a remote content edit.
    result = await session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM seo_agent.posts
                 WHERE site_id = CAST(:site_id AS uuid) AND :target_url <> ''
                   AND lower(rtrim(url, '/')) = lower(rtrim(:target_url, '/'))
                   AND modified_at > CAST(:published_at AS timestamptz) + interval '1 hour'
            ) AS contaminated
            """
        ),
        {"site_id": site_id, "target_url": target_url or "", "published_at": published_at},
    )
    return bool(result.scalar_one())


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _baseline_is_comparable(
    payload: dict[str, Any],
    *,
    published_at: datetime,
    target_url: Any,
) -> bool:
    if payload.get("baseline_valid") is False:
        return False
    baseline = dict(payload.get("baseline") or {})
    kind = baseline.get("kind")
    if (
        not target_url
        or _effect_url_key(baseline.get("target_url")) != _effect_url_key(target_url)
    ):
        return False
    if kind == "structural_zero":
        return payload.get("action") == "new_article"
    if kind == "measured":
        return (
            payload.get("action") == "update_article"
            and _captured_before_publication(baseline, published_at)
        )
    return False


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
        outcome = classify_outcome(
            snapshot,
            delta,
            contaminated=contaminated,
            baseline_valid=_baseline_is_comparable(
                payload,
                published_at=published_at,
                target_url=row["target_url"],
            ),
            day=day,
        )
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
    "capture_metrics",
    "classify_outcome",
    "ensure_effect",
    "list_effects",
    "load_scope_locks",
    "mark_effect_published",
    "metric_delta",
    "normalize_canonical_url",
    "process_due_effects",
    "resolve_strategy_target_url",
    "should_lock_topic_cluster",
    "strategy_identity",
    "topic_cooldown_until",
]
