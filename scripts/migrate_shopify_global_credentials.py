"""One-time, explicit migration from legacy Shopify env credentials to one site.

Usage:
    python scripts/migrate_shopify_global_credentials.py --site-key healthyoxy-shopify

The command never prints credentials. The normal connection test and activation
still need to be run from the site card after migration.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.services.shopify_connection_service import configure_shopify_connection
from app.services.shopify_connection_service import (
    activate_shopify_connection,
    test_shopify_connection,
)


async def migrate(site_key: str, verify_and_activate: bool = False) -> None:
    settings = get_settings()
    if not settings.shopify_client_id or not settings.shopify_client_secret:
        raise SystemExit("Legacy SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET are not configured")

    async with SessionLocal() as session:
        site = (
            await session.execute(
                text(
                    "SELECT id, domain, api_config FROM seo_agent.sites "
                    "WHERE site_key=:site_key AND lower(site_type) IN ('shopify','shopify_admin')"
                ),
                {"site_key": site_key},
            )
        ).mappings().first()
        if not site:
            raise SystemExit(f"Shopify site not found: {site_key}")
        config = dict(site["api_config"] or {})
        result = await configure_shopify_connection(
            session,
            site_id=str(site["id"]),
            shop_domain=str(config.get("shopDomain") or config.get("shop_domain") or site["domain"]),
            blog_handle=str(config.get("blogHandle") or config.get("blog_handle") or "news"),
            api_version=str(config.get("apiVersion") or config.get("api_version") or settings.shopify_api_version),
            client_id=settings.shopify_client_id,
            client_secret=settings.shopify_client_secret,
        )
        if verify_and_activate:
            tested = await test_shopify_connection(session, site_id=str(site["id"]))
            if not tested["ok"]:
                raise SystemExit(f"Shopify verification failed: {tested.get('error') or tested['status']}")
            result = await activate_shopify_connection(session, site_id=str(site["id"]))
    await engine.dispose()
    suffix = "verified and activated" if verify_and_activate else "run the connection test before activation"
    print(f"Migrated {site_key}: status={result['status']}; {suffix}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-key", required=True)
    parser.add_argument("--verify-and-activate", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(args.site_key, verify_and_activate=args.verify_and_activate))
