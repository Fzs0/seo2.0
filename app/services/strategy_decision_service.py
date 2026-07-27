"""Pure decision helpers for non-article SEO actions.

This module turns already-collected page audit evidence into strategy
candidates.  It does not read or write the database and cannot publish.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_effect_service import normalize_canonical_url


_SUPPORTED_PAGE_TYPES = {"home", "product", "category"}
_POLICY_VERSION = "strategy_policy_v1"


def build_keywordless_new_article_context(
    decision: dict[str, Any], *, site: dict[str, Any]
) -> dict[str, Any]:
    """Validate and adapt a keywordless, evidence-backed new-article decision."""
    if decision.get("strategy_type") != "new_article":
        raise ValueError("keywordless context is only valid for new articles")
    query = str(decision.get("query") or "").strip()
    if not query:
        raise ValueError("keywordless new article is missing its topic query")
    if not site.get("id") or not site.get("business_id"):
        raise ValueError("target site is incomplete")
    if decision.get("business_id") and decision["business_id"] != site["business_id"]:
        raise ValueError("target site does not belong to the strategy business")
    if not site.get("market") or not site.get("language_code"):
        raise ValueError("target site market and language are required")
    if decision.get("risk_gate_passed") is not True:
        raise ValueError("risk gate has not passed")

    evidence = decision.get("execution_evidence") or {}
    intent = evidence.get("search_intent") or {}
    if intent.get("status") != "confirmed":
        raise ValueError("search intent is not confirmed")
    topic = evidence.get("topic_basis") or {}
    if not topic.get("product_or_category_match") or not topic.get("content_gap"):
        raise ValueError("topic basis is incomplete")
    if (evidence.get("cannibalization") or {}).get("status") not in {"clear", "none", "low"}:
        raise ValueError("cannibalization or duplicate risk is unresolved")
    sources = [
        str(source).strip()
        for source in evidence.get("evidence_sources") or []
        if str(source).strip()
    ]
    if not sources:
        raise ValueError("at least one evidence source is required")
    if not evidence.get("product_facts"):
        raise ValueError("verified product facts are required")
    if str(decision.get("risk_level") or "standard") in {"regulated", "ymyl"}:
        if not evidence.get("authority_sources"):
            raise ValueError("high-risk content requires authoritative sources")

    return {
        "id": None,
        "keyword": query,
        "business_id": str(site["business_id"]),
        "assigned_site_id": str(site["id"]),
        "assigned_site_label": str(site.get("name") or ""),
        "market": str(site["market"]),
        "language_code": str(site["language_code"]),
        "evidence_sources": sources,
        "evidence_snapshot": dict(evidence.get("snapshots") or {}),
        "execution_evidence": evidence,
    }


def build_on_page_candidates(
    *, site: dict[str, Any], assets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return safe, addressable on-page candidates from existing audits."""
    site_id = str(site.get("id") or "").strip()
    business_id = str(site.get("business_id") or "").strip()
    candidates: list[dict[str, Any]] = []
    seen_identities: set[str] = set()

    for asset in assets:
        page_type = str(asset.get("page_type") or "").strip().casefold()
        raw_url = str(asset.get("url") or asset.get("canonical_url") or "").strip()
        audit = asset.get("seo_audit")
        if page_type not in _SUPPORTED_PAGE_TYPES or not raw_url or not isinstance(audit, dict):
            continue
        try:
            url = normalize_canonical_url(
                raw_url,
                site_url=site.get("base_url"),
                site_domain=site.get("domain"),
            )
        except ValueError:
            continue

        for subtype, evidence in _actionable_issues(audit):
            identity = f"{site_id}|{url}|{page_type}|{subtype}"
            if identity in seen_identities:
                continue
            seen_identities.add(identity)
            scope_key = hashlib.sha256(f"{site_id}|{url}".encode()).hexdigest()
            strategy_fingerprint = hashlib.sha256(identity.encode()).hexdigest()
            evidence_fingerprint = hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            candidates.append(
                {
                    "id": f"on-page-{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
                    "business_id": business_id,
                    "site_id": site_id,
                    "site_name": site.get("name") or "",
                    "strategy_type": "on_page_fix",
                    "page_type": page_type,
                    "subtype": subtype,
                    "target_url": url,
                    "target_asset_id": str(asset.get("id") or ""),
                    "title": f"修复{_page_type_label(page_type)} SEO：{url}",
                    "query": url,
                    "priority": "P1" if subtype in {"meta", "alt"} else "P2",
                    "score": 70 if subtype == "meta" else 65 if subtype == "alt" else 55,
                    "opportunity_score": 70 if subtype == "meta" else 65 if subtype == "alt" else 55,
                    "readiness_score": 0.9,
                    "risk_score": 0.05,
                    "risk_gate_passed": True,
                    "days_since_last_action": int(asset.get("days_since_last_action") or 0),
                    "confidence": 0.9,
                    "evidence_level": "confirmed",
                    "reason": _reason_for(subtype, evidence),
                    "recommended_action": f"执行 {_page_type_label(page_type)} {subtype} 修复",
                    "evidence": evidence,
                    "candidate_status": "available",
                    "scope_key": scope_key,
                    "lock_scope": "url",
                    "lock_key": scope_key,
                    "strategy_fingerprint": strategy_fingerprint,
                    "evidence_fingerprint": evidence_fingerprint,
                    "policy_version": _POLICY_VERSION,
                    "requires_publish": False,
                }
            )
    return candidates


