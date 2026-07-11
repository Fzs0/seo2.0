"""v1 Google 数据路由：sources 列表 / 触发同步 / 同步日志 / GSC 数据 / GA4 数据 / Dashboard 汇总。

所有路由前缀 /api/v1/analytics。

设计原则：
  - 任何返回 source 详细信息的端点都走 list_public()（脱敏，不暴露 SA private_key）
  - 查询参数 site_id 优先；找不到 site 时返回 404（前端提示"未配置"）
  - GSC / GA4 数据查询走 seo_agent.gsc_query_daily / ga4_session_daily（已同步的数据）；
    不在前端每次刷新时打 Google API
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.google_config import default_date_range, get_store
from app.services.google_sync import sync_all_sources, sync_source

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------- 1. Source 配置（脱敏） ----------

@router.get("/analytics/sources")
async def list_sources() -> dict[str, Any]:
    """列出所有 Google 数据源（脱敏：不含 SA key）。"""
    store = get_store()
    return {
        "items": store.list_public(),
        "total": len(store.list_public()),
        "configError": store.load_error(),
    }


@router.get("/analytics/sources/by-site/{site_id}")
async def get_source_for_site(site_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """按 seo_agent.sites.id 反查 source。返回 {site, source, recentSyncLog}。"""
    row = await session.execute(
        text(
            "SELECT id, site_key, name, domain, base_url, market, language_code FROM seo_agent.sites WHERE id = :id"
        ),
        {"id": site_id},
    )
    site = row.mappings().first()
    if not site:
        raise HTTPException(404, f"site not found: {site_id}")
    site_dict = dict(site)

    # 反查 source（按 domain）
    domain = (site_dict.get("domain") or "").lower().lstrip("www.")
    base = (site_dict.get("base_url") or "").lower()
    source = None
    if domain:
        source = get_store().get_by_domain(domain)
    if not source and base:
        source = get_store().get_by_domain(base)

    # 最近一次同步日志（按 site_id）
    log_rows = await session.execute(
        text(
            """
            SELECT id, source_type, status, trigger, rows_fetched, rows_written,
                   duration_ms, error_message, started_at, finished_at
              FROM seo_agent.google_sync_log
             WHERE site_id = :site_id
             ORDER BY started_at DESC
             LIMIT 6
            """
        ),
        {"site_id": site_id},
    )
    recent_log = [dict(r) for r in log_rows.mappings().all()]

    public_source = None
    if source:
        public = [s for s in get_store().list_public() if s["id"] == source.id]
        public_source = public[0] if public else None

    return {
        "site": site_dict,
        "source": public_source,
        "configured": public_source is not None,
        "recentSyncLog": recent_log,
    }


# ---------- 2. 触发同步 ----------

@router.post("/analytics/sync")
async def trigger_sync(body: dict[str, Any], session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """触发手动同步。body:
      - { sourceId?: string }: 同步单个 source
      - { siteId?: string }: 按站点反查 source 后同步
      - { all?: true, daysBack?: int }: 同步全部 source
      - { skipGsc?: bool, skipGa4?: bool }
    """
    if body.get("all"):
        days_back = body.get("daysBack")
        results = await sync_all_sources(session, days_back=days_back, trigger="manual")
        return {"ok": all(r.get("ok") for r in results), "results": results}
    if body.get("sourceId"):
        return await sync_source(
            session,
            body["sourceId"],
            days_back=body.get("daysBack"),
            trigger="manual",
            skip_gsc=body.get("skipGsc", False),
            skip_ga4=body.get("skipGa4", False),
        )
    if body.get("siteId"):
        site_id = body["siteId"]
        row = await session.execute(
            text("SELECT id, site_key, domain, base_url FROM seo_agent.sites WHERE id = :id"),
            {"id": site_id},
        )
        site = row.mappings().first()
        if not site:
            raise HTTPException(404, f"site not found: {site_id}")
        site_key = (site["site_key"] or "").lower()
        domain = (site["domain"] or "").lower().lstrip("www.")
        source = get_store().get_by_domain(domain)
        if not source and site["base_url"]:
            source = get_store().get_by_domain(str(site["base_url"]))
        if not source and site_key:
            source = next(
                (s for s in get_store().list_all() if s.gsc_host().split(".")[0] == site_key),
                None,
            )
        if not source:
            raise HTTPException(
                404,
                f"no Google source configured for site {site_id}",
            )
        return await sync_source(
            session,
            source.id,
            days_back=body.get("daysBack"),
            trigger="manual",
            skip_gsc=body.get("skipGsc", False),
            skip_ga4=body.get("skipGa4", False),
        )
    raise HTTPException(400, "body must contain one of: sourceId / siteId / all=true")


@router.get("/analytics/sync-log")
async def list_sync_log(
    site_id: str | None = None,
    source_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    where = "WHERE 1=1"
    params: dict[str, Any] = {"limit": limit}
    if site_id:
        where += " AND site_id = :site_id"
        params["site_id"] = site_id
    if source_id:
        where += " AND source_id = :source_id"
        params["source_id"] = source_id
    rows = await session.execute(
        text(
            f"""
            SELECT id, source_id, site_id, source_type, range_start, range_end,
                   CASE WHEN status = 'done' THEN 'success' ELSE status END AS status,
                   trigger, rows_fetched, rows_written, duration_ms,
                   error_message, started_at, finished_at
              FROM seo_agent.google_sync_log
              {where}
             ORDER BY started_at DESC
             LIMIT :limit
            """
        ),
        params,
    )
    items = [dict(r) for r in rows.mappings().all()]
    return {"items": items, "total": len(items)}


# ---------- 3. Dashboard 汇总（前端 DashboardPage 用） ----------

@router.get("/analytics/dashboard/{site_id}")
async def get_dashboard(
    site_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """单站点 dashboard 汇总：GSC 28d + GA4 28d 一次性返回。"""
    # 站点元信息
    row = await session.execute(
        text(
            "SELECT id, site_key, name, domain, base_url, market, language_code FROM seo_agent.sites WHERE id = :id"
        ),
        {"id": site_id},
    )
    site = row.mappings().first()
    if not site:
        raise HTTPException(404, f"site not found: {site_id}")
    site_dict = dict(site)

    # GSC 28d 聚合
    gsc_row = await session.execute(
        text(
            """
            SELECT clicks, impressions, ctr, avg_position
              FROM seo_agent.v_gsc_site_28d
             WHERE site_id = :site_id
            """
        ),
        {"site_id": site_id},
    )
    gsc = gsc_row.mappings().first()
    gsc_28d = dict(gsc) if gsc else None

    # GA4 28d 聚合
    ga4_row = await session.execute(
        text(
            """
            SELECT sessions, users, new_users, pageviews, engagement_rate,
                   bounce_rate, avg_session_duration, conversions, revenue
              FROM seo_agent.v_ga4_site_28d
             WHERE site_id = :site_id
            """
        ),
        {"site_id": site_id},
    )
    ga4 = ga4_row.mappings().first()
    ga4_28d = dict(ga4) if ga4 else None

    # 7 天趋势（按日期）
    gsc_trend_rows = await session.execute(
        text(
            """
            SELECT date, sum(clicks) AS clicks, sum(impressions) AS impressions
              FROM seo_agent.gsc_query_daily
             WHERE site_id = :site_id
               AND date >= current_date - INTERVAL '7 days'
             GROUP BY date
             ORDER BY date
            """
        ),
        {"site_id": site_id},
    )
    gsc_trend = [dict(r) for r in gsc_trend_rows.mappings().all()]

    ga4_trend_rows = await session.execute(
        text(
            """
            SELECT date, sessions, total_users AS users, pageviews
              FROM seo_agent.ga4_session_daily
             WHERE site_id = :site_id
               AND channel = 'all'
               AND date >= current_date - INTERVAL '7 days'
             ORDER BY date
            """
        ),
        {"site_id": site_id},
    )
    ga4_trend = [dict(r) for r in ga4_trend_rows.mappings().all()]

    # 最近一次同步时间（任一 type）
    last_log = await session.execute(
        text(
            """
            SELECT source_type,
                   CASE WHEN status = 'done' THEN 'success' ELSE status END AS status,
                   started_at, finished_at, rows_written
              FROM seo_agent.google_sync_log
             WHERE site_id = :site_id
             ORDER BY started_at DESC
             LIMIT 2
            """
        ),
        {"site_id": site_id},
    )
    last_sync = [dict(r) for r in last_log.mappings().all()]

    # source 是否配置
    domain = (site_dict.get("domain") or "").lower().lstrip("www.")
    base = (site_dict.get("base_url") or "").lower()
    source = get_store().get_by_domain(domain) if domain else None
    if not source and base:
        source = get_store().get_by_domain(base)
    configured = source is not None

    return {
        "site": site_dict,
        "configured": configured,
        "gsc28d": gsc_28d,
        "ga428d": ga4_28d,
        "gsc7dTrend": gsc_trend,
        "ga47dTrend": ga4_trend,
        "lastSync": last_sync,
    }


# ---------- 4. GSC 关键词 / 页面（前端 OpportunitiesPage 用） ----------

@router.get("/analytics/gsc/queries")
async def get_gsc_queries(
    site_id: str,
    days: int = Query(28, ge=1, le=90),
    limit: int = Query(100, ge=1, le=500),
    min_impressions: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """GSC 关键词级聚合（默认 28 天）。"""
    rows = await session.execute(
        text(
            """
            SELECT query, clicks, impressions, ctr, avg_position, last_seen
              FROM seo_agent.v_gsc_query_28d
             WHERE site_id = :site_id
               AND impressions >= :min_impressions
             ORDER BY impressions DESC
             LIMIT :limit
            """
        ),
        {"site_id": site_id, "limit": limit, "min_impressions": min_impressions},
    )
    items = [dict(r) for r in rows.mappings().all()]
    return {"items": items, "total": len(items), "days": 28}


@router.get("/analytics/gsc/opportunities")
async def get_gsc_opportunities(
    site_id: str,
    days: int = Query(28, ge=1, le=90),
    min_impressions: int = Query(100, ge=1),
    max_position: float = Query(20.0, ge=1.0, le=100.0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """SEO 机会关键词：高展示（≥min_impressions）+ 平均排名 ≤max_position。
    这些是"已经接近首页但没进"的关键词，是 Brief 流水线最该攻的目标。
    """
    rows = await session.execute(
        text(
            """
            SELECT query, clicks, impressions, ctr, avg_position, last_seen
              FROM seo_agent.v_gsc_query_28d
             WHERE site_id = :site_id
               AND impressions >= :min_imp
               AND avg_position > 0
               AND avg_position <= :max_pos
             ORDER BY impressions DESC
             LIMIT :limit
            """
        ),
        {
            "site_id": site_id,
            "min_imp": min_impressions,
            "max_pos": max_position,
            "limit": limit,
        },
    )
    items = [dict(r) for r in rows.mappings().all()]

    # 数字格式：position / ctr 转 string（前端更好显示）
    out = []
    for it in items:
        out.append({
            "query": it["query"],
            "clicks": int(it["clicks"] or 0),
            "impressions": int(it["impressions"] or 0),
            "ctr": round(float(it["ctr"] or 0), 4),
            "avgPosition": round(float(it["avg_position"] or 0), 1),
            "lastSeen": it["last_seen"].isoformat() if it.get("last_seen") else None,
        })
    return {
        "items": out,
        "total": len(out),
        "criteria": {
            "minImpressions": min_impressions,
            "maxPosition": max_position,
            "days": 28,
        },
    }


@router.get("/analytics/gsc/pages")
async def get_gsc_pages(
    site_id: str,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """GSC 页面级聚合。"""
    rows = await session.execute(
        text(
            """
            SELECT page, clicks, impressions, ctr, avg_position, last_seen
              FROM seo_agent.v_gsc_page_28d
             WHERE site_id = :site_id
             ORDER BY clicks DESC
             LIMIT :limit
            """
        ),
        {"site_id": site_id, "limit": limit},
    )
    items = [dict(r) for r in rows.mappings().all()]
    return {"items": items, "total": len(items)}


# ---------- 5. GA4 站点 / 渠道 ----------

@router.get("/analytics/ga4/overview")
async def get_ga4_overview(
    site_id: str,
    days: int = Query(28, ge=1, le=90),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """GA4 站点级 28 天汇总 + 日趋势。"""
    summary_row = await session.execute(
        text(
            "SELECT sessions, users, new_users, pageviews, engagement_rate, bounce_rate, "
            "avg_session_duration, conversions, revenue "
            "FROM seo_agent.v_ga4_site_28d WHERE site_id = :site_id"
        ),
        {"site_id": site_id},
    )
    summary = summary_row.mappings().first()
    trend_rows = await session.execute(
        text(
            """
            SELECT date, sessions, total_users AS users, pageviews,
                   engagement_rate, conversions
              FROM seo_agent.ga4_session_daily
             WHERE site_id = :site_id
               AND channel = 'all'
               AND date >= current_date - INTERVAL '28 days'
             ORDER BY date
            """
        ),
        {"site_id": site_id},
    )
    trend = [dict(r) for r in trend_rows.mappings().all()]
    return {
        "summary": dict(summary) if summary else None,
        "trend": trend,
        "days": 28,
    }


@router.get("/analytics/ga4/channels")
async def get_ga4_channels(
    site_id: str,
    days: int = Query(28, ge=1, le=90),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """GA4 按渠道拆分（organic_search / direct / referral …）。"""
    rows = await session.execute(
        text(
            """
            SELECT channel,
                   sum(sessions) AS sessions,
                   sum(total_users) AS users,
                   sum(pageviews) AS pageviews,
                   round(sum(engaged_sessions)::numeric
                         / NULLIF(sum(sessions), 0), 4) AS engagement_rate,
                   sum(conversions) AS conversions,
                   sum(revenue) AS revenue
              FROM seo_agent.ga4_session_daily
             WHERE site_id = :site_id
               AND date >= current_date - INTERVAL '28 days'
               AND channel <> 'all'
             GROUP BY channel
             ORDER BY sessions DESC
            """
        ),
        {"site_id": site_id},
    )
    items = [dict(r) for r in rows.mappings().all()]
    return {"items": items, "total": len(items), "days": 28}


# ---------- 6. Ping（健康检查） ----------

@router.get("/analytics/ping")
async def ping_all() -> dict[str, Any]:
    """每个 source 的 GSC + GA4 鉴权 + API 探活（不打真实数据，只换 token + 1 行 test）。"""
    from app.clients.google_analytics import GA4Client
    from app.clients.google_search_console import GSCClient

    out = []
    for src in get_store().list_all():
        gsc_p = await GSCClient(src).ping()
        ga4_p = await GA4Client(src).ping()
        out.append({
            "sourceId": src.id,
            "name": src.name,
            "gscSiteUrl": src.gsc_site_url,
            "ga4PropertyId": src.ga4_property_id,
            "gsc": gsc_p,
            "ga4": ga4_p,
        })
    return {"items": out, "total": len(out)}
