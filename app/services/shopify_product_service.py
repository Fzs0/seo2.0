"""Deep module for Shopify product SEO synchronization and guarded writes."""
from __future__ import annotations

import json
import hashlib
import hmac
import re
from typing import Any
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.http_client import ExternalCallError
from app.clients.publishers import ShopifyPublisher
from app.core.config import get_settings
from app.services.shopify_connection_service import publisher_for_site_runtime


class ShopifyProductError(ValueError):
    pass


async def sync_shopify_products(
    session: AsyncSession, *, site_id: str, limit: int = 250
) -> dict[str, Any]:
    site = await _shopify_site(session, site_id)
    connector = await publisher_for_site_runtime(
        session, site, dry_run=True, require_active=True
    )
    if not isinstance(connector, ShopifyPublisher):
        raise ShopifyProductError("site does not use the Shopify connector")
    remote_products = await connector.read_products(limit=min(max(limit, 1), 1000))
    upserted = 0
    missing_seo = 0
    for remote in remote_products:
        mapped = _map_product(site, remote)
        if mapped["seo_audit"]["missing_meta_title"] or mapped["seo_audit"]["missing_meta_description"]:
            missing_seo += 1
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.products
                  (external_id, site_id, handle, title, url, category, price, status,
                   image, description, keywords, source, raw, extracted_at,
                   canonical_url, meta_title, meta_description, meta_keywords,
                   images, variants, seo_audit, source_updated_at)
                VALUES
                  (:external_id, CAST(:site_id AS uuid), :handle, :title, :url, :category,
                   :price, :status, CAST(:image AS jsonb), :description, ARRAY[]::text[],
                   'shopify_admin_graphql', CAST(:raw AS jsonb), now(), :canonical_url,
                   :meta_title, :meta_description, ARRAY[]::text[], CAST(:images AS jsonb),
                   CAST(:variants AS jsonb), CAST(:seo_audit AS jsonb), :source_updated_at)
                ON CONFLICT (site_id, external_id)
                  WHERE source_connector_id IS NULL AND site_id IS NOT NULL DO UPDATE SET
                  site_id=EXCLUDED.site_id, handle=EXCLUDED.handle, title=EXCLUDED.title,
                  url=EXCLUDED.url, category=EXCLUDED.category, price=EXCLUDED.price,
                  status=EXCLUDED.status, image=EXCLUDED.image,
                  description=EXCLUDED.description, source=EXCLUDED.source,
                  raw=EXCLUDED.raw, extracted_at=now(), canonical_url=EXCLUDED.canonical_url,
                  meta_title=EXCLUDED.meta_title, meta_description=EXCLUDED.meta_description,
                  images=EXCLUDED.images, variants=EXCLUDED.variants,
                  seo_audit=EXCLUDED.seo_audit,
                  source_updated_at=EXCLUDED.source_updated_at
                """
            ),
            {
                **mapped,
                "image": json.dumps(mapped["image"], ensure_ascii=False),
                "raw": json.dumps(mapped["raw"], ensure_ascii=False),
                "images": json.dumps(mapped["images"], ensure_ascii=False),
                "variants": json.dumps(mapped["variants"], ensure_ascii=False),
                "seo_audit": json.dumps(mapped["seo_audit"], ensure_ascii=False),
            },
        )
        upserted += 1
    await _audit(
        session,
        site_id=site_id,
        external_id="*",
        run_type="sync",
        status="succeeded",
        after={"received": len(remote_products), "upserted": upserted, "missing_seo": missing_seo},
    )
    await session.commit()
    return {
        "ok": True,
        "site_id": site_id,
        "received": len(remote_products),
        "upserted": upserted,
        "missing_seo": missing_seo,
    }


async def list_shopify_product_seo(
    session: AsyncSession,
    *,
    site_id: str,
    missing_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    await _shopify_site(session, site_id)
    missing_clause = (
        "AND (NULLIF(trim(meta_title),'') IS NULL OR NULLIF(trim(meta_description),'') IS NULL)"
        if missing_only
        else ""
    )
    params = {
        "site_id": site_id,
        "limit": min(max(limit, 1), 250),
        "offset": max(offset, 0),
    }
    total = (
        await session.execute(
            text(
                "SELECT count(*) FROM seo_agent.products "
                "WHERE site_id=CAST(:site_id AS uuid) AND source='shopify_admin_graphql' "
                f"{missing_clause}"
            ),
            params,
        )
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                """
                SELECT id, external_id, title, handle, url, status, image, images,
                       meta_title, meta_description, seo_audit, source_updated_at, updated_at
                  FROM seo_agent.products
                 WHERE site_id=CAST(:site_id AS uuid)
                   AND source='shopify_admin_graphql'
                """
                + missing_clause
                + " ORDER BY source_updated_at DESC NULLS LAST, id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return {
        "items": [dict(row) for row in rows],
        "total": int(total or 0),
        "limit": params["limit"],
        "offset": params["offset"],
    }


async def update_shopify_product_seo(
    session: AsyncSession,
    *,
    site_id: str,
    product_id: int,
    expected_updated_at: str,
    meta_title: str,
    meta_description: str,
    actor: str = "local_user",
    dry_run: bool = True,
    preview_token: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    title = str(meta_title or "").strip()
    description = str(meta_description or "").strip()
    if not title or not description:
        raise ShopifyProductError("SEO title and description are both required")
    if len(title) > 70:
        raise ShopifyProductError("SEO title must be 70 characters or fewer")
    if len(description) > 320:
        raise ShopifyProductError("SEO description must be 320 characters or fewer")
    product = (
        await session.execute(
            text(
                """
                SELECT id, external_id, site_id, title, handle, meta_title,
                       meta_description, source_updated_at, source
                  FROM seo_agent.products
                 WHERE id=:product_id AND site_id=CAST(:site_id AS uuid)
                 FOR UPDATE
                """
            ),
            {"product_id": product_id, "site_id": site_id},
        )
    ).mappings().first()
    if not product or product["source"] != "shopify_admin_graphql":
        raise ShopifyProductError("Shopify product not found for this site")
    actual_version = _iso_timestamp(product["source_updated_at"])
    before = {
        "meta_title": product["meta_title"] or "",
        "meta_description": product["meta_description"] or "",
        "source_updated_at": actual_version,
    }
    patch = {"meta_title": title, "meta_description": description}
    if not dry_run:
        if not request_id:
            raise ShopifyProductError("request_id is required for a live update")
        existing_run = (
            await session.execute(
                text(
                    "SELECT site_id, product_id, external_id, requested_patch, status, "
                    "after_snapshot, error_summary FROM seo_agent.shopify_product_seo_runs "
                    "WHERE request_id=CAST(:request_id AS uuid)"
                ),
                {"request_id": request_id},
            )
        ).mappings().first()
        if existing_run:
            same_operation = (
                str(existing_run["site_id"]) == site_id
                and int(existing_run["product_id"] or 0) == product_id
                and str(existing_run["external_id"]) == str(product["external_id"])
                and dict(existing_run["requested_patch"] or {}) == patch
            )
            if not same_operation:
                raise ShopifyProductError("request_id is already bound to a different operation")
            if existing_run["status"] == "succeeded":
                return {
                    "ok": True,
                    "dry_run": False,
                    "idempotent": True,
                    "product_id": product_id,
                    "after": dict(existing_run["after_snapshot"] or {}),
                }
            raise ShopifyProductError(
                f"request_id already exists with status={existing_run['status']}"
            )
    if actual_version != _iso_timestamp(expected_updated_at):
        raise ShopifyProductError("product changed after review; sync and review it again")
    bound_preview_token = _preview_token(site_id, product_id, actual_version, patch)
    if dry_run:
        await _audit(
            session,
            site_id=site_id,
            product_id=product_id,
            external_id=product["external_id"],
            run_type="preview",
            status="succeeded",
            before=before,
            patch=patch,
            actor=actor,
        )
        await session.commit()
        return {
            "ok": True,
            "dry_run": True,
            "product_id": product_id,
            "before": before,
            "after": patch,
            "preview_token": bound_preview_token,
        }
    if not preview_token or not hmac.compare_digest(preview_token, bound_preview_token):
        raise ShopifyProductError("live update requires the matching preview_token")
    preview_exists = (
        await session.execute(
            text(
                """
                SELECT 1 FROM seo_agent.shopify_product_seo_runs
                 WHERE site_id=CAST(:site_id AS uuid) AND product_id=:product_id
                   AND external_id=:external_id AND run_type='preview' AND status='succeeded'
                   AND before_snapshot=CAST(:before AS jsonb)
                   AND requested_patch=CAST(:patch AS jsonb)
                   AND created_at >= now() - interval '15 minutes'
                 LIMIT 1
                """
            ),
            {
                "site_id": site_id,
                "product_id": product_id,
                "external_id": product["external_id"],
                "before": json.dumps(before, ensure_ascii=False),
                "patch": json.dumps(patch, ensure_ascii=False),
            },
        )
    ).first()
    if not preview_exists:
        raise ShopifyProductError("preview expired or was not recorded; preview the change again")
    if before["meta_title"] == title and before["meta_description"] == description:
        return {"ok": True, "dry_run": False, "no_op": True, "product_id": product_id, "before": before, "after": before}
    run_id = await _audit(
        session,
        site_id=site_id,
        product_id=product_id,
        external_id=product["external_id"],
        request_id=str(request_id),
        run_type="update",
        status="running",
        before=before,
        patch=patch,
        actor=actor,
    )
    await session.commit()
    current_version = (
        await session.execute(
            text(
                "SELECT source_updated_at FROM seo_agent.products "
                "WHERE id=:product_id AND site_id=CAST(:site_id AS uuid) FOR UPDATE"
            ),
            {"product_id": product_id, "site_id": site_id},
        )
    ).scalar_one_or_none()
    if _iso_timestamp(current_version) != actual_version:
        await _finish_audit(
            session, run_id, status="stale", error="product changed after preview"
        )
        await session.commit()
        raise ShopifyProductError("product changed after review; sync and review it again")

    try:
        site = await _shopify_site(session, site_id, require_write=True)
        connector = await publisher_for_site_runtime(
            session, site, dry_run=False, require_active=True
        )
        if not isinstance(connector, ShopifyPublisher):
            raise ShopifyProductError("site does not use the Shopify connector")
    except Exception as error:
        await _finish_audit(session, run_id, status="failed", error=str(error))
        await session.commit()
        if isinstance(error, ShopifyProductError):
            raise
        raise ShopifyProductError("Shopify connector could not be loaded") from error
    remote: dict[str, Any] | None = None
    try:
        remote = await connector.update_product_seo(
            product["external_id"],
            title=title,
            description=description,
            expected_updated_at=actual_version,
        )
    except ExternalCallError as error:
        message = str(error)
        status = (
            "stale"
            if "changed after review" in message
            else "failed"
            if "Shopify productUpdate:" in message
            else "unknown"
        )
        if status == "unknown":
            try:
                probe = await connector.get_product_for_seo(product["external_id"])
            except Exception:
                probe = None
            probe_seo = (probe or {}).get("seo") or {}
            if (
                str(probe_seo.get("title") or "") == title
                and str(probe_seo.get("description") or "") == description
            ):
                remote = probe
            else:
                await _finish_audit(session, run_id, status=status, error=message)
                await session.commit()
                raise ShopifyProductError(
                    "Shopify write result is unknown; sync the product before retrying"
                ) from error
        else:
            await _finish_audit(session, run_id, status=status, error=message)
            await session.commit()
            raise ShopifyProductError(str(error)) from error
    except Exception as error:
        await _finish_audit(session, run_id, status="unknown", error=str(error))
        await session.commit()
        raise ShopifyProductError(
            "Shopify write result is unknown; sync the product before retrying"
        ) from error
    assert remote is not None
    after = {
        "meta_title": str((remote.get("seo") or {}).get("title") or ""),
        "meta_description": str((remote.get("seo") or {}).get("description") or ""),
        "source_updated_at": _iso_timestamp(remote.get("updatedAt")),
    }
    await session.execute(
        text(
            """
            UPDATE seo_agent.products SET
              meta_title=:meta_title, meta_description=:meta_description,
              source_updated_at=:source_updated_at,
              seo_audit=CAST(:seo_audit AS jsonb), raw=raw || CAST(:raw_patch AS jsonb),
              extracted_at=now()
            WHERE id=:product_id AND site_id=CAST(:site_id AS uuid)
            """
        ),
        {
            "product_id": product_id,
            "site_id": site_id,
            "meta_title": after["meta_title"],
            "meta_description": after["meta_description"],
            "source_updated_at": after["source_updated_at"],
            "seo_audit": json.dumps(_seo_audit(after["meta_title"], after["meta_description"])),
            "raw_patch": json.dumps({"seo": remote.get("seo") or {}, "updatedAt": remote.get("updatedAt")}),
        },
    )
    await _finish_audit(session, run_id, status="succeeded", after=after)
    await session.commit()
    return {"ok": True, "dry_run": False, "product_id": product_id, "before": before, "after": after}


async def _shopify_site(
    session: AsyncSession, site_id: str, *, require_write: bool = False
) -> dict[str, Any]:
    site = (
        await session.execute(
            text(
                "SELECT s.id, s.site_key, s.name, s.site_type, s.domain, s.base_url, "
                "s.api_base_url, s.api_config, s.status, c.status AS connection_status, "
                "c.scopes AS connection_scopes "
                "FROM seo_agent.sites s LEFT JOIN seo_agent.shopify_connections c ON c.site_id=s.id "
                "WHERE s.id=CAST(:id AS uuid)"
            ),
            {"id": site_id},
        )
    ).mappings().first()
    if not site:
        raise ShopifyProductError("site not found")
    if str(site["site_type"]).casefold() not in {"shopify", "shopify_admin"}:
        raise ShopifyProductError("site is not a Shopify site")
    if site["status"] != "active":
        raise ShopifyProductError("site is not active")
    if site["connection_status"] != "active":
        raise ShopifyProductError("Shopify connection is not active")
    scopes = {str(scope).casefold() for scope in (site["connection_scopes"] or [])}
    if not ({"read_products", "write_products"} & scopes):
        raise ShopifyProductError("Shopify connection is missing product access")
    if require_write and "write_products" not in scopes:
        raise ShopifyProductError("Shopify connection is missing write_products")
    return dict(site)


def _map_product(site: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    external_id = str(remote.get("id") or "")
    if not re.fullmatch(r"gid://shopify/Product/[1-9]\d*", external_id):
        raise ShopifyProductError("Shopify returned an invalid product id")
    media = [item for item in (remote.get("media") or {}).get("nodes", []) if isinstance(item, dict)]
    images = [
        {"id": item.get("id"), "src": (item.get("image") or {}).get("url"), "alt": item.get("alt") or ""}
        for item in media
    ]
    featured = remote.get("featuredMedia") or {}
    featured_image = {
        "id": featured.get("id"),
        "src": (featured.get("image") or {}).get("url"),
        "alt": featured.get("alt") or "",
    } if featured else {}
    variants = [dict(item) for item in (remote.get("variants") or {}).get("nodes", []) if isinstance(item, dict)]
    prices = [float(item["price"]) for item in variants if item.get("price") not in {None, ""}]
    seo = remote.get("seo") or {}
    public_base = str(site.get("domain") or site.get("base_url") or "").rstrip("/")
    handle = str(remote.get("handle") or "")
    url = str(remote.get("onlineStoreUrl") or "") or (f"{public_base}/products/{handle}" if public_base and handle else "")
    return {
        "external_id": external_id,
        "site_id": str(site["id"]),
        "handle": handle,
        "title": str(remote.get("title") or ""),
        "url": url,
        "canonical_url": url,
        "category": str(remote.get("productType") or "") or None,
        "price": min(prices) if prices else None,
        "status": {"ACTIVE": "active", "DRAFT": "draft", "ARCHIVED": "archived"}.get(
            str(remote.get("status") or "").upper(), "draft"
        ),
        "image": featured_image,
        "description": str(remote.get("descriptionHtml") or ""),
        "meta_title": str(seo.get("title") or ""),
        "meta_description": str(seo.get("description") or ""),
        "images": images,
        "variants": variants,
        "seo_audit": _seo_audit(str(seo.get("title") or ""), str(seo.get("description") or ""), images),
        "source_updated_at": _parse_timestamp(remote.get("updatedAt")),
        "raw": remote,
    }


def _seo_audit(title: str, description: str, images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    image_items = images or []
    return {
        "missing_meta_title": not bool(str(title or "").strip()),
        "missing_meta_description": not bool(str(description or "").strip()),
        "images_missing_alt": sum(1 for item in image_items if not str(item.get("alt") or "").strip()),
    }


def _preview_token(
    site_id: str, product_id: int, expected_updated_at: str, patch: dict[str, str]
) -> str:
    payload = json.dumps(
        {
            "site_id": site_id,
            "product_id": product_id,
            "expected_updated_at": expected_updated_at,
            "patch": patch,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    key = get_settings().connector_secret_key.encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ShopifyProductError("Shopify returned an invalid updatedAt timestamp") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso_timestamp(value: Any) -> str:
    parsed = _parse_timestamp(value)
    if not parsed:
        return ""
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def _audit(
    session: AsyncSession,
    *,
    site_id: str,
    external_id: str,
    run_type: str,
    status: str,
    product_id: int | None = None,
    before: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    error: str | None = None,
    actor: str = "local_user",
    request_id: str | None = None,
) -> str:
    result = await session.execute(
        text(
            """
            INSERT INTO seo_agent.shopify_product_seo_runs
              (site_id, product_id, external_id, request_id, run_type, status, before_snapshot,
               requested_patch, after_snapshot, error_summary, actor)
            VALUES
              (CAST(:site_id AS uuid), :product_id, :external_id, CAST(:request_id AS uuid), :run_type, :status,
               CAST(:before AS jsonb), CAST(:patch AS jsonb), CAST(:after AS jsonb),
               :error, :actor)
            RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "product_id": product_id,
            "external_id": external_id,
            "request_id": request_id,
            "run_type": run_type,
            "status": status,
            "before": json.dumps(before or {}, ensure_ascii=False),
            "patch": json.dumps(patch or {}, ensure_ascii=False),
            "after": json.dumps(after or {}, ensure_ascii=False),
            "error": str(error or "")[:1000] or None,
            "actor": str(actor or "local_user")[:128],
        },
    )
    return str(result.scalar_one())


async def _finish_audit(
    session: AsyncSession,
    run_id: str,
    *,
    status: str,
    after: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE seo_agent.shopify_product_seo_runs SET status=:status, "
            "after_snapshot=CAST(:after AS jsonb), error_summary=:error "
            "WHERE id=CAST(:id AS uuid) AND status='running'"
        ),
        {
            "id": run_id,
            "status": status,
            "after": json.dumps(after or {}, ensure_ascii=False),
            "error": str(error or "")[:1000] or None,
        },
    )


__all__ = [
    "ShopifyProductError",
    "list_shopify_product_seo",
    "sync_shopify_products",
    "update_shopify_product_seo",
]
