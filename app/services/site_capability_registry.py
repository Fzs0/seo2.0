"""Machine-readable capability contracts.

This module contains declarations only.  Capability discovery must never probe a
remote system in order to learn whether an operation is safe.
"""
from __future__ import annotations

from typing import Any

from app.core.article_urls import is_oemapps_site


FORBIDDEN_ACTIONS = {
    "delete_content": "forbidden",
    "redirect": "forbidden",
    "change_slug": "forbidden",
    "change_canonical": "forbidden",
    "change_price": "forbidden",
    "change_inventory": "forbidden",
    "change_variants": "forbidden",
    "change_category_membership": "forbidden",
}

OEMAPPS_CONTRACT: dict[str, Any] = {
    "connector_capabilities": {
        "products": {"read": True, "write": True},
        "collections": {"read": True, "write": True},
        "homepage": {"read": True, "write": True},
    },
    "supported_actions": {
        "product_seo": "approval_required",
        "product_image_alt": "approval_required",
        "category_seo": "approval_required",
        "homepage_seo": "approval_required",
    },
    "supported_fields": {
        "product_seo": ["meta_title", "meta_description", "meta_keywords", "image_alts"],
        "category_seo": ["meta_title", "meta_description", "meta_keywords"],
        "homepage_seo": ["title", "meta_title", "meta_description", "meta_keywords"],
    },
    "side_effects": {
        "product_seo": {
            "variant_recreation_possible": True,
            "requires_variant_confirmation": True,
        },
        "category_seo": {
            "membership_reset_possible": True,
            "requires_membership_confirmation": True,
        },
        "homepage_seo": {"requires_explicit_confirmation": True},
    },
}

ARTICLE_CONTRACT: dict[str, Any] = {
    "connector_capabilities": {"articles": {"read": True, "write": True}},
    "supported_actions": {
        "new_article": "approval_required",
        "update_article": "approval_required",
    },
    "supported_fields": {
        "articles": [
            "title",
            "body",
            "meta_title",
            "meta_description",
        ]
    },
    "side_effects": {},
}


def contract_for_site(site: dict[str, Any], adapters: set[str]) -> dict[str, Any]:
    """Return the conservative contract supported by current implementation."""
    contracts: list[dict[str, Any]] = []
    if "oemapps" in adapters:
        contracts.append(OEMAPPS_CONTRACT)
    connector_type = str((site.get("api_config") or {}).get("connector_type") or "").casefold()
    if is_oemapps_site(site) or site.get("site_type") in {"wp", "blog", "shopify"} or connector_type in {
        "wp",
        "wordpress",
        "shopify",
        "shopify_admin",
        "custom_openapi",
    }:
        contracts.append(ARTICLE_CONTRACT)

    merged: dict[str, Any] = {
        "connector_capabilities": {},
        "supported_actions": dict(FORBIDDEN_ACTIONS),
        "supported_fields": {},
        "side_effects": {},
    }
    for contract in contracts:
        for key in merged:
            merged[key].update(contract.get(key) or {})
    if merged["supported_fields"].get("articles") and is_oemapps_site(site):
        merged["supported_fields"]["articles"] = [
            *merged["supported_fields"]["articles"],
            "images",
            "image_alts",
        ]
    return merged


__all__ = ["FORBIDDEN_ACTIONS", "contract_for_site"]
