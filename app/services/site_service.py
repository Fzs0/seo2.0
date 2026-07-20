"""?????CRUD + ??? market/language ???????"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SUPPORTED_SITE_TYPES = ("main", "wp", "blog", "shopify", "other")


async def list_sites(
    session: AsyncSession,
    *,
    market: str | None = None,
    language_code: str | None = None,
    site_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """????????"""
    sql = "SELECT id, site_key, name, site_type, domain, base_url, api_base_url, " \
          "market, language_code, google_gl, google_hl, semrush_database, " \
          "content_role, content_scope, business_id, strategy_enabled, is_main, allow_external_links, " \
          "status, notes, api_config, knowledge_profile, created_at, updated_at " \
          "FROM seo_agent.sites WHERE 1=1"
    params: dict[str, Any] = {}
    if market:
        sql += " AND market = :market"
        params["market"] = market
    if language_code:
        sql += " AND language_code = :language_code"
        params["language_code"] = language_code
    if site_type:
        sql += " AND site_type = :site_type"
        params["site_type"] = site_type
    sql += " ORDER BY is_main DESC, name ASC LIMIT :limit"
    params["limit"] = max(1, min(limit, 500))
    result = await session.execute(text(sql), params)
    return [_attach_publish_state(dict(r)) for r in result.mappings().all()]


async def get_site(session: AsyncSession, site_id: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, site_key, name, site_type, domain, base_url, api_base_url, "
            "market, language_code, google_gl, google_hl, semrush_database, "
            "content_role, content_scope, business_id, strategy_enabled, is_main, allow_external_links, "
            "status, notes, api_config, knowledge_profile, created_at, updated_at "
            "FROM seo_agent.sites WHERE id = :id"
        ),
        {"id": site_id},
    )
    row = result.mappings().first()
    return _attach_publish_state(dict(row)) if row else None


async def resolve_site(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    label: str | None = None,
    market: str | None = None,
    language_code: str | None = None,
) -> dict[str, Any] | None:
    if site_id:
        row = (
            await session.execute(
                text(
                    "SELECT id, site_key, name, content_role, market, language_code "
                    "FROM seo_agent.sites WHERE id = :id AND status = 'active' "
                    "AND (CAST(:market AS text) IS NULL OR market = CAST(:market AS text)) "
                    "AND (CAST(:language_code AS text) IS NULL OR lower(language_code) = lower(CAST(:language_code AS text)))"
                ),
                {"id": site_id, "market": market, "language_code": language_code},
            )
        ).mappings().first()
        return dict(row) if row else None
    if not label:
        return None
    exact = await session.execute(
        text(
            "SELECT id, site_key, name, content_role, market, language_code "
            "FROM seo_agent.sites WHERE status = 'active' "
            "AND (site_key = :label OR name = :label) "
            "AND (CAST(:market AS text) IS NULL OR market = CAST(:market AS text)) "
            "AND (CAST(:language_code AS text) IS NULL OR lower(language_code) = lower(CAST(:language_code AS text)))"
        ),
        {"label": label, "market": market, "language_code": language_code},
    )
    exact_rows = exact.mappings().all()
    if len(exact_rows) == 1:
        return dict(exact_rows[0])
    result = await session.execute(
        text(
            """
            SELECT id, site_key, name, content_role, market, language_code
              FROM seo_agent.sites
             WHERE status = 'active'
               AND content_role = :label
               AND (CAST(:market AS text) IS NULL OR market = CAST(:market AS text))
               AND (CAST(:language_code AS text) IS NULL OR lower(language_code) = lower(CAST(:language_code AS text)))
             ORDER BY name
            """
        ),
        {"label": label, "market": market, "language_code": language_code},
    )
    rows = result.mappings().all()
    return dict(rows[0]) if len(rows) == 1 else None


async def resolve_site_id(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    label: str | None = None,
    market: str | None = None,
    language_code: str | None = None,
) -> str | None:
    site = await resolve_site(
        session,
        site_id=site_id,
        label=label,
        market=market,
        language_code=language_code,
    )
    return str(site["id"]) if site else None


async def upsert_site(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """????????site_key ??????"""
    site_type = payload.get("site_type") or payload.get("siteType") or "other"
    if site_type not in SUPPORTED_SITE_TYPES:
        raise ValueError(f"site_type ??? {SUPPORTED_SITE_TYPES} ??")
    site_key = payload.get("site_key") or payload.get("siteKey") or payload.get("name")
    if not site_key:
        raise ValueError("site_key / siteKey / name ??????")

    existing = (
        await session.execute(
            text("SELECT api_config, publish_config, raw, knowledge_profile, business_id, strategy_enabled FROM seo_agent.sites WHERE site_key = :site_key"),
            {"site_key": site_key},
        )
    ).mappings().first()
    input_api_config = payload.get("api_config") if "api_config" in payload else payload.get("apiConfig")
    input_publish_config = payload.get("publish_config") if "publish_config" in payload else payload.get("publishConfig")
    existing_api_config = (existing or {}).get("api_config") or {}
    existing_publish_config = (existing or {}).get("publish_config") or {}
    existing_raw = (existing or {}).get("raw") or {}
    existing_knowledge = (existing or {}).get("knowledge_profile") or {}
    business_id = payload.get("business_id", payload.get("businessId", (existing or {}).get("business_id")))
    strategy_enabled = bool(payload.get("strategy_enabled", payload.get("strategyEnabled", (existing or {}).get("strategy_enabled", False))))
    if strategy_enabled and not str(business_id or "").strip():
        raise ValueError("启用策略前必须设置 business_id")
    api_config = {**existing_api_config, **(input_api_config or {})}
    publish_config = {**existing_publish_config, **(input_publish_config or {})}

    sql = text(
        """
        INSERT INTO seo_agent.sites
          (site_key, name, site_type, domain, base_url, api_base_url,
           market, language_code, google_gl, google_hl, semrush_database,
           content_role, content_scope, business_id, strategy_enabled, is_main, allow_external_links,
           publish_config, api_config, status, notes, raw, knowledge_profile)
        VALUES
          (:site_key, :name, :site_type, :domain, :base_url, :api_base_url,
           :market, :language_code, :google_gl, :google_hl, :semrush_database,
           :content_role, :content_scope, :business_id, :strategy_enabled, :is_main, :allow_external_links,
           CAST(:publish_config AS jsonb), CAST(:api_config AS jsonb),
           :status, :notes, CAST(:raw AS jsonb), CAST(:knowledge_profile AS jsonb))
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
          content_scope = EXCLUDED.content_scope,
          business_id = EXCLUDED.business_id,
          strategy_enabled = EXCLUDED.strategy_enabled,
          is_main = EXCLUDED.is_main,
          allow_external_links = EXCLUDED.allow_external_links,
          publish_config = EXCLUDED.publish_config,
          api_config = EXCLUDED.api_config,
          status = EXCLUDED.status,
          notes = EXCLUDED.notes,
          raw = EXCLUDED.raw,
          knowledge_profile = EXCLUDED.knowledge_profile
        RETURNING id, site_key, name, site_type, business_id, strategy_enabled, status
        """
    )
    params = {
        "site_key": site_key,
        "name": payload.get("name") or site_key,
        "site_type": site_type,
        "domain": payload.get("domain"),
        "base_url": payload.get("base_url") or payload.get("baseUrl"),
        "api_base_url": payload.get("api_base_url") or payload.get("apiBaseUrl"),
        "market": payload.get("market"),
        "language_code": payload.get("language_code") or payload.get("languageCode"),
        "google_gl": payload.get("google_gl") or payload.get("googleGl"),
        "google_hl": payload.get("google_hl") or payload.get("googleHl"),
        "semrush_database": payload.get("semrush_database") or payload.get("semrushDatabase"),
        "content_role": payload.get("content_role") or payload.get("contentRole"),
        "content_scope": payload.get("content_scope") or payload.get("contentScope"),
        "business_id": str(business_id).strip() if business_id else None,
        "strategy_enabled": strategy_enabled,
        "is_main": bool(payload.get("is_main", payload.get("isMain", False))),
        "allow_external_links": bool(payload.get("allow_external_links", payload.get("allowExternalLinks", False))),
        "publish_config": _to_json(publish_config),
        "api_config": _to_json(api_config),
        "status": payload.get("status") or "active",
        "notes": payload.get("notes"),
        "raw": _to_json({**existing_raw, **(payload.get("raw") if isinstance(payload.get("raw"), dict) else payload)}),
        "knowledge_profile": _to_json(payload.get("knowledge_profile") if isinstance(payload.get("knowledge_profile"), dict) else existing_knowledge),
    }
    result = await session.execute(sql, params)
    await session.commit()
    row = result.mappings().first()
    return dict(row) if row else {}


async def delete_site(session: AsyncSession, site_id: str) -> bool:
    """????????? posts / tasks / articles / keywords.assigned_site_id??"""
    result = await session.execute(
        text("DELETE FROM seo_agent.sites WHERE id = :id"),
        {"id": site_id},
    )
    await session.commit()
    return (result.rowcount or 0) > 0


def _attach_publish_state(site: dict[str, Any]) -> dict[str, Any]:
    api_config = site.pop("api_config", None) or {}
    site["knowledge_profile"] = site.pop("knowledge_profile", None) or None
    site_type = (site.get("site_type") or "").lower()
    connector_type = str(api_config.get("connector_type") or ("wordpress" if site_type == "wp" else "shopify" if site_type == "shopify" else "custom_openapi"))
    site["connector_type"] = connector_type
    site["api_config_summary"] = {
        "configured_keys": [key for key, value in api_config.items() if value and key not in {"connector_type"}],
        "articles_path": api_config.get("articlesPath"),
        "publish_path": api_config.get("publishPath"),
    }

    if connector_type in {"wp", "wordpress"}:
        ready = bool(
            (site.get("domain") or site.get("base_url"))
            and api_config.get("username")
            and api_config.get("applicationPassword")
        )
        site["publish_adapter"] = "wordpress"
        site["publish_hint"] = "WordPress ?????" if ready else "?? WordPress ???????"
    elif connector_type in {"shopify", "shopify_admin"}:
        ready = bool(site.get("domain") or site.get("base_url"))
        site["publish_adapter"] = "shopify"
        site["publish_hint"] = "Shopify GraphQL" if ready else "?? Shopify ???????"
    else:
        ready = bool(
            (site.get("api_base_url") or site.get("base_url"))
            and (api_config.get("openApiKey") or api_config.get("tokenA") or api_config.get("tokenB"))
        )
        site["publish_adapter"] = "openapi"
        site["publish_hint"] = "OpenAPI ?????" if ready else "?? OpenAPI ?????"

    site["publish_ready"] = ready
    return site


def _to_json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
