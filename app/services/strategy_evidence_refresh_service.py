"""Read-only remote evidence refresh used after repeated strategy holds."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.google_config import get_store
from app.services.content_audit_service import scan_content
from app.services.custom_connector_service import sync_connector_products
from app.services.google_sync import sync_source
from app.services.oemapps_collection_service import sync_oemapps_collections
from app.services.oemapps_home_seo_service import sync_oemapps_home_seo
from app.services.shopify_product_service import sync_shopify_products
from app.services.strategy_hold_service import EXPANDED_EVIDENCE_SOURCES


async def refresh_default_strategy_evidence(
    session: AsyncSession,
    *,
    business_id: str,
    sites: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Refresh configured read-only sources and return per-site audit records."""
    refreshed_at = datetime.now(UTC).isoformat()
    records = {
        str(site["id"]): {
            "site_id": str(site["id"]),
            "refreshed_at": refreshed_at,
            "sources": {
                source: _source_record("unavailable", refreshed_at, error="source not configured")
                for source in EXPANDED_EVIDENCE_SOURCES
            },
        }
        for site in sites
    }

    await _refresh_google(session, sites=sites, records=records, refreshed_at=refreshed_at)
    await _refresh_site_assets(session, sites=sites, records=records, refreshed_at=refreshed_at)
    await _refresh_content(
        session,
        business_id=business_id,
        records=records,
        refreshed_at=refreshed_at,
    )
    for record in records.values():
        record["degraded"] = any(
            item["status"] not in {"fresh", "empty"}
            for item in record["sources"].values()
        )
    return list(records.values())


async def _refresh_google(
    session: AsyncSession,
    *,
    sites: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    refreshed_at: str,
) -> None:
    configured = list(get_store().list_all())
    for site in sites:
        site_id = str(site["id"])
        host = _host(site.get("domain") or site.get("base_url"))
        matches = [source for source in configured if _host(source.gsc_host()) == host]
        if not matches:
            continue
        for source in matches:
            try:
                result = await sync_source(
                    session,
                    source.id,
                    days_back=28,
                    trigger="strategy_hold_refresh",
                )
            except Exception as error:  # external read isolation
                for source_type in ("gsc", "ga4"):
                    records[site_id]["sources"][source_type] = _source_record(
                        "failed", refreshed_at, error=str(error)
                    )
                continue
            by_type = {
                str(item.get("type")): item for item in result.get("results") or []
            }
            for source_type in ("gsc", "ga4"):
                item = by_type.get(source_type)
                if not item:
                    records[site_id]["sources"][source_type] = _source_record(
                        "failed",
                        refreshed_at,
                        error=str(result.get("error") or f"{source_type} result missing"),
                    )
                    continue
                records[site_id]["sources"][source_type] = _source_record(
                    "fresh" if item.get("ok") else "failed",
                    refreshed_at,
                    snapshot_id=item.get("log_id"),
                    error=item.get("error"),
                    detail=item,
                )


