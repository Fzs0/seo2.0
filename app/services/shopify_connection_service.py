"""Per-site encrypted Shopify Admin connections."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.publishers import (
    PublisherBase,
    ShopifyPublisher,
    evict_shopify_token_cache,
    publisher_for_site,
)
from app.connectors.secrets import SecretCipher
from app.core.article_urls import canonical_article_connector_type, is_oemapps_site
from app.core.config import get_settings
from app.services.site_config_service import (
    load_openapi_runtime_api_config,
    load_wordpress_runtime_api_config,
)


_SHOP_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$", re.IGNORECASE)
# Shopify documents that when both read/write are needed for one resource,
# checking the write scope is sufficient; the live probe below verifies reads.
_REQUIRED_SCOPES = {"write_content"}


async def _lock_site(session: AsyncSession, site_id: str) -> None:
    """Serialize credential/config changes with live Shopify operations."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:site_id, 7122026))"),
        {"site_id": site_id},
    )


def _cipher() -> SecretCipher:
    return SecretCipher(get_settings().connector_secret_key)


def _normalize_shop_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    if (
        parsed.scheme not in {"", "http", "https"}
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("shop_domain must be a valid *.myshopify.com domain")
    host = (parsed.hostname or "").rstrip(".")
    if not _SHOP_DOMAIN.fullmatch(host):
        raise ValueError("shop_domain must be a valid *.myshopify.com domain")
    return host


def _fingerprint(shop_domain: str, client_id: str, client_secret: str) -> str:
    return hashlib.sha256(f"{shop_domain}\0{client_id}\0{client_secret}".encode()).hexdigest()


def _public(row: Any) -> dict[str, Any]:
    return {
        "site_id": str(row["site_id"]),
        "shop_domain": row["shop_domain"],
        "blog_handle": row["blog_handle"],
        "api_version": row["api_version"],
        "status": row["status"],
        "scopes": list(row["scopes"] or []),
        "configured_secret_names": list(row.get("secret_names") or []),
        "last_tested_at": row["last_tested_at"],
        "last_error": _safe_error(row["last_error"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _safe_error(value: Any) -> str | None:
    if not value:
        return None
    text_value = str(value)
    text_value = re.sub(
        r"(?i)(client_secret|access_token|authorization|token)\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        text_value,
    )
    return text_value[:1000]


async def configure_shopify_connection(
    session: AsyncSession,
    *,
    site_id: str,
    shop_domain: str,
    blog_handle: str,
    api_version: str,
    client_id: str | None,
    client_secret: str | None,
) -> dict[str, Any]:
    if len(str(client_id or "")) > 1000 or len(str(client_secret or "")) > 4000:
        raise ValueError("Shopify credential field exceeds the allowed length")
    domain = _normalize_shop_domain(shop_domain)
    handle = str(blog_handle or "news").strip().strip("/")
    version = str(api_version or get_settings().shopify_api_version).strip()
    if not handle or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", handle):
        raise ValueError("blog_handle is invalid")
    supplied = [bool(str(client_id or "").strip()), bool(str(client_secret or "").strip())]
    if any(supplied) and not all(supplied):
        raise ValueError("client_id and client_secret must be provided together")

    await _lock_site(session, site_id)
    site = (
        await session.execute(
            text("SELECT id, site_type FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"),
            {"id": site_id},
        )
    ).mappings().first()
    if not site:
        raise ValueError("site not found")
    if str(site["site_type"]).casefold() not in {"shopify", "shopify_admin"}:
        raise ValueError("site is not a Shopify site")

    existing = (
        await session.execute(
            text(
                "SELECT shop_domain, blog_handle, api_version, status, scopes, credential_fingerprint "
                "FROM seo_agent.shopify_connections "
                "WHERE site_id=CAST(:id AS uuid)"
            ),
            {"id": site_id},
        )
    ).mappings().first()
    existing_secret = (
        await session.execute(
            text("SELECT encrypted_value FROM seo_agent.shopify_connection_secrets WHERE site_id = CAST(:id AS uuid)"),
            {"id": site_id},
        )
    ).scalar_one_or_none()
    if not all(supplied) and existing_secret is None:
        raise ValueError("client_id and client_secret are required for a new Shopify connection")
    if existing and existing["status"] == "active" and existing["shop_domain"] != domain:
        raise ValueError("an active Shopify connection cannot change shop_domain; create a new site instead")
    reserved_domain = (
        await session.execute(
            text("SELECT shop_domain FROM seo_agent.shopify_connection_rollbacks WHERE site_id=CAST(:id AS uuid)"),
            {"id": site_id},
        )
    ).scalar_one_or_none()
    if reserved_domain and reserved_domain != domain:
        raise ValueError("shop_domain cannot change while a replacement connection is awaiting verification")

    material_change = bool(
        all(supplied)
        or not existing
        or existing["shop_domain"] != domain
        or existing["blog_handle"] != handle
        or existing["api_version"] != version
    )
    if existing and existing["status"] == "active" and material_change and existing_secret is not None:
        secret_names = (
            await session.execute(
                text("SELECT secret_names FROM seo_agent.shopify_connection_secrets WHERE site_id=CAST(:id AS uuid)"),
                {"id": site_id},
            )
        ).scalar_one()
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.shopify_connection_rollbacks
                  (site_id, shop_domain, blog_handle, api_version, scopes, credential_fingerprint,
                   encrypted_value, secret_names)
                VALUES (CAST(:id AS uuid), :domain, :handle, :version, :scopes, :fingerprint,
                        :encrypted, :secret_names)
                ON CONFLICT (site_id) DO UPDATE SET
                  shop_domain=EXCLUDED.shop_domain, blog_handle=EXCLUDED.blog_handle,
                  api_version=EXCLUDED.api_version, scopes=EXCLUDED.scopes,
                  credential_fingerprint=EXCLUDED.credential_fingerprint,
                  encrypted_value=EXCLUDED.encrypted_value, secret_names=EXCLUDED.secret_names,
                  created_at=now()
                """
            ),
            {
                "id": site_id,
                "domain": existing["shop_domain"],
                "handle": existing["blog_handle"],
                "version": existing["api_version"],
                "scopes": list(existing["scopes"] or []),
                "fingerprint": existing["credential_fingerprint"],
                "encrypted": existing_secret,
                "secret_names": list(secret_names or []),
            },
        )
    try:
        await session.execute(
            text(
                """
            INSERT INTO seo_agent.shopify_connections
              (site_id, shop_domain, blog_handle, api_version, status, scopes, last_error)
            VALUES (CAST(:site_id AS uuid), :shop_domain, :blog_handle, :api_version, 'draft', ARRAY[]::text[], NULL)
            ON CONFLICT (site_id) DO UPDATE SET
              shop_domain = EXCLUDED.shop_domain,
              blog_handle = EXCLUDED.blog_handle,
              api_version = EXCLUDED.api_version,
              status = CASE WHEN CAST(:reset_status AS boolean) THEN 'draft' ELSE seo_agent.shopify_connections.status END,
              scopes = CASE WHEN CAST(:reset_status AS boolean) THEN ARRAY[]::text[] ELSE seo_agent.shopify_connections.scopes END,
              last_tested_at = CASE WHEN CAST(:reset_status AS boolean) THEN NULL ELSE seo_agent.shopify_connections.last_tested_at END,
              last_error = NULL
            """
            ),
            {
                "site_id": site_id,
                "shop_domain": domain,
                "blog_handle": handle,
                "api_version": version,
                "reset_status": material_change,
            },
        )
        if all(supplied):
            secrets = {"client_id": str(client_id).strip(), "client_secret": str(client_secret).strip()}
            await session.execute(
                text(
                    """
                INSERT INTO seo_agent.shopify_connection_secrets
                  (site_id, encrypted_value, secret_names)
                VALUES (CAST(:site_id AS uuid), :encrypted, :names)
                ON CONFLICT (site_id) DO UPDATE SET
                  encrypted_value = EXCLUDED.encrypted_value,
                  secret_names = EXCLUDED.secret_names,
                  updated_at = now()
                """
                ),
                {
                    "site_id": site_id,
                    "encrypted": _cipher().encrypt(secrets),
                    "names": sorted(secrets),
                },
            )
            await session.execute(
                text("UPDATE seo_agent.shopify_connections SET credential_fingerprint=:fp WHERE site_id=CAST(:id AS uuid)"),
                {"id": site_id, "fp": _fingerprint(domain, secrets["client_id"], secrets["client_secret"])},
            )
            await _record_run(session, site_id, "rotate", True, {"credential_rotated": True}, None)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ValueError("this Shopify shop domain is already assigned to another site") from error
    except Exception:
        await session.rollback()
        raise
    if material_change:
        evict_shopify_token_cache(site_id)
    return await get_shopify_connection(session, site_id=site_id, include_missing=True)


async def get_shopify_connection(
    session: AsyncSession, *, site_id: str, include_missing: bool = False
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT c.*, COALESCE(s.secret_names, ARRAY[]::text[]) AS secret_names
                  FROM seo_agent.shopify_connections c
                  LEFT JOIN seo_agent.shopify_connection_secrets s ON s.site_id = c.site_id
                 WHERE c.site_id = CAST(:id AS uuid)
                """
            ),
            {"id": site_id},
        )
    ).mappings().first()
    if not row and include_missing:
        return {"site_id": site_id, "status": "not_configured", "configured_secret_names": []}
    if not row:
        raise ValueError("Shopify connection not found")
    return _public(row)


async def load_shopify_runtime(
    session: AsyncSession, *, site_id: str, require_active: bool = True
) -> tuple[dict[str, Any], dict[str, str]]:
    if require_active:
        await _lock_site(session, site_id)
    row = (
        await session.execute(
            text(
                """
                SELECT c.*, s.encrypted_value
                  FROM seo_agent.shopify_connections c
                  JOIN seo_agent.shopify_connection_secrets s ON s.site_id = c.site_id
                 WHERE c.site_id = CAST(:id AS uuid)
                """
            ),
            {"id": site_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("Shopify connection or credentials not found")
    if require_active and row["status"] != "active":
        rollback = (
            await session.execute(
                text(
                    "SELECT site_id, shop_domain, blog_handle, api_version, 'active' AS status, "
                    "scopes, credential_fingerprint, encrypted_value "
                    "FROM seo_agent.shopify_connection_rollbacks WHERE site_id=CAST(:id AS uuid)"
                ),
                {"id": site_id},
            )
        ).mappings().first()
        if not rollback:
            raise ValueError(f"Shopify connection is not active (status={row['status']})")
        row = rollback
    return dict(row), _cipher().decrypt(row["encrypted_value"])


async def publisher_for_site_runtime(
    session: AsyncSession,
    site: dict[str, Any],
    *,
    dry_run: bool,
    require_active: bool = True,
) -> PublisherBase:
    connector_type = canonical_article_connector_type(site)
    if connector_type != "shopify":
        runtime_site = site
        if connector_type == "wordpress":
            runtime_site = {
                **site,
                "api_config": load_wordpress_runtime_api_config(site),
            }
        elif is_oemapps_site(site):
            token = await _load_active_oemapps_token(
                session,
                site_id=str(site["id"]),
                require_active=require_active,
            )
            if token:
                runtime_site = {
                    **site,
                    "api_config": {
                        **dict(site.get("api_config") or {}),
                        "tokenB": token,
                    },
                }
        elif connector_type == "custom_openapi":
            runtime_site = {
                **site,
                "api_config": load_openapi_runtime_api_config(site),
            }
        return publisher_for_site(runtime_site, dry_run=dry_run)
    if dry_run and not require_active:
        try:
            stored, secrets = await load_shopify_runtime(
                session, site_id=str(site["id"]), require_active=False
            )
        except ValueError:
            return ShopifyPublisher(site, dry_run=True, credentials={})
    else:
        stored, secrets = await load_shopify_runtime(
            session, site_id=str(site["id"]), require_active=require_active
        )
    runtime_site = {
        **site,
        "api_config": {
            **dict(site.get("api_config") or {}),
            "shopDomain": stored["shop_domain"],
            "blogHandle": stored["blog_handle"],
            "apiVersion": stored["api_version"],
        },
    }
    return ShopifyPublisher(runtime_site, dry_run=dry_run, credentials=secrets)


async def _load_active_oemapps_token(
    session: AsyncSession,
    *,
    site_id: str,
    require_active: bool,
) -> str | None:
    status_clause = "AND c.status = 'active'" if require_active else ""
    version_column = "c.active_version" if require_active else "c.current_version"
    row = (
        await session.execute(
            text(
                f"""
                SELECT secrets.encrypted_value
                  FROM seo_agent.custom_connectors c
                  JOIN seo_agent.custom_connector_versions version
                    ON version.connector_id = c.id
                   AND version.version = {version_column}
                  JOIN seo_agent.custom_connector_secrets secrets
                    ON secrets.connector_id = c.id
                 WHERE c.site_id = CAST(:site_id AS uuid)
                   AND version.config->>'adapter' = 'oemapps'
                   {status_clause}
                 ORDER BY c.updated_at DESC
                 LIMIT 1
                """
            ),
            {"site_id": site_id},
        )
    ).mappings().first()
    if not row:
        return None
    return str(_cipher().decrypt(row["encrypted_value"]).get("token") or "").strip() or None


async def test_shopify_connection(session: AsyncSession, *, site_id: str) -> dict[str, Any]:
    await _lock_site(session, site_id)
    stored, secrets = await load_shopify_runtime(session, site_id=site_id, require_active=False)
    site = (
        await session.execute(
            text("SELECT id, site_type, domain, base_url, api_config FROM seo_agent.sites WHERE id=CAST(:id AS uuid)"),
            {"id": site_id},
        )
    ).mappings().one()
    runtime_site = dict(site)
    runtime_site["api_config"] = {
        **dict(runtime_site.get("api_config") or {}),
        "shopDomain": stored["shop_domain"],
        "blogHandle": stored["blog_handle"],
        "apiVersion": stored["api_version"],
    }
    publisher = ShopifyPublisher(runtime_site, dry_run=False, credentials=secrets)
    try:
        info = await publisher.test_credentials()
        scopes = sorted({str(value).strip().casefold() for value in info["scopes"] if str(value).strip()})
        missing = sorted(_REQUIRED_SCOPES - set(scopes))
        if missing:
            raise ValueError(f"Shopify connection missing required scopes: {', '.join(missing)}")
    except Exception as error:
        updated = await session.execute(
            text(
                "UPDATE seo_agent.shopify_connections SET status='failed', last_tested_at=now(), last_error=:error "
                "WHERE site_id=CAST(:id AS uuid) AND credential_fingerprint=:fingerprint "
                "AND shop_domain=:shop_domain AND blog_handle=:blog_handle AND api_version=:api_version"
            ),
            {
                "id": site_id,
                "error": _safe_error(error),
                "fingerprint": stored["credential_fingerprint"],
                "shop_domain": stored["shop_domain"],
                "blog_handle": stored["blog_handle"],
                "api_version": stored["api_version"],
            },
        )
        if updated.rowcount == 0:
            await session.rollback()
            return {"ok": False, "site_id": site_id, "status": "stale", "error": "connection changed during verification"}
        safe_error = _safe_error(error)
        await _record_run(session, site_id, "test", False, {}, safe_error)
        restored = await _restore_shopify_rollback(session, site_id)
        await session.commit()
        return {
            "ok": False,
            "site_id": site_id,
            "status": "active" if restored else "failed",
            "restored_previous": restored,
            "error": safe_error,
        }
    updated = await session.execute(
        text(
            "UPDATE seo_agent.shopify_connections SET status='verified', scopes=:scopes, "
            "last_tested_at=now(), last_error=NULL WHERE site_id=CAST(:id AS uuid) "
            "AND credential_fingerprint=:fingerprint AND shop_domain=:shop_domain "
            "AND blog_handle=:blog_handle AND api_version=:api_version "
            "AND status IN ('draft','failed','verified','active')"
        ),
        {
            "id": site_id,
            "scopes": scopes,
            "fingerprint": stored["credential_fingerprint"],
            "shop_domain": stored["shop_domain"],
            "blog_handle": stored["blog_handle"],
            "api_version": stored["api_version"],
        },
    )
    if updated.rowcount == 0:
        await session.rollback()
        return {"ok": False, "site_id": site_id, "status": "stale", "error": "connection changed during verification"}
    await _record_run(session, site_id, "test", True, {"scopes": scopes}, None)
    await session.commit()
    return {"ok": True, "site_id": site_id, "status": "verified", "scopes": scopes}


async def activate_shopify_connection(session: AsyncSession, *, site_id: str) -> dict[str, Any]:
    await _lock_site(session, site_id)
    row = (
        await session.execute(
            text("SELECT status, scopes FROM seo_agent.shopify_connections WHERE site_id=CAST(:id AS uuid) FOR UPDATE"),
            {"id": site_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("Shopify connection not found")
    if row["status"] not in {"verified", "active"}:
        raise ValueError("Shopify connection must pass verification before activation")
    missing = sorted(_REQUIRED_SCOPES - set(row["scopes"] or []))
    if missing:
        raise ValueError(f"Shopify connection missing required scopes: {', '.join(missing)}")
    await session.execute(
        text("UPDATE seo_agent.shopify_connections SET status='active', last_error=NULL WHERE site_id=CAST(:id AS uuid)"),
        {"id": site_id},
    )
    await session.execute(
        text("DELETE FROM seo_agent.shopify_connection_rollbacks WHERE site_id=CAST(:id AS uuid)"),
        {"id": site_id},
    )
    await _record_run(session, site_id, "activate", True, {"scopes": list(row["scopes"] or [])}, None)
    await session.commit()
    return await get_shopify_connection(session, site_id=site_id)


async def _record_run(
    session: AsyncSession,
    site_id: str,
    run_type: str,
    ok: bool,
    summary: dict[str, Any],
    error: str | None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.shopify_connection_runs
              (site_id, run_type, status, summary, error_summary)
            VALUES (CAST(:site_id AS uuid), :run_type, :status, CAST(:summary AS jsonb), :error)
            """
        ),
        {
            "site_id": site_id,
            "run_type": run_type,
            "status": "succeeded" if ok else "failed",
            "summary": json.dumps(summary, ensure_ascii=False, sort_keys=True),
            "error": _safe_error(error)[:2000] if error else None,
        },
    )


async def _restore_shopify_rollback(session: AsyncSession, site_id: str) -> bool:
    rollback = (
        await session.execute(
            text("SELECT * FROM seo_agent.shopify_connection_rollbacks WHERE site_id=CAST(:id AS uuid) FOR UPDATE"),
            {"id": site_id},
        )
    ).mappings().first()
    if not rollback:
        return False
    await session.execute(
        text(
            """
            UPDATE seo_agent.shopify_connections SET
              shop_domain=:domain, blog_handle=:handle, api_version=:version,
              status='active', scopes=:scopes, credential_fingerprint=:fingerprint,
              last_error=:error
            WHERE site_id=CAST(:id AS uuid)
            """
        ),
        {
            "id": site_id,
            "domain": rollback["shop_domain"],
            "handle": rollback["blog_handle"],
            "version": rollback["api_version"],
            "scopes": list(rollback["scopes"] or []),
            "fingerprint": rollback["credential_fingerprint"],
            "error": "replacement connection failed verification; previous active connection restored",
        },
    )
    await session.execute(
        text(
            "UPDATE seo_agent.shopify_connection_secrets SET encrypted_value=:encrypted, "
            "secret_names=:names, updated_at=now() WHERE site_id=CAST(:id AS uuid)"
        ),
        {"id": site_id, "encrypted": rollback["encrypted_value"], "names": list(rollback["secret_names"] or [])},
    )
    await session.execute(
        text("DELETE FROM seo_agent.shopify_connection_rollbacks WHERE site_id=CAST(:id AS uuid)"),
        {"id": site_id},
    )
    evict_shopify_token_cache(site_id)
    return True


__all__ = [
    "activate_shopify_connection",
    "configure_shopify_connection",
    "get_shopify_connection",
    "load_shopify_runtime",
    "publisher_for_site_runtime",
    "test_shopify_connection",
]
