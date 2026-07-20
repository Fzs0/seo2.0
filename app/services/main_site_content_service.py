"""主站电商内容分层：产品/分类页承接商业意图，文章作为支持层。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.site_index_service import _page_type


PAGE_ROLES = {
    "product": ("产品页", "承接产品名、型号、规格、价格和购买意图"),
    "category": ("分类页", "承接品类、系列、用途和筛选意图"),
    "article": ("支持文章", "解释问题、比较和使用场景，并链接到产品页或分类页"),
    "home": ("品牌入口", "分发品牌、重点分类和重点产品，不作为普通文章目标"),
}


async def get_main_site_content_plan(session: AsyncSession, site_id: str) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT id, name, site_type, is_main, knowledge_profile "
                "FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
            ),
            {"id": site_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("site not found")
    if not row["is_main"] and row["site_type"] != "main":
        raise ValueError("主站内容规划只允许主站")

    profile = row["knowledge_profile"] if isinstance(row["knowledge_profile"], dict) else {}
    index = profile.get("index_scan") if isinstance(profile.get("index_scan"), dict) else {}
    pages = index.get("pages") if isinstance(index.get("pages"), list) else []
    indexed_urls = index.get("urls") if isinstance(index.get("urls"), list) else []
    known = {str(page.get("url") or "").rstrip("/"): page for page in pages if isinstance(page, dict) and page.get("url")}
    inventory = []
    for url in indexed_urls:
        normalized = str(url).strip().rstrip("/")
        if not normalized:
            continue
        page = known.get(normalized, {"url": normalized})
        page_type = page.get("page_type") or _page_type(normalized)
        label, recommendation = PAGE_ROLES.get(page_type, ("其他页面", "先作为页面库存保留，不自动生成内容"))
        inventory.append(
            {
                "url": normalized,
                "page_type": page_type,
                "role": label,
                "recommendation": recommendation,
                "title": page.get("title") or "",
                "h1": page.get("h1") or [],
                "status": page.get("status") or "indexed_only",
            }
        )

    counts = {key: sum(item["page_type"] == key for item in inventory) for key in PAGE_ROLES}
    counts["other"] = len(inventory) - sum(counts.values())
    return {
        "site_id": str(row["id"]),
        "site_name": row["name"],
        "indexed_urls": len(inventory),
        "scanned_pages": len(pages),
        "unscanned_urls": max(0, len(inventory) - len(pages)),
        "counts": counts,
        "commercial_targets": [item for item in inventory if item["page_type"] in {"product", "category"}],
        "supporting_articles": [item for item in inventory if item["page_type"] == "article"],
        "inventory": inventory,
        "rules": [
            "产品页是单个产品的主要商业承接页；不要用博客文章替代真实产品页。",
            "分类页承接品类或系列意图，并应直接链接到希望收录的产品页。",
            "文章必须绑定一个产品页或分类页作为转化目标，不能只追求无关流量。",
            "产品名称、规格、价格、库存、配送和限制声明只能来自真实产品数据。",
            "筛选、排序和参数 URL 默认不作为独立内容目标，除非有明确搜索需求和独立价值。",
            "产品页优先使用 Product / Offer 数据，文章和分类页不冒充单一可购买产品。",
        ],
        "references": [
            "https://developers.google.com/search/docs/specialty/ecommerce/help-google-understand-your-ecommerce-site-structure",
            "https://developers.google.com/search/docs/appearance/structured-data/merchant-listing",
            "https://developers.google.com/search/docs/specialty/ecommerce/pagination-and-incremental-page-loading",
        ],
    }
