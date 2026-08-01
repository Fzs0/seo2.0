"""Resolve the real media writer for each site without crossing business scope."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.publishers import PublisherBase
from app.core.article_urls import is_content_openapi_site, is_oemapps_site
from app.services.shopify_connection_service import publisher_for_site_runtime


@dataclass(frozen=True)
class SiteMediaUploader:
    publisher: PublisherBase
    media_host_site: dict[str, Any]
    transport: str


async def resolve_site_media_uploader(
    session: AsyncSession,
    site: dict[str, Any],
    *,
    dry_run: bool,
    require_active: bool,
) -> SiteMediaUploader:
    """Return the connector that owns the uploaded file.

    WordPress and OEMApps main sites upload to themselves. Self-hosted content
    blogs have no media endpoint, so they use the active OEMApps main site from
    the same ``business_id`` as their media host. The resulting public URL is
    then localized by the custom blog's article API during publish.
    """
    media_host = site
    transport = "direct_upload"
    if is_content_openapi_site(site):
        business_id = str(site.get("business_id") or "").strip()
        if not business_id:
            raise ValueError("custom blog media routing requires business_id")
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, business_id, site_key, name, site_type,
                           domain, base_url, api_base_url, status, api_config
                      FROM seo_agent.sites
                     WHERE business_id = :business_id
                       AND is_main IS TRUE
                       AND status = 'active'
                     ORDER BY site_key ASC
                    """
                ),
                {"business_id": business_id},
            )
        ).mappings().all()
        media_host = next(
            (
                dict(row)
                for row in rows
                if str(row.get("business_id") or "") == business_id
                and is_oemapps_site(dict(row))
            ),
            None,
        )
        if media_host is None:
            raise ValueError(
                "custom blog has no active same-business OEMApps media host"
            )
        transport = "business_oemapps_upload_then_article_publish"

    publisher = await publisher_for_site_runtime(
        session,
        media_host,
        dry_run=dry_run,
        require_active=require_active,
    )
    if "upload_image" not in publisher.capabilities:
        raise ValueError("resolved media host does not support image upload")
    return SiteMediaUploader(
        publisher=publisher,
        media_host_site=media_host,
        transport=transport,
    )


__all__ = ["SiteMediaUploader", "resolve_site_media_uploader"]
