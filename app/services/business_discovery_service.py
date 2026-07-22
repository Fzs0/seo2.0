"""Discover a draft business profile from an existing site without publishing content."""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.http_client import ExternalCallError, request_bytes
from app.services.site_index_service import scan_site_index
from app.services.site_knowledge_service import generate_site_knowledge
from app.services.site_service import get_site


async def discover_business_from_site(session: AsyncSession, site_id: str) -> dict[str, Any]:
    """Scan a site's public sitemap and turn local evidence into a draft profile.

    The interface intentionally takes only a site id.  It hides sitemap
    resolution, read-only page scanning and AI profile drafting behind one
    operation.  It never enables a business, creates strategies or publishes.
    """
    site = await get_site(session, site_id)
    if not site:
        raise ValueError("site not found")
    base_url = str(site.get("base_url") or site.get("domain") or "").strip()
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("站点缺少可访问的 base_url 或 domain")

    sitemap_url = urljoin(base_url.rstrip("/") + "/", "sitemap.xml")
    try:
        sitemap_content = await request_bytes(
            "GET",
            sitemap_url,
            client_label="business_discovery_sitemap",
            headers={"Accept": "application/xml,text/xml,application/gzip,*/*"},
            timeout=20,
            max_attempts=1,
        )
    except ExternalCallError as error:
        raise ValueError(f"无法读取站点 sitemap：{error}") from error

    index_result = await scan_site_index(
        session,
        site_id,
        sitemap_content,
        filename="sitemap.xml（业务识别自动扫描）",
        replace=True,
        persist=False,
        additional_allowed_hosts=_declared_sitemap_hosts(sitemap_content),
    )
    knowledge_result = await generate_site_knowledge(
        session,
        site_id,
        profile_override=index_result["knowledge_profile"],
        persist=False,
    )
    evidence_counts = await _evidence_counts(session, site_id)
    candidate_targets = _candidate_targets(index_result["knowledge_profile"])
    profile = _apply_safety_floor(knowledge_result["knowledge_profile"], site, candidate_targets)
    return {
        "site": _site_summary(site),
        "sitemap_url": sitemap_url,
        "scan": {
            "indexed_urls": index_result["index"]["indexed_urls"],
            "scanned_urls": index_result["index"]["scanned_urls"],
            "issues": index_result["seo_audit"]["summary"]["issues"],
            "product_hints": index_result["seo_audit"]["product_hints"][:12],
        },
        "evidence": evidence_counts,
        "candidate_targets": candidate_targets,
        "knowledge_profile": profile,
        "recommended_next_step": _next_step(profile),
    }


async def _evidence_counts(session: AsyncSession, site_id: str) -> dict[str, int]:
    posts = await session.execute(
        text("SELECT count(*) FROM seo_agent.posts WHERE site_id = CAST(:site_id AS uuid) AND COALESCE(status, '') <> 'remote_missing'"),
        {"site_id": site_id},
    )
    products = await session.execute(
        text("SELECT count(*) FROM seo_agent.products WHERE site_id = CAST(:site_id AS uuid)"),
        {"site_id": site_id},
    )
    return {"existing_posts": int(posts.scalar_one() or 0), "product_records": int(products.scalar_one() or 0)}


def _site_summary(site: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(site.get("id") or ""),
        "name": site.get("name") or "",
        "site_key": site.get("site_key") or "",
        "site_type": site.get("site_type") or "",
        "base_url": site.get("base_url") or site.get("domain") or "",
    }


def _next_step(profile: dict[str, Any]) -> str:
    policy = profile.get("generation_policy") if isinstance(profile.get("generation_policy"), dict) else {}
    risk_level = policy.get("risk_level") or "standard"
    if risk_level in {"regulated", "ymyl"}:
        return "请确认 AI 识别的范围，并补充允许来源；高风险业务不会自动启用策略。"
    return "请确认业务 ID、目标市场和 AI 草案；确认后先做一篇不落库的文章测试。"


def _candidate_targets(index_profile: dict[str, Any]) -> list[dict[str, Any]]:
    index_scan = index_profile.get("index_scan") if isinstance(index_profile.get("index_scan"), dict) else {}
    pages = index_scan.get("pages") if isinstance(index_scan.get("pages"), list) else []
    targets = []
    for page in pages:
        if not isinstance(page, dict) or page.get("page_type") not in {"product", "service", "category"}:
            continue
        url = str(page.get("url") or "").strip()
        if not url:
            continue
        title = _clean_page_title(page.get("title"))
        # Do not elevate arbitrary product-page body copy into a verified fact.
        # A user can select this target, but medical/efficacy claims still need
        # approved sources before article generation is allowed.
        facts = _unique_text([title, *(page.get("h1") or [])])
        targets.append({
            "url": url,
            "type": page.get("page_type"),
            "title": title,
            "facts": facts[:4],
        })
    return targets[:30]


def _apply_safety_floor(profile: dict[str, Any], site: dict[str, Any], candidate_targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Never let a scan under-classify a commercial health-related site."""
    result = dict(profile)
    policy = dict(result.get("generation_policy") or {})
    role = str(policy.get("site_role") or "").strip()
    if not role and (site.get("site_type") == "shopify" or candidate_targets):
        policy["site_role"] = "commercial"
    corpus = " ".join(_unique_text([
        result.get("positioning"), *result.get("products", []), *result.get("in_scope_topics", []),
        *(item.get("title") for item in candidate_targets),
    ])).casefold()
    health_terms = ("oxygen concentrator", "oxygen therapy", "medical device", "respiratory", "patient")
    if any(term in corpus for term in health_terms):
        current = str(policy.get("risk_level") or "standard")
        if current in {"", "low", "standard"}:
            policy["risk_level"] = "regulated"
        triggers = dict(policy.get("keyword_triggers") or {})
        triggers["health"] = _unique_text([*(triggers.get("health") or []), *health_terms])
        policy["keyword_triggers"] = triggers
        policy["claim_terms"] = _unique_text([*(policy.get("claim_terms") or []), "oxygen therapy", "medical device", "respiratory"])
        policy["forbidden_claims"] = _unique_text([*(policy.get("forbidden_claims") or []), "Do not provide diagnosis, treatment, dosage, safety, or efficacy claims without approved sources."])
    result["generation_policy"] = policy
    return result


def _unique_text(values: list[Any]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        text_value = str(value or "").strip()
        if text_value and text_value.casefold() not in seen:
            items.append(text_value)
            seen.add(text_value.casefold())
    return items


def _clean_page_title(value: Any) -> str:
    title = str(value or "").strip()
    # Shopify themes sometimes append payment-provider labels after the store
    # name.  They are navigation chrome, not a product name.
    title = re.split(r"\s+[–—|-]\s+[^–—|-]*(?:Visa|Mastercard|Shop Pay|Apple Pay|Google Pay)", title, maxsplit=1, flags=re.I)[0]
    title = re.split(r"\s+[–—|-]\s+healthyoxy\s*$", title, maxsplit=1, flags=re.I)[0]
    return title.strip()


def _declared_sitemap_hosts(content: bytes) -> set[str]:
    """Trust only public hosts explicitly named by the store's root sitemap.

    Shopify can serve a root sitemap from ``*.myshopify.com`` while listing
    product sitemaps on the store's primary custom domain.  The normal manual
    sitemap scanner stays single-host; this narrow exception is for automatic
    discovery after the configured store has supplied those declarations.
    """
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return set()
    hosts = set()
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "loc":
            continue
        value = "".join(node.itertext()).strip()
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname:
            hosts.add(parsed.hostname.casefold())
    return hosts