async def _refresh_site_assets(
    session: AsyncSession,
    *,
    sites: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    refreshed_at: str,
) -> None:
    connector_rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, site_id::text AS site_id
                  FROM seo_agent.custom_connectors
                 WHERE status = 'active'
                   AND site_id = ANY(CAST(:site_ids AS uuid[]))
                """
            ),
            {"site_ids": [str(site["id"]) for site in sites]},
        )
    ).mappings().all()
    by_site: dict[str, list[str]] = {}
    for row in connector_rows:
        by_site.setdefault(str(row["site_id"]), []).append(str(row["id"]))

    for site in sites:
        site_id = str(site["id"])
        product_results: list[dict[str, Any]] = []
        collection_results: list[dict[str, Any]] = []
        home_results: list[dict[str, Any]] = []
        if str(site.get("site_type") or "").casefold() in {"shopify", "shopify_admin"}:
            product_results.append(
                await _isolated(sync_shopify_products, session, site_id=site_id)
            )
        for connector_id in by_site.get(site_id, []):
            product_results.append(
                await _isolated(sync_connector_products, session, connector_id)
            )
            collection_results.append(
                await _isolated(sync_oemapps_collections, session, connector_id)
            )
            home_results.append(
                await _isolated(sync_oemapps_home_seo, session, connector_id)
            )
        records[site_id]["sources"]["products"] = _aggregate(
            product_results, refreshed_at, "product connector not configured"
        )
        records[site_id]["sources"]["collections"] = _aggregate(
            collection_results, refreshed_at, "collection connector not configured"
        )
        records[site_id]["sources"]["on_page"] = _aggregate(
            [*product_results, *collection_results, *home_results],
            refreshed_at,
            "on-page connector not configured",
        )


async def _refresh_content(
    session: AsyncSession,
    *,
    business_id: str,
    records: dict[str, dict[str, Any]],
    refreshed_at: str,
) -> None:
    try:
        audit = await scan_content(
            session,
            business_id=business_id,
            refresh=True,
            fetch_serp=True,
            use_ai=False,
        )
    except Exception as error:  # article/SERP refresh is one isolated boundary
        for record in records.values():
            for source in (
                "articles",
                "live_serp",
                "publishing_frequency",
                "content_gap",
            ):
                record["sources"][source] = _source_record(
                    "failed", refreshed_at, error=str(error)
                )
        return

    items_by_site: dict[str, list[dict[str, Any]]] = {}
    for item in audit.get("items") or []:
        item_site_id = str(item.get("site_id") or "")
        if item_site_id:
            items_by_site.setdefault(item_site_id, []).append(item)
    scan_id = audit.get("scanned_at")
    for site_id, record in records.items():
        items = items_by_site.get(site_id, [])
        record["sources"]["articles"] = _source_record(
            "fresh", refreshed_at, snapshot_id=scan_id, detail=audit.get("sync")
        )
        record["sources"]["live_serp"] = _source_record(
            "fresh" if (audit.get("data_sources") or {}).get("serp") else "empty",
            refreshed_at,
            snapshot_id=scan_id,
            detail=(audit.get("data_sources") or {}).get("serp"),
        )
        record["sources"]["publishing_frequency"] = _source_record(
            "fresh",
            refreshed_at,
            snapshot_id=scan_id,
            detail={"candidate_rows": len(items)},
        )
        record["sources"]["content_gap"] = _source_record(
            "fresh" if items else "empty",
            refreshed_at,
            snapshot_id=scan_id,
            detail={
                "candidate_rows": len(items),
                "new_article_candidates": sum(
                    item.get("action") == "new_article" for item in items
                ),
            },
        )


async def _isolated(function: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        result = dict(await function(*args, **kwargs) or {})
        return {"ok": result.get("ok") is not False, "detail": result}
    except Exception as error:
        return {"ok": False, "error": str(error)}


def _aggregate(results: list[dict[str, Any]], refreshed_at: str, missing: str) -> dict[str, Any]:
    if not results:
        return _source_record("unavailable", refreshed_at, error=missing)
    failures = [item for item in results if not item.get("ok")]
    return _source_record(
        "fresh" if not failures else "failed",
        refreshed_at,
        error="; ".join(str(item.get("error") or "refresh failed") for item in failures)
        or None,
        detail=results,
    )


def _source_record(
    status: str,
    refreshed_at: str,
    *,
    snapshot_id: Any = None,
    error: Any = None,
    detail: Any = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "refreshed_at": refreshed_at,
        "snapshot_id": str(snapshot_id) if snapshot_id else None,
        "error": str(error)[:1000] if error else None,
        "detail": detail,
        "decision_impact": (
            "fresh evidence included in candidate recalculation"
            if status in {"fresh", "success", "empty"}
            else "candidate confidence may be reduced; source failure cannot bypass safety gates"
        ),
    }


def _host(value: Any) -> str:
    from urllib.parse import urlsplit

    raw = str(value or "").strip().casefold()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").rstrip(".")
    return host[4:] if host.startswith("www.") else host


__all__ = ["refresh_default_strategy_evidence"]
