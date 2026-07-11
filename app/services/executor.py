"""PostgreSQL 写入执行器。UnitOfWork 模式。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_sites(session: AsyncSession, sites: list[dict[str, Any]]) -> int:
    """最小化的 sites upsert；本轮仅在 sync-workspace 路径使用。"""
    if not sites:
        return 0
    rows = [
        {
            "site_key": s.get("site_key") or s.get("siteKey") or s.get("name"),
            "name": s.get("name"),
            "site_type": s.get("site_type") or s.get("siteType") or "other",
            "domain": s.get("domain"),
            "base_url": s.get("base_url") or s.get("baseUrl"),
            "api_base_url": s.get("api_base_url") or s.get("apiBaseUrl"),
            "market": s.get("market"),
            "language_code": s.get("language_code") or s.get("languageCode"),
            "google_gl": s.get("google_gl") or s.get("googleGl"),
            "google_hl": s.get("google_hl") or s.get("googleHl"),
            "semrush_database": s.get("semrush_database") or s.get("semrushDatabase"),
            "content_role": s.get("content_role") or s.get("contentRole"),
            "is_main": bool(s.get("is_main", s.get("isMain", False))),
            "allow_external_links": bool(s.get("allow_external_links", s.get("allowExternalLinks", False))),
            "publish_config": s.get("publish_config") or s.get("publishConfig") or {},
            "api_config": s.get("api_config") or s.get("apiConfig") or {},
            "status": s.get("status") or "active",
            "notes": s.get("notes"),
            "raw": s.get("raw") or {},
        }
        for s in sites
    ]
    sql = text(
        """
        INSERT INTO seo_agent.sites
          (site_key, name, site_type, domain, base_url, api_base_url, market, language_code,
           google_gl, google_hl, semrush_database, content_role, is_main, allow_external_links,
           publish_config, api_config, status, notes, raw)
        VALUES
          (:site_key, :name, :site_type, :domain, :base_url, :api_base_url, :market, :language_code,
           :google_gl, :google_hl, :semrush_database, :content_role, :is_main, :allow_external_links,
           CAST(:publish_config AS jsonb), CAST(:api_config AS jsonb), :status, :notes, CAST(:raw AS jsonb))
        ON CONFLICT (site_key) DO UPDATE SET
          name = EXCLUDED.name,
          site_type = EXCLUDED.site_type,
          domain = EXCLUDED.domain,
          base_url = EXCLUDED.base_url,
          api_base_url = EXCLUDED.api_base_url,
          market = EXCLUDED.market,
          language_code = EXCLUDED.language_code,
          google_gl = EXCLUDED.google_gl,
          google_hl = EXCLUDED.google_hl,
          semrush_database = EXCLUDED.semrush_database,
          content_role = EXCLUDED.content_role,
          is_main = EXCLUDED.is_main,
          allow_external_links = EXCLUDED.allow_external_links,
          publish_config = EXCLUDED.publish_config,
          api_config = EXCLUDED.api_config,
          status = EXCLUDED.status,
          notes = EXCLUDED.notes,
          raw = EXCLUDED.raw
        """
    )
    await session.execute(sql, rows)
    return len(rows)


async def upsert_keywords(session: AsyncSession, keywords: list[dict[str, Any]]) -> int:
    if not keywords:
        return 0
    rows = [
        {
            "keyword": k.get("keyword"),
            "source": k.get("source") or "semrush",
            "source_file": k.get("source_file") or k.get("sourceFile"),
            "semrush_database": k.get("database"),
            "market": k.get("market"),
            "language_code": k.get("language_code") or k.get("languageCode"),
            "google_gl": k.get("google_gl") or k.get("googleGl"),
            "google_hl": k.get("google_hl") or k.get("googleHl"),
            "volume": int(k.get("volume") or 0),
            "kd": float(k.get("kd") or 0),
            "cpc": float(k.get("cpc") or 0),
            "intent": k.get("intent"),
            "topic_cluster": k.get("topicCluster") or k.get("topic_cluster"),
            "seed_keyword": k.get("seedKeyword") or k.get("seed_keyword"),
            "page_group": k.get("pageGroup") or k.get("page_group"),
            "page_type": k.get("pageType") or k.get("page_type"),
            "page_role": k.get("pageRole") or k.get("page_role"),
            "assigned_site_label": k.get("assignedSite") or k.get("assigned_site_label"),
            "target_asset_url": k.get("targetAsset") or k.get("target_asset_url"),
            "asset_status": k.get("assetStatus") or k.get("asset_status"),
            "content_action": k.get("contentAction") or k.get("content_action"),
            "priority": k.get("priority"),
            "score": float((k.get("scores") or {}).get("total") or k.get("score") or 0),
            "status": k.get("status") or "analyzed",
            "reason": k.get("reason"),
            "raw": json.dumps(k.get("raw") or {}, ensure_ascii=False),
        }
        for k in keywords
    ]
    sql = text(
        """
        INSERT INTO seo_agent.keywords
          (keyword, source, source_file, semrush_database, market, language_code, google_gl, google_hl,
           volume, kd, cpc, intent, topic_cluster, seed_keyword, page_group, page_type, page_role,
           assigned_site_label, target_asset_url, asset_status, content_action, priority, score, status, reason, raw)
        VALUES
          (:keyword, :source, :source_file, :semrush_database, :market, :language_code, :google_gl, :google_hl,
           :volume, :kd, :cpc, :intent, :topic_cluster, :seed_keyword, :page_group, :page_type, :page_role,
           :assigned_site_label, :target_asset_url, :asset_status, :content_action, :priority, :score, :status, :reason, CAST(:raw AS jsonb))
        ON CONFLICT (normalized_keyword, COALESCE(semrush_database, ''), COALESCE(market, ''), COALESCE(language_code, ''))
        DO UPDATE SET
          volume = EXCLUDED.volume,
          kd = EXCLUDED.kd,
          cpc = EXCLUDED.cpc,
          intent = EXCLUDED.intent,
          topic_cluster = EXCLUDED.topic_cluster,
          seed_keyword = EXCLUDED.seed_keyword,
          page_group = EXCLUDED.page_group,
          page_type = EXCLUDED.page_type,
          page_role = EXCLUDED.page_role,
          assigned_site_label = EXCLUDED.assigned_site_label,
          target_asset_url = EXCLUDED.target_asset_url,
          asset_status = EXCLUDED.asset_status,
          content_action = EXCLUDED.content_action,
          priority = EXCLUDED.priority,
          score = EXCLUDED.score,
          status = EXCLUDED.status,
          reason = EXCLUDED.reason,
          raw = EXCLUDED.raw
        """
    )
    await session.execute(sql, rows)
    return len(rows)
