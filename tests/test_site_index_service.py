import gzip

import pytest

from app.services import site_index_service
from app.services.site_index_service import _decode_index_content, _merge_pages, _merge_urls


def test_decode_gzip_index_content():
    source, compressed = _decode_index_content(gzip.compress(b"<urlset />"), "sitemap.xml.gz")

    assert source == "<urlset />"
    assert compressed is True


@pytest.mark.asyncio
async def test_replace_scan_discards_previous_inventory(monkeypatch):
    saved: dict[str, object] = {}

    class Result:
        def mappings(self):
            return self

        def first(self):
            return {
                "id": "site-1",
                "name": "Main",
                "domain": "example.com",
                "base_url": "https://example.com",
                "is_main": True,
                "knowledge_profile": {
                    "index_scan": {
                        "urls": ["https://example.com/old"],
                        "pages": [{"url": "https://example.com/old", "page_type": "page"}],
                        "files": [{"filename": "sitemap.xml", "source": "sitemap"}],
                    },
                    "products": ["Old product"],
                },
            }

    class Session:
        async def execute(self, *_args, **_kwargs):
            return Result()

    async def fake_scan(_urls):
        return [{"url": "https://example.com/new", "page_type": "product", "status": "ok"}]

    async def fake_save(_session, _site_id, profile):
        saved["profile"] = profile
        return profile

    monkeypatch.setattr(site_index_service, "_scan_pages", fake_scan)
    monkeypatch.setattr(site_index_service, "save_site_knowledge", fake_save)

    result = await site_index_service.scan_site_index(
        Session(), "site-1", b"<urlset><url><loc>/new</loc></url></urlset>", replace=True
    )

    assert result["index"]["indexed_urls"] == 1
    assert result["index"]["files"][0]["filename"] == "sitemap.xml"
    assert saved["profile"]["index_scan"]["urls"] == ["https://example.com/new"]
    assert saved["profile"]["products"] == []


def test_merge_pages_keeps_previous_urls_and_latest_duplicate():
    merged = _merge_pages(
        [{"url": "https://exdivo.com/products/old", "title": "old"}],
        [
            {"url": "https://exdivo.com/products/new", "title": "new"},
            {"url": "https://exdivo.com/products/old/", "title": "refreshed"},
        ],
    )

    assert [page["url"] for page in merged] == [
        "https://exdivo.com/products/old",
        "https://exdivo.com/products/new",
    ]
    assert merged[0]["title"] == "refreshed"


def test_merge_urls_keeps_full_index_inventory():
    assert _merge_urls(["https://exdivo.com/products/one/"], ["https://exdivo.com/products/two"]) == [
        "https://exdivo.com/products/one",
        "https://exdivo.com/products/two",
    ]
