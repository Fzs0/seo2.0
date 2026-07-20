"""PostgreSQL 写入执行器。UnitOfWork 模式。"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _json_text(value: Any, default: Any) -> str:
    if value in (None, ""):
        value = default
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            value = [part.strip() for part in re.split(r"[,;|]", value) if part.strip()]
    return json.dumps(value, ensure_ascii=False, default=str)



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
    rows = []
    for keyword in keywords:
        rows.append(
            {
                "keyword": keyword.get("keyword"),
                "source": keyword.get("source") or "semrush",
                "source_file": keyword.get("source_file") or keyword.get("sourceFile"),
                "semrush_database": keyword.get("database"),
                "market": keyword.get("market"),
                "language_code": keyword.get("language_code") or keyword.get("languageCode"),
                "google_gl": keyword.get("google_gl") or keyword.get("googleGl"),
                "google_hl": keyword.get("google_hl") or keyword.get("googleHl"),
                "volume": int(keyword.get("volume") or 0),
                "kd": float(keyword.get("kd") or 0),
                "cpc": float(keyword.get("cpc") or 0),
                "intent": keyword.get("intent"),
                "serp_features": _json_text(keyword.get("serpFeatures") or keyword.get("serp_features"), []),
                "trend": float(keyword.get("trend") or 0),
                "trend_data": _json_text(keyword.get("trendData") or keyword.get("trend_data"), []),
                "pkd": float(keyword.get("pkd") or 0),
                "potential_traffic": float(keyword.get("potentialTraffic") or keyword.get("potential_traffic") or 0),
                "competitive_density": float(keyword.get("competitiveDensity") or keyword.get("competitive_density") or 0),
                "serp_results": int(keyword.get("serpResults") or keyword.get("serp_results") or 0),
                "keyword_type": keyword.get("keywordType") or keyword.get("keyword_type"),
                "preflight_status": keyword.get("preflightStatus") or keyword.get("preflight_status") or "ready",
                "preflight_reason": keyword.get("preflightReason") or keyword.get("preflight_reason"),
                "business_id": keyword.get("businessId") or keyword.get("business_id"),
                "source_batch_id": keyword.get("sourceBatchId") or keyword.get("source_batch_id"),
                "topic_cluster": keyword.get("topicCluster") or keyword.get("topic_cluster"),
                "seed_keyword": keyword.get("seedKeyword") or keyword.get("seed_keyword"),
                "page_group": keyword.get("pageGroup") or keyword.get("page_group"),
                "topic_cluster_id": keyword.get("topicClusterId") or keyword.get("topic_cluster_id"),
                "cluster_role": keyword.get("clusterRole") or keyword.get("cluster_role"),
                "cluster_size": keyword.get("clusterSize") or keyword.get("cluster_size"),
                "pillar_keyword": keyword.get("pillarKeyword") or keyword.get("pillar_keyword"),
                "page_type": keyword.get("pageType") or keyword.get("page_type"),
                "page_role": keyword.get("pageRole") or keyword.get("page_role"),
                "assigned_site_id": None,
                "assigned_site_label": None,
                "target_asset_url": keyword.get("targetAsset") or keyword.get("target_asset_url"),
                "asset_status": keyword.get("assetStatus") or keyword.get("asset_status"),
                "content_action": keyword.get("contentAction") or keyword.get("content_action"),
                "priority": keyword.get("priority"),
                "score": float((keyword.get("scores") or {}).get("total") or keyword.get("score") or 0),
                "status": keyword.get("status") if keyword.get("status") in {"imported", "hold", "dropped"} else "imported",
                "reason": keyword.get("reason"),
                "raw": json.dumps(keyword.get("raw") or {}, ensure_ascii=False),
            }
        )
    sql = text(
        """
        INSERT INTO seo_agent.keywords
          (keyword, source, source_file, semrush_database, market, language_code, google_gl, google_hl,
           volume, kd, cpc, intent, serp_features, trend, trend_data, pkd, potential_traffic, competitive_density, serp_results,
           keyword_type, preflight_status, preflight_reason, business_id, source_batch_id,
           topic_cluster, topic_cluster_id, cluster_role, cluster_size, pillar_keyword, seed_keyword, page_group, page_type, page_role,
           assigned_site_id, assigned_site_label, target_asset_url, asset_status, content_action, priority, score, status, reason, raw)
        VALUES
          (:keyword, :source, :source_file, :semrush_database, :market, :language_code, :google_gl, :google_hl,
          :volume, :kd, :cpc, :intent, CAST(:serp_features AS jsonb), :trend, CAST(:trend_data AS jsonb), :pkd, :potential_traffic, :competitive_density, :serp_results,
          :keyword_type, :preflight_status, :preflight_reason, :business_id, :source_batch_id,
          :topic_cluster, :topic_cluster_id, :cluster_role, :cluster_size, :pillar_keyword, :seed_keyword, :page_group, :page_type, :page_role,
           CAST(:assigned_site_id AS uuid), :assigned_site_label, :target_asset_url, :asset_status, :content_action, :priority, :score, :status, :reason, CAST(:raw AS jsonb))
        ON CONFLICT (COALESCE(business_id, ''), normalized_keyword, COALESCE(semrush_database, ''), COALESCE(market, ''), COALESCE(language_code, ''))
        DO UPDATE SET
          source = EXCLUDED.source,
          source_file = EXCLUDED.source_file,
          volume = EXCLUDED.volume,
          kd = EXCLUDED.kd,
          cpc = EXCLUDED.cpc,
          intent = EXCLUDED.intent,
          serp_features = EXCLUDED.serp_features,
          trend = EXCLUDED.trend,
          trend_data = EXCLUDED.trend_data,
          pkd = EXCLUDED.pkd,
          potential_traffic = EXCLUDED.potential_traffic,
          competitive_density = EXCLUDED.competitive_density,
          serp_results = EXCLUDED.serp_results,
          keyword_type = EXCLUDED.keyword_type,
          preflight_status = EXCLUDED.preflight_status,
          preflight_reason = EXCLUDED.preflight_reason,
          business_id = EXCLUDED.business_id,
          source_batch_id = EXCLUDED.source_batch_id,
          topic_cluster = EXCLUDED.topic_cluster,
          topic_cluster_id = EXCLUDED.topic_cluster_id,
          cluster_role = EXCLUDED.cluster_role,
          cluster_size = EXCLUDED.cluster_size,
          pillar_keyword = EXCLUDED.pillar_keyword,
          seed_keyword = EXCLUDED.seed_keyword,
          page_group = EXCLUDED.page_group,
          page_type = EXCLUDED.page_type,
          page_role = EXCLUDED.page_role,
          assigned_site_id = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.assigned_site_id ELSE NULL END,
          assigned_site_label = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.assigned_site_label ELSE NULL END,
          target_asset_url = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.target_asset_url ELSE EXCLUDED.target_asset_url END,
          asset_status = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.asset_status ELSE EXCLUDED.asset_status END,
          content_action = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.content_action ELSE EXCLUDED.content_action END,
          priority = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.priority ELSE EXCLUDED.priority END,
          score = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.score ELSE EXCLUDED.score END,
          status = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.status ELSE 'imported' END,
          reason = CASE WHEN seo_agent.keywords.ai_review ? 'strategy' THEN seo_agent.keywords.reason ELSE EXCLUDED.reason END,
          raw = EXCLUDED.raw
        """
    )
    await session.execute(sql, rows)
    return len(rows)
