import pytest

from app.services import business_discovery_service


class _Result:
    def __init__(self, value: int):
        self.value = value

    def scalar_one(self):
        return self.value


class _Session:
    def __init__(self):
        self.values = iter([4, 0])

    async def execute(self, *_args, **_kwargs):
        return _Result(next(self.values))


def test_candidate_target_strips_shopify_payment_chrome_and_excludes_description_claims():
    targets = business_discovery_service._candidate_targets({
        "index_scan": {"pages": [{
            "url": "https://shop.example/products/one", "page_type": "product",
            "title": "Portable concentrator – healthyoxy Visa Mastercard Shop Pay",
            "h1": ["Portable concentrator"],
            "description": "Claims it treats respiratory conditions.",
        }]}
    })

    assert targets == [{
        "url": "https://shop.example/products/one", "type": "product",
        "title": "Portable concentrator", "facts": ["Portable concentrator"],
    }]


@pytest.mark.asyncio
async def test_discover_business_scans_public_sitemap_and_returns_draft(monkeypatch):
    async def fake_site(_session, _site_id):
        return {
            "id": "site-1", "name": "HealthyOxy", "site_key": "healthyoxy-shopify",
            "site_type": "shopify", "base_url": "https://healthyoxy.example",
        }

    received = {}

    async def fake_request(method, url, **_kwargs):
        received["request"] = (method, url)
        return b"<urlset></urlset>"

    async def fake_index(_session, site_id, content, filename, **kwargs):
        received["index"] = (site_id, content, filename)
        assert kwargs["persist"] is False
        assert kwargs["additional_allowed_hosts"] == set()
        return {
            "knowledge_profile": {"products": ["Portable concentrator"], "index_scan": {}},
            "index": {"indexed_urls": 12, "scanned_urls": 12},
            "seo_audit": {"summary": {"issues": 2}, "product_hints": ["Portable concentrator"]},
        }

    async def fake_knowledge(_session, _site_id, **kwargs):
        assert kwargs["persist"] is False
        assert kwargs["profile_override"]["products"] == ["Portable concentrator"]
        return {"knowledge_profile": {"status": "draft", "positioning": "Oxygen equipment", "generation_policy": {"risk_level": "ymyl"}}}

    monkeypatch.setattr(business_discovery_service, "get_site", fake_site)
    monkeypatch.setattr(business_discovery_service, "request_bytes", fake_request)
    monkeypatch.setattr(business_discovery_service, "scan_site_index", fake_index)
    monkeypatch.setattr(business_discovery_service, "generate_site_knowledge", fake_knowledge)

    result = await business_discovery_service.discover_business_from_site(_Session(), "site-1")

    assert received["request"] == ("GET", "https://healthyoxy.example/sitemap.xml")
    assert received["index"][2] == "sitemap.xml（业务识别自动扫描）"
    assert result["scan"]["product_hints"] == ["Portable concentrator"]
    assert result["evidence"] == {"existing_posts": 4, "product_records": 0}
    assert "高风险业务" in result["recommended_next_step"]
