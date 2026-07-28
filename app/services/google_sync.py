"""Google 数据同步服务。

职责：
  - sync_source(source, session, *, days_back, trigger) — 同步单个 source 的 GSC + GA4 数据到 DB
  - sync_site(site_id, session, ...) — 按 seo_agent.sites.id 找到对应 source 再调 sync_source
  - run_scheduled_sync() — 后台 scheduler 调：遍历全部 source 同步最近 N 天

设计：
  - 写入走 ON CONFLICT DO UPDATE（同一 site+date+dimension 永远最新覆盖）
  - 同步日志写 google_sync_log，每条 source × type 一行
  - 即使 GSC/GA4 任何一个失败，另一个继续跑
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.google_analytics import GA4Client, GA4ClientError
from app.clients.google_search_console import GSCClient, GSCClientError
from app.core.google_config import GoogleSource, get_store

logger = structlog.get_logger(__name__)
_date = date

_GSC_PAGE_DIMENSIONS = ["date", "query", "page", "country", "device"]
GOOGLE_SYNC_TRIGGER_MANUAL = "manual"
GOOGLE_SYNC_TRIGGER_SCHEDULED = "scheduled"
GOOGLE_SYNC_TRIGGER_RETRY = "retry"
GOOGLE_SYNC_TRIGGER_STRATEGY_HOLD_REFRESH = "strategy_hold_refresh"
GOOGLE_SYNC_TRIGGERS = frozenset(
    {
        GOOGLE_SYNC_TRIGGER_MANUAL,
        GOOGLE_SYNC_TRIGGER_SCHEDULED,
        GOOGLE_SYNC_TRIGGER_RETRY,
        GOOGLE_SYNC_TRIGGER_STRATEGY_HOLD_REFRESH,
    }
)

async def _resolve_site_id_by_domain(
    session: AsyncSession, source: GoogleSource
) -> str | None:
    """把 source.gsc_host() 映射到 seo_agent.sites.id。

    匹配策略（按优先级）：
      1. site_key 直接等于 host 的 stem（vapetopline.com → site_key=vapetopline）
      2. base_url LIKE '%host%'
      3. domain LIKE '%host%'

    因为 DB 里 sites.domain 字段存的是 API URL（不是真实域名），纯按 domain 匹配会失败；
    必须兜底匹配 base_url。
    """
    host = source.gsc_host()
    # 剥后缀做 stem
    stem = host.split(".")[0] if "." in host else host

    candidates = [
        # site_key 直接匹配 stem（exdivo.com → exdivo）
        ("site_key = :stem", {"stem": stem}),
        # base_url / domain LIKE 包含 host
        ("lower(coalesce(base_url, '')) LIKE :host_like", {"host_like": f"%{host}%"}),
        ("lower(coalesce(domain, '')) LIKE :host_like", {"host_like": f"%{host}%"}),
    ]
    for clause, params in candidates:
        row = await session.execute(
            text(f"SELECT id FROM seo_agent.sites WHERE {clause} LIMIT 1"),
            params,
        )
        r = row.first()
        if r:
            return str(r[0])
    return None


async def _log_start(
    session: AsyncSession,
    *,
    source_id: str,
    site_id: str | None,
    source_type: str,
    range_start: str,
    range_end: str,
    trigger: str,
) -> int:
    """写一行 google_sync_log（status=running），返回 log.id。"""
    from datetime import date as _date

    def _parse(d: str) -> _date:
        return _date.fromisoformat(d) if isinstance(d, str) else d

    row = await session.execute(
        text(
            """
            INSERT INTO seo_agent.google_sync_log
              (source_id, site_id, source_type, range_start, range_end, status, trigger, started_at)
            VALUES
              (:source_id, :site_id, :source_type, :range_start, :range_end, 'running', :trigger, now())
            RETURNING id
            """
        ),
        {
            "source_id": source_id,
            "site_id": site_id,
            "source_type": source_type,
            "range_start": _parse(range_start),
            "range_end": _parse(range_end),
            "trigger": trigger,
        },
    )
    await session.commit()
    return int(row.scalar_one())


async def _log_done(
    session: AsyncSession,
    log_id: int,
    *,
    rows_fetched: int,
    rows_written: int,
    duration_ms: int,
    error: str | None = None,
) -> None:
    status_val = "done" if error is None else "failed"
    await session.execute(
        text(
            """
            UPDATE seo_agent.google_sync_log
               SET status = :status,
                   rows_fetched = :fetched,
                   rows_written = :written,
                   duration_ms = :duration_ms,
                   error_message = :error,
                   finished_at = now()
             WHERE id = :id
            """
        ),
        {
            "id": log_id,
            "status": status_val,
            "fetched": rows_fetched,
            "written": rows_written,
            "duration_ms": duration_ms,
            "error": error,
        },
    )
    await session.commit()


# ---------- GSC 同步 ----------

async def _sync_gsc(
    session: AsyncSession,
    source: GoogleSource,
    site_id: str,
    *,
    start_date: str,
    end_date: str,
    trigger: str,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    log_id: int | None = None
    try:
        log_id = await _log_start(
            session,
            source_id=source.id,
            site_id=site_id,
            source_type="gsc",
            range_start=start_date,
            range_end=end_date,
            trigger=trigger,
        )
        client = GSCClient(source)
        rows = await client.searchanalytics(
            start_date=start_date,
            end_date=end_date,
            dimensions=_GSC_PAGE_DIMENSIONS,
            row_limit=25000,
        )
        # 写入：每行 ON CONFLICT DO UPDATE
        written = 0
        for r in rows:
            keys = r.get("keys", []) or ["", "", "", "", ""]
            # dimensions 顺序：date, query, page, country, device
            d = keys[0] if len(keys) >= 1 else ""
            q = keys[1] if len(keys) >= 2 else ""
            p = keys[2] if len(keys) >= 3 else ""
            c = keys[3] if len(keys) >= 4 else ""
            dv = keys[4] if len(keys) >= 5 else ""
            d_parsed = _date.fromisoformat(d) if isinstance(d, str) and len(d) == 10 else (
                _date.fromisoformat(d) if isinstance(d, str) else d
            )
            await session.execute(
                text(
                    """
                    INSERT INTO seo_agent.gsc_query_daily
                      (site_id, date, query, page, country, device, clicks, impressions, ctr, position, synced_at)
                    VALUES
                      (:site_id, :date, :query, :page, :country, :device,
                       :clicks, :impressions, :ctr, :position, now())
                    ON CONFLICT (site_id, date, query, page, country, device)
                    DO UPDATE SET
                      clicks = EXCLUDED.clicks,
                      impressions = EXCLUDED.impressions,
                      ctr = EXCLUDED.ctr,
                      position = EXCLUDED.position,
                      synced_at = now()
                    """
                ),
                {
                    "site_id": site_id,
                    "date": d_parsed,
                    "query": q,
                    "page": p,
                    "country": c,
                    "device": dv,
                    "clicks": int(r.get("clicks", 0) or 0),
                    "impressions": int(r.get("impressions", 0) or 0),
                    "ctr": float(r.get("ctr", 0) or 0),
                    "position": float(r.get("position", 0) or 0),
                },
            )
            written += 1
        await session.commit()
        await _log_done(
            session,
            log_id,
            rows_fetched=len(rows),
            rows_written=written,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        return {
            "ok": True,
            "type": "gsc",
            "rows_fetched": len(rows),
            "rows_written": written,
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as e:  # noqa: BLE001
        await session.rollback()
        if log_id is not None:
            await _log_done(
                session,
                log_id,
                rows_fetched=0,
                rows_written=0,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                error=str(e)[:1000],
            )
        return {
            "ok": False,
            "type": "gsc",
            "error": str(e)[:500],
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }


# ---------- GA4 同步 ----------

async def _sync_ga4(
    session: AsyncSession,
    source: GoogleSource,
    site_id: str,
    *,
    start_date: str,
    end_date: str,
    trigger: str,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    log_id: int | None = None
    try:
        log_id = await _log_start(
            session,
            source_id=source.id,
            site_id=site_id,
            source_type="ga4",
            range_start=start_date,
            range_end=end_date,
            trigger=trigger,
        )
        client = GA4Client(source)
        rows = await client.run_report(
            start_date=start_date,
            end_date=end_date,
            channel_breakdown=False,
        )
        rows += await client.run_report(
            start_date=start_date,
            end_date=end_date,
            channel_breakdown=True,
        )
        landing_rows = await client.run_report(
            start_date=start_date,
            end_date=end_date,
            landing_page_breakdown=True,
        )
        if rows and not landing_rows:
            raise GA4ClientError(
                "GA4 landing report is empty while overview has data"
            )
        range_params = {
            "site_id": site_id,
            "start_date": _date.fromisoformat(start_date),
            "end_date": _date.fromisoformat(end_date),
        }
        await session.execute(
            text(
                """
                DELETE FROM seo_agent.ga4_session_daily
                 WHERE site_id = CAST(:site_id AS uuid)
                   AND date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)
                """
            ),
            range_params,
        )
        await session.execute(
            text(
                """
                DELETE FROM seo_agent.ga4_landing_page_daily
                 WHERE site_id = CAST(:site_id AS uuid)
                   AND date BETWEEN CAST(:start_date AS date) AND CAST(:end_date AS date)
                """
            ),
            range_params,
        )
        written = 0
        for r in rows:
            d_raw = r.get("date", "")
            d_parsed = _date.fromisoformat(d_raw) if isinstance(d_raw, str) and len(d_raw) == 10 else (
                _date.fromisoformat(d_raw) if isinstance(d_raw, str) else d_raw
            )
            await session.execute(
                text(
                    """
                    INSERT INTO seo_agent.ga4_session_daily
                      (site_id, date, channel, sessions, total_users, new_users, pageviews,
                       engaged_sessions, engagement_rate, avg_session_duration, bounce_rate,
                       conversions, revenue, synced_at)
                    VALUES
                      (:site_id, :date, :channel, :sessions, :total_users, :new_users, :pageviews,
                       :engaged_sessions, :engagement_rate, :avg_session_duration, :bounce_rate,
                       :conversions, :revenue, now())
                    ON CONFLICT (site_id, date, channel)
                    DO UPDATE SET
                      sessions = EXCLUDED.sessions,
                      total_users = EXCLUDED.total_users,
                      new_users = EXCLUDED.new_users,
                      pageviews = EXCLUDED.pageviews,
                      engaged_sessions = EXCLUDED.engaged_sessions,
                      engagement_rate = EXCLUDED.engagement_rate,
                      avg_session_duration = EXCLUDED.avg_session_duration,
                      bounce_rate = EXCLUDED.bounce_rate,
                      conversions = EXCLUDED.conversions,
                      revenue = EXCLUDED.revenue,
                      synced_at = now()
                    """
                ),
                {
                    "site_id": site_id,
                    "date": d_parsed,
                    "channel": r.get("channel", "all"),
                    "sessions": int(r.get("sessions", 0) or 0),
                    "total_users": int(r.get("total_users", 0) or 0),
                    "new_users": int(r.get("new_users", 0) or 0),
                    "pageviews": int(r.get("pageviews", 0) or 0),
                    "engaged_sessions": int(r.get("engaged_sessions", 0) or 0),
                    "engagement_rate": float(r.get("engagement_rate", 0) or 0),
                    "avg_session_duration": float(
                        r.get("avg_session_duration", 0) or 0
                    ),
                    "bounce_rate": float(r.get("bounce_rate", 0) or 0),
                    "conversions": float(r.get("conversions", 0) or 0),
                    "revenue": float(r.get("revenue", 0) or 0),
                },
            )
            written += 1
        landing_written = 0
        for r in landing_rows:
            landing_page = str(r.get("landing_page", "") or "").strip()
            if not landing_page:
                continue
            d_raw = r.get("date", "")
            d_parsed = _date.fromisoformat(d_raw) if isinstance(d_raw, str) and len(d_raw) == 10 else (
                _date.fromisoformat(d_raw) if isinstance(d_raw, str) else d_raw
            )
            await session.execute(
                text(
                    """
                    INSERT INTO seo_agent.ga4_landing_page_daily
                      (site_id, date, landing_page, sessions, total_users, pageviews,
                       engaged_sessions, engagement_rate, avg_session_duration, bounce_rate,
                       conversions, revenue, synced_at)
                    VALUES
                      (:site_id, :date, :landing_page, :sessions, :total_users, :pageviews,
                       :engaged_sessions, :engagement_rate, :avg_session_duration, :bounce_rate,
                       :conversions, :revenue, now())
                    ON CONFLICT (site_id, date, landing_page)
                    DO UPDATE SET
                      sessions = EXCLUDED.sessions,
                      total_users = EXCLUDED.total_users,
                      pageviews = EXCLUDED.pageviews,
                      engaged_sessions = EXCLUDED.engaged_sessions,
                      engagement_rate = EXCLUDED.engagement_rate,
                      avg_session_duration = EXCLUDED.avg_session_duration,
                      bounce_rate = EXCLUDED.bounce_rate,
                      conversions = EXCLUDED.conversions,
                      revenue = EXCLUDED.revenue,
                      synced_at = now()
                    """
                ),
                {
                    "site_id": site_id,
                    "date": d_parsed,
                    "landing_page": landing_page,
                    "sessions": int(r.get("sessions", 0) or 0),
                    "total_users": int(r.get("total_users", 0) or 0),
                    "pageviews": int(r.get("pageviews", 0) or 0),
                    "engaged_sessions": int(r.get("engaged_sessions", 0) or 0),
                    "engagement_rate": float(r.get("engagement_rate", 0) or 0),
                    "avg_session_duration": float(r.get("avg_session_duration", 0) or 0),
                    "bounce_rate": float(r.get("bounce_rate", 0) or 0),
                    "conversions": float(r.get("conversions", 0) or 0),
                    "revenue": float(r.get("revenue", 0) or 0),
                },
            )
            landing_written += 1
        await session.commit()
        total_fetched = len(rows) + len(landing_rows)
        total_written = written + landing_written
        await _log_done(
            session,
            log_id,
            rows_fetched=total_fetched,
            rows_written=total_written,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        return {
            "ok": True,
            "type": "ga4",
            "rows_fetched": total_fetched,
            "rows_written": total_written,
            "landing_pages_written": landing_written,
            "hostname_scope": list(source.ga4_hosts()),
            "warnings": [],
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as e:  # noqa: BLE001
        await session.rollback()
        if log_id is not None:
            await _log_done(
                session,
                log_id,
                rows_fetched=0,
                rows_written=0,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                error=str(e)[:1000],
            )
        return {
            "ok": False,
            "type": "ga4",
            "error": str(e)[:500],
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }


# ---------- 顶层入口 ----------

def _resolve_range(source: GoogleSource, days_back: int | None) -> tuple[str, str]:
    today = date.today()
    end = today - timedelta(days=1)  # 昨天（今天数据还没完整）
    if days_back is None:
        if source.default_start_date and source.default_end_date:
            return source.default_start_date, source.default_end_date
        days_back = 30
    start = end - timedelta(days=days_back - 1)
    return start.isoformat(), end.isoformat()


async def sync_source(
    session: AsyncSession,
    source_id: str,
    *,
    days_back: int | None = None,
    trigger: str = "manual",
    skip_gsc: bool = False,
    skip_ga4: bool = False,
) -> dict[str, Any]:
    """按 source.id 同步 GSC + GA4。"""
    if trigger not in GOOGLE_SYNC_TRIGGERS:
        raise ValueError(f"unsupported google sync trigger: {trigger}")
    source = get_store().get_by_id(source_id)
    if not source:
        return {"ok": False, "error": f"source not found: {source_id}"}
    site_id = await _resolve_site_id_by_domain(session, source)
    if not site_id:
        return {
            "ok": False,
            "error": f"no seo_agent.sites row matches domain {source.gsc_host()}",
            "source_id": source_id,
            "domain": source.gsc_host(),
        }
    start_date, end_date = _resolve_range(source, days_back)
    results: list[dict[str, Any]] = []
    if not skip_gsc:
        results.append(
            await _run_isolated_source(
                "gsc",
                _sync_gsc,
                session,
                source,
                site_id,
                start_date=start_date,
                end_date=end_date,
                trigger=trigger,
            )
        )
    if not skip_ga4:
        results.append(
            await _run_isolated_source(
                "ga4",
                _sync_ga4,
                session,
                source,
                site_id,
                start_date=start_date,
                end_date=end_date,
                trigger=trigger,
            )
        )
    return {
        "ok": all(r.get("ok") for r in results),
        "source_id": source_id,
        "site_id": site_id,
        "domain": source.gsc_host(),
        "range": {"start": start_date, "end": end_date},
        "results": results,
    }


async def _run_isolated_source(
    source_type: str,
    function: Any,
    session: AsyncSession,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Keep one failed source from poisoning the shared PostgreSQL session."""
    bind = getattr(session, "bind", None)
    if isinstance(session, AsyncSession) and bind is not None:
        factory = async_sessionmaker(bind, expire_on_commit=False)
        async with factory() as isolated_session:
            try:
                return await function(isolated_session, *args, **kwargs)
            except Exception as error:
                await isolated_session.rollback()
                return {
                    "ok": False,
                    "type": source_type,
                    "error": str(error)[:500],
                }
    try:
        return await function(session, *args, **kwargs)
    except Exception as error:  # defensive boundary around log/start/finalize failures
        await session.rollback()
        return {
            "ok": False,
            "type": source_type,
            "error": str(error)[:500],
        }


async def sync_all_sources(
    session: AsyncSession,
    *,
    days_back: int | None = None,
    trigger: str = "scheduled",
) -> list[dict[str, Any]]:
    """遍历全部 source 同步。"""
    out: list[dict[str, Any]] = []
    for source in get_store().list_all():
        out.append(
            await sync_source(
                session,
                source.id,
                days_back=days_back,
                trigger=trigger,
            )
        )
    return out
