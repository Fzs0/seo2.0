"""文章服务：保存 / 列出 / 按 keyword 取。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def save_article(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """插入或更新一篇文章；按 task_id 或 (site_id, external_id) 判重。"""
    task_id = payload.get("task_id") or payload.get("taskId")
    site_id = payload.get("site_id") or payload.get("siteId")
    external_id = payload.get("external_id") or payload.get("externalId")
    keyword_id = payload.get("keyword_id") or payload.get("keywordId")
    serp_snapshot_id = payload.get("serp_snapshot_id") or payload.get("serpSnapshotId")

    sql = text(
        """
        INSERT INTO seo_agent.articles
          (task_id, site_id, keyword_id, serp_snapshot_id, title, slug, target_url,
           status, language_code, market, brief_md, prompt_text, content_md, content_html,
           article_parts, meta_title, meta_description, primary_keyword, secondary_keywords,
           internal_link_plan, image_plan, references_plan, qa_checklist,
           generation_provider, generation_model, raw_ai_response)
        VALUES
          (CAST(:task_id AS uuid), CAST(:site_id AS uuid), CAST(:keyword_id AS uuid),
           CAST(:serp_snapshot_id AS uuid),
           :title, :slug, :target_url,
           :status, :language_code, :market,
           :brief_md, :prompt_text, :content_md, :content_html,
           CAST(:article_parts AS jsonb), :meta_title, :meta_description,
           :primary_keyword, CAST(:secondary_keywords AS text[]),
           CAST(:internal_link_plan AS jsonb), CAST(:image_plan AS jsonb),
           CAST(:references_plan AS jsonb), CAST(:qa_checklist AS jsonb),
           :generation_provider, :generation_model, CAST(:raw_ai_response AS jsonb))
        ON CONFLICT (task_id) WHERE task_id IS NOT NULL
        DO UPDATE SET
          title = EXCLUDED.title,
          slug = EXCLUDED.slug,
          serp_snapshot_id = EXCLUDED.serp_snapshot_id,
          brief_md = EXCLUDED.brief_md,
          prompt_text = EXCLUDED.prompt_text,
          content_md = EXCLUDED.content_md,
          article_parts = EXCLUDED.article_parts,
          meta_title = EXCLUDED.meta_title,
          meta_description = EXCLUDED.meta_description,
          internal_link_plan = EXCLUDED.internal_link_plan,
          image_plan = EXCLUDED.image_plan,
          references_plan = EXCLUDED.references_plan,
          qa_checklist = EXCLUDED.qa_checklist,
          generation_provider = EXCLUDED.generation_provider,
          generation_model = EXCLUDED.generation_model,
          raw_ai_response = EXCLUDED.raw_ai_response,
          status = EXCLUDED.status,
          updated_at = now()
        RETURNING id, site_id, title, slug, status, language_code, market,
                  meta_title, meta_description, primary_keyword, created_at
        """
    )
    # 注意：v1 articles 表在 task_id 上有 FK，但 ON CONFLICT 用 task_id 风险大
    # 这里只支持纯 INSERT（task_id 留空时走默认 ON CONFLICT DO NOTHING）
    # 为了安全：当 task_id 缺失时改用 INSERT ... ON CONFLICT DO NOTHING
    sql_no_task = text(
        """
        INSERT INTO seo_agent.articles
          (task_id, site_id, keyword_id, serp_snapshot_id, title, slug, target_url,
           status, language_code, market, brief_md, prompt_text, content_md, content_html,
           article_parts, meta_title, meta_description, primary_keyword, secondary_keywords,
           internal_link_plan, image_plan, references_plan, qa_checklist,
           generation_provider, generation_model, raw_ai_response)
        VALUES
          (CAST(:task_id AS uuid), CAST(:site_id AS uuid), CAST(:keyword_id AS uuid),
           CAST(:serp_snapshot_id AS uuid),
           :title, :slug, :target_url,
           :status, :language_code, :market,
           :brief_md, :prompt_text, :content_md, :content_html,
           CAST(:article_parts AS jsonb), :meta_title, :meta_description,
           :primary_keyword, CAST(:secondary_keywords AS text[]),
           CAST(:internal_link_plan AS jsonb), CAST(:image_plan AS jsonb),
           CAST(:references_plan AS jsonb), CAST(:qa_checklist AS jsonb),
           :generation_provider, :generation_model, CAST(:raw_ai_response AS jsonb))
        ON CONFLICT DO NOTHING
        RETURNING id, site_id, title, slug, status, language_code, market,
                  meta_title, meta_description, primary_keyword, created_at
        """
    )
    params = {
        "task_id": task_id,
        "site_id": site_id,
        "keyword_id": keyword_id,
        "serp_snapshot_id": serp_snapshot_id,
        "title": payload.get("title") or "untitled",
        "slug": payload.get("slug"),
        "target_url": payload.get("target_url") or payload.get("targetUrl"),
        "status": payload.get("status") or "draft",
        "language_code": payload.get("language_code") or payload.get("languageCode"),
        "market": payload.get("market"),
        "brief_md": payload.get("brief_md") or payload.get("briefMd"),
        "prompt_text": payload.get("prompt_text") or payload.get("promptText"),
        "content_md": payload.get("content_md") or payload.get("contentMd"),
        "content_html": payload.get("content_html") or payload.get("contentHtml"),
        "article_parts": _to_json(payload.get("article_parts") or payload.get("articleParts") or {}),
        "meta_title": payload.get("meta_title") or payload.get("metaTitle"),
        "meta_description": payload.get("meta_description") or payload.get("metaDescription"),
        "primary_keyword": payload.get("primary_keyword") or payload.get("primaryKeyword"),
        "secondary_keywords": payload.get("secondary_keywords") or payload.get("secondaryKeywords") or [],
        "internal_link_plan": _to_json(payload.get("internal_link_plan") or payload.get("internalLinkPlan") or []),
        "image_plan": _to_json(payload.get("image_plan") or payload.get("imagePlan") or []),
        "references_plan": _to_json(payload.get("references_plan") or payload.get("referencesPlan") or []),
        "qa_checklist": _to_json(payload.get("qa_checklist") or payload.get("qaChecklist") or []),
        "generation_provider": payload.get("generation_provider") or payload.get("generationProvider"),
        "generation_model": payload.get("generation_model") or payload.get("generationModel"),
        "raw_ai_response": _to_json(payload.get("raw_ai_response") or payload.get("rawAiResponse") or {}),
    }
    sql_to_use = sql_no_task if not task_id else sql
    result = await session.execute(sql_to_use, params)
    await session.commit()
    row = result.mappings().first()
    return dict(row) if row else {"saved": True, "duplicate": True}


async def articles_kpi(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    date_field: str = "created_at",
) -> dict[str, Any]:
    """KPI 速查：累计 / 今日 / 本周 / 本月 / 近 7 天 / 近 30 天 / 上月新增。

    date_field: "created_at"（系统生成时间）或 "published_at"（真正发布时间）。
    当选 published_at 时，未发布（IS NULL）的文章不计入。
    """
    from datetime import datetime, timezone, timedelta

    # 白名单：防 SQL 注入
    if date_field not in {"created_at", "published_at"}:
        date_field = "created_at"
    df = f"a.{date_field}"

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())  # 周一为起点
    # 本月 1 号
    month_start = today_start.replace(day=1)
    # 上月 1 号
    if month_start.month == 1:
        prev_month_start = month_start.replace(year=month_start.year - 1, month=12)
    else:
        prev_month_start = month_start.replace(month=month_start.month - 1)
    last_7d = today_start - timedelta(days=7)
    last_30d = today_start - timedelta(days=30)

    where = "WHERE 1=1"
    params: dict[str, Any] = {}
    if site_id:
        where += " AND a.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    # published_at 不能是空
    if date_field == "published_at":
        where += f" AND {df} IS NOT NULL"

    sql = text(
        f"""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE {df} >= :today) AS today,
          count(*) FILTER (WHERE {df} >= :week) AS this_week,
          count(*) FILTER (WHERE {df} >= :month) AS this_month,
          count(*) FILTER (WHERE {df} >= :last7) AS last_7_days,
          count(*) FILTER (WHERE {df} >= :last30) AS last_30_days,
          count(*) FILTER (WHERE {df} >= :prev_month AND {df} < :month) AS last_month
        FROM seo_agent.articles a
        {where}
        """
    )
    # PG timestamp 是 naive，去掉 tzinfo
    sql_params = {
        **params,
        "today": today_start.replace(tzinfo=None),
        "week": week_start.replace(tzinfo=None),
        "month": month_start.replace(tzinfo=None),
        "prev_month": prev_month_start.replace(tzinfo=None),
        "last7": last_7d.replace(tzinfo=None),
        "last30": last_30d.replace(tzinfo=None),
    }
    row = (await session.execute(sql, sql_params)).mappings().first()
    if not row:
        return {
            "total": 0,
            "today": 0,
            "this_week": 0,
            "this_month": 0,
            "last_7_days": 0,
            "last_30_days": 0,
            "last_month": 0,
            "date_field": date_field,
            "generated_at": now.isoformat(),
        }
    return {
        "total": int(row["total"] or 0),
        "today": int(row["today"] or 0),
        "this_week": int(row["this_week"] or 0),
        "this_month": int(row["this_month"] or 0),
        "last_7_days": int(row["last_7_days"] or 0),
        "last_30_days": int(row["last_30_days"] or 0),
        "last_month": int(row["last_month"] or 0),
        "date_field": date_field,
        "generated_at": now.isoformat(),
    }


async def articles_timeseries(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    start: str,  # YYYY-MM-DD
    end: str,    # YYYY-MM-DD (含)
    granularity: str = "auto",  # auto | day | week | month
    date_field: str = "created_at",  # created_at | published_at
) -> dict[str, Any]:
    """按时间桶聚合文章数；返回连续 N 个桶（无数据补 0），可按站点拆分。

    granularity=auto 时：<=62 天用 day；63-365 天用 week；>365 天用 month。
    date_field：created_at（系统生成时间）或 published_at（真正发布时间）。
    选 published_at 时，未发布（IS NULL）的不计入。
    """
    from datetime import datetime, timezone, timedelta, date as _date

    if date_field not in {"created_at", "published_at"}:
        date_field = "created_at"
    df = f"a.{date_field}"

    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    range_days = (end_d - start_d).days + 1

    g = (granularity or "auto").lower()
    if g == "auto":
        g = "day" if range_days <= 62 else "week" if range_days <= 365 else "month"
    if g not in {"day", "week", "month"}:
        g = "month"

    where = f"WHERE {df} >= CAST(:start AS timestamp) AND {df} < CAST(:end_exclusive AS timestamp)"
    end_exclusive = end_d + timedelta(days=1)
    # PG timestamp 是 naive，按 UTC 存；传 naive datetime 让 asyncpg 正确编码
    params: dict[str, Any] = {
        "start": datetime(start_d.year, start_d.month, start_d.day),
        "end_exclusive": datetime(end_exclusive.year, end_exclusive.month, end_exclusive.day),
    }
    if site_id:
        where += " AND a.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    if date_field == "published_at":
        where += f" AND {df} IS NOT NULL"

    # 时间桶键
    if g == "day":
        bucket_expr = f"to_char(date_trunc('day', {df}), 'YYYY-MM-DD')"
    elif g == "week":
        # 周一对齐
        bucket_expr = f"to_char(date_trunc('week', {df}), 'YYYY-MM-DD')"
    else:  # month
        bucket_expr = f"to_char(date_trunc('month', {df}), 'YYYY-MM')"

    sql_total = text(
        f"""
        SELECT count(*) AS n
          FROM seo_agent.articles a
          {where}
        """
    )
    total = (await session.execute(sql_total, params)).scalar_one() or 0

    sql_buckets = text(
        f"""
        SELECT {bucket_expr} AS bucket_key,
               count(*) AS n
          FROM seo_agent.articles a
          {where}
         GROUP BY 1
         ORDER BY 1
        """
    )
    rows = (await session.execute(sql_buckets, params)).mappings().all()
    by_key: dict[str, int] = {str(r["bucket_key"]): int(r["n"] or 0) for r in rows}

    # 补全连续桶
    buckets_out: list[dict[str, Any]] = []
    if g == "day":
        cursor = start_d
        while cursor <= end_d:
            key = cursor.isoformat()
            buckets_out.append({"key": key, "label": key, "count": by_key.get(key, 0)})
            cursor += timedelta(days=1)
    elif g == "week":
        # 对齐到周一开始
        cursor = start_d - timedelta(days=start_d.weekday())
        while cursor <= end_d:
            key = cursor.isoformat()
            buckets_out.append({"key": key, "label": f"W {cursor.isoformat()}", "count": by_key.get(key, 0)})
            cursor += timedelta(days=7)
    else:  # month
        cursor = start_d.replace(day=1)
        last = end_d.replace(day=1)
        while cursor <= last:
            key = f"{cursor.year:04d}-{cursor.month:02d}"
            buckets_out.append({"key": key, "label": key, "count": by_key.get(key, 0)})
            # 下个月
            cursor = (cursor.replace(day=28) + timedelta(days=10)).replace(day=1)

    # 按站点聚合（同一个时间桶内）
    bucket_label_lookup = {b["key"]: b["label"] for b in buckets_out}
    if g == "day":
        bucket_label_lookup = {b["key"]: b["key"] for b in buckets_out}
    elif g == "week":
        bucket_label_lookup = {b["key"]: f"W {b['key']}" for b in buckets_out}
    else:
        bucket_label_lookup = {b["key"]: b["key"] for b in buckets_out}

    sql_by_site = text(
        f"""
        SELECT a.site_id,
               COALESCE(s.name, '未分配站点') AS site_name,
               {bucket_expr} AS bucket_key,
               count(*) AS n
          FROM seo_agent.articles a
          LEFT JOIN seo_agent.sites s ON s.id = a.site_id
          {where}
         GROUP BY a.site_id, s.name, 3
         ORDER BY s.name NULLS LAST
        """
    )
    site_rows = (await session.execute(sql_by_site, params)).mappings().all()
    site_buckets: dict[str, dict[str, Any]] = {}
    for r in site_rows:
        sid = str(r["site_id"]) if r["site_id"] else "__unassigned__"
        bucket_key = str(r["bucket_key"])
        if bucket_key not in bucket_label_lookup:
            continue  # 范围外的不计
        bucket = site_buckets.setdefault(
            sid,
            {"site_id": sid if sid != "__unassigned__" else None, "site_name": str(r["site_name"]), "data": {}},
        )
        bucket["data"][bucket_key] = int(r["n"] or 0)

    by_site_out: list[dict[str, Any]] = []
    for bucket in site_buckets.values():
        series = [
            {"key": b["key"], "label": b["label"], "count": bucket["data"].get(b["key"], 0)}
            for b in buckets_out
        ]
        by_site_out.append({
            "site_id": bucket["site_id"],
            "site_name": bucket["site_name"],
            "series": series,
            "total": sum(s["count"] for s in series),
        })

    return {
        "granularity": g,
        "range": {
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "days": range_days,
        },
        "buckets": buckets_out,
        "by_site": by_site_out,
        "total": int(total),
        "date_field": date_field,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def articles_monthly_stats(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    months: int = 12,
) -> dict[str, Any]:
    """按 created_at 月份聚合文章数；返回连续 N 个月的月度计数（无数据的月份补 0）。

    输出格式：
    {
      months: ["2026-07", "2026-06", ...],          # 从本月往前推 N-1 个月，最新在前
      totals: { "2026-07": 3, "2026-06": 5, ... },
      by_site: [                                    # 每个站点的月度计数
        { site_id: "uuid", site_name: "...", by_month: { "2026-07": 1, "2026-06": 2, ... } }
      ],
      total_articles: 42,
      generated_at: "2026-07-20T10:00:00+00:00",
    }
    """
    from datetime import datetime, timezone
    from calendar import monthrange

    months = max(1, min(int(months or 12), 36))
    today = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 构造 months 列表（最新在前）
    month_keys: list[str] = []
    cursor = today
    for _ in range(months):
        month_keys.append(f"{cursor.year:04d}-{cursor.month:02d}")
        # 上一个月
        prev_month = cursor.month - 1 or 12
        prev_year = cursor.year if cursor.month > 1 else cursor.year - 1
        cursor = cursor.replace(year=prev_year, month=prev_month)

    # 取该范围的下界（最早月份的 1 号），用 date 对象让 asyncpg 正确编码
    oldest_year, oldest_month = (int(month_keys[-1].split("-")[0]), int(month_keys[-1].split("-")[1]))
    range_start = datetime(oldest_year, oldest_month, 1, tzinfo=timezone.utc)

    where = "WHERE a.created_at >= :range_start"
    params: dict[str, Any] = {"range_start": range_start}
    if site_id:
        where += " AND a.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id

    # 总数 + 月度（按月聚合）
    total = (
        await session.execute(text(f"SELECT count(*) AS n FROM seo_agent.articles a {where}"), params)
    ).scalar_one() or 0

    monthly_sql = text(
        f"""
        SELECT to_char(date_trunc('month', a.created_at), 'YYYY-MM') AS month_key,
               count(*) AS n
          FROM seo_agent.articles a
          {where}
         GROUP BY 1
         ORDER BY 1 DESC
        """
    )
    monthly_rows = (await session.execute(monthly_sql, params)).mappings().all()
    totals: dict[str, int] = {key: 0 for key in month_keys}
    for row in monthly_rows:
        key = str(row["month_key"])
        if key in totals:
            totals[key] = int(row["n"] or 0)

    # 按站点 + 月聚合
    by_site_sql = text(
        f"""
        SELECT a.site_id,
               COALESCE(s.name, '未分配站点') AS site_name,
               to_char(date_trunc('month', a.created_at), 'YYYY-MM') AS month_key,
               count(*) AS n
          FROM seo_agent.articles a
          LEFT JOIN seo_agent.sites s ON s.id = a.site_id
          {where}
         GROUP BY a.site_id, s.name, 3
         ORDER BY s.name NULLS LAST
        """
    )
    site_rows = (await session.execute(by_site_sql, params)).mappings().all()
    site_buckets: dict[str, dict[str, Any]] = {}
    for row in site_rows:
        sid = str(row["site_id"]) if row["site_id"] else "__unassigned__"
        bucket = site_buckets.setdefault(
            sid,
            {"site_id": sid if sid != "__unassigned__" else None, "site_name": str(row["site_name"]), "by_month": {key: 0 for key in month_keys}},
        )
        key = str(row["month_key"])
        if key in bucket["by_month"]:
            bucket["by_month"][key] = int(row["n"] or 0)

    return {
        "months": month_keys,
        "totals": totals,
        "by_site": list(site_buckets.values()),
        "total_articles": int(total),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "months_window": months,
    }


async def list_articles(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    keyword_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    where = "WHERE 1=1"
    params: dict[str, Any] = {"limit": max(1, min(limit, 200)), "offset": max(0, offset)}
    if site_id:
        where += " AND a.site_id = :site_id"
        params["site_id"] = site_id
    if keyword_id:
        where += " AND a.keyword_id = :keyword_id"
        params["keyword_id"] = keyword_id
    if status:
        where += " AND a.status = :status"
        params["status"] = status
    total = (
        await session.execute(
            text(f"SELECT count(*) AS n FROM seo_agent.articles a {where}"),
            params,
        )
    ).scalar_one()
    result = await session.execute(
        text(
            f"""
            SELECT a.id, a.task_id, a.site_id, s.name AS site_label,
                   a.keyword_id, k.keyword, a.title, a.slug, a.target_url, a.status,
                   a.language_code, a.market, a.meta_title, a.meta_description,
                   a.primary_keyword, a.generation_provider, a.generation_model,
                   a.created_at, a.updated_at, a.published_at, a.published_url
              FROM seo_agent.articles a
              LEFT JOIN seo_agent.sites s ON s.id = a.site_id
              LEFT JOIN seo_agent.keywords k ON k.id = a.keyword_id
              {where}
             ORDER BY a.created_at DESC
             LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {
        "items": [dict(r) for r in result.mappings().all()],
        "total": int(total or 0),
        "limit": params["limit"],
        "offset": params["offset"],
    }


async def get_article(session: AsyncSession, article_id: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, task_id, site_id, keyword_id, serp_snapshot_id, title, slug, target_url, "
            "status, language_code, market, brief_md, prompt_text, content_md, content_html, "
            "article_parts, meta_title, meta_description, primary_keyword, secondary_keywords, "
            "internal_link_plan, image_plan, references_plan, qa_checklist, "
            "generation_provider, generation_model, published_url, published_at, "
            "created_at, updated_at "
            "FROM seo_agent.articles WHERE id = :id"
        ),
        {"id": article_id},
    )
    row = result.mappings().first()
    if not row:
        return None
    d = dict(row)
    # jsonb 字段直接转 Python 对象
    for k in ("article_parts", "internal_link_plan", "image_plan", "references_plan", "qa_checklist"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except (ValueError, TypeError):
                pass
    return d


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