async def load_on_page_assets(
    session: AsyncSession, *, business_id: str
) -> dict[str, list[dict[str, Any]]]:
    """Load existing read-only SEO audit snapshots grouped by site."""
    rows = await session.execute(
        text(
            """
            SELECT site_id::text AS site_id, id::text AS id, 'product' AS page_type,
                   COALESCE(NULLIF(url, ''), NULLIF(canonical_url, '')) AS url, seo_audit
              FROM seo_agent.products
             WHERE site_id IN (
                    SELECT id FROM seo_agent.sites
                     WHERE business_id = :business_id AND status = 'active'
             )
            UNION ALL
            SELECT site_id::text, id::text, 'category', NULLIF(url, ''), seo_audit
              FROM seo_agent.product_collections
             WHERE site_id IN (
                    SELECT id FROM seo_agent.sites
                     WHERE business_id = :business_id AND status = 'active'
             )
            UNION ALL
            SELECT h.site_id::text, h.id::text, 'home',
                   COALESCE(NULLIF(s.base_url, ''), NULLIF(s.domain, '')), h.seo_audit
              FROM seo_agent.site_home_seo h
              JOIN seo_agent.sites s ON s.id = h.site_id
             WHERE s.business_id = :business_id AND s.status = 'active'
            """
        ),
        {"business_id": business_id},
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows.mappings().all():
        item = dict(row)
        grouped.setdefault(str(item.pop("site_id")), []).append(item)
    return grouped


def _actionable_issues(audit: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    issues: list[tuple[str, dict[str, Any]]] = []
    missing_tdk = {
        str(item).strip().casefold()
        for item in audit.get("missing_tdk", [])
        if str(item).strip()
    }
    if (
        missing_tdk.intersection({"meta_title", "meta_description"})
        or audit.get("missing_meta_title") is True
        or audit.get("missing_meta_description") is True
    ):
        fields = sorted(
            missing_tdk.intersection({"meta_title", "meta_description"})
            or {
                name
                for name in ("meta_title", "meta_description")
                if audit.get(f"missing_{name}") is True
            }
        )
        issues.append(("meta", {"missing_fields": fields}))

    missing_alt = audit.get("images_missing_alt")
    if isinstance(missing_alt, int) and not isinstance(missing_alt, bool) and missing_alt > 0:
        issues.append(("alt", {"images_missing_alt": missing_alt}))

    if audit.get("missing_internal_links") is True or audit.get("internal_link_count") == 0:
        issues.append(("internal_link", {"internal_link_count": 0}))

    return issues


def _page_type_label(page_type: str) -> str:
    return {"home": "首页", "product": "产品页", "category": "分类页"}[page_type]


def _reason_for(subtype: str, evidence: dict[str, Any]) -> str:
    if subtype == "meta":
        return f"已确认缺少 SEO 字段：{', '.join(evidence['missing_fields'])}"
    if subtype == "alt":
        return f"已确认有 {evidence['images_missing_alt']} 张图片缺少 ALT"
    return "已确认页面缺少内部链接"


__all__ = [
    "build_keywordless_new_article_context",
    "build_on_page_candidates",
    "load_on_page_assets",
]
