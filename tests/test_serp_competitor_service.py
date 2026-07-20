import pytest

from app.services import serp_competitor_service
from app.services.serp_competitor_service import _parse_competitor_html


def test_competitor_parser_extracts_structure_and_counts():
    result = _parse_competitor_html(
        """
        <html><head><title>Competitor guide</title><meta name='description' content='A guide'></head>
        <body><nav><h2>Menu</h2></nav><article class='article-content'>
        <h1>Best devices</h1><h2>How to choose</h2><p>""" + ("Useful content " * 80) + """</p>
        <ul><li>One</li></ul><table><tr><td>Compare</td></tr></table><img src='x.jpg'><a href='/internal'>Internal</a><a href='https://other.example/x'>External</a>
        </article></body></html>
        """,
        "https://example.com/guide",
    )

    assert result["meta_title"] == "Competitor guide"
    assert [heading["text"] for heading in result["headings"]] == ["Best devices", "How to choose"]
    assert result["word_count"] > 100
    assert result["list_count"] == 1
    assert result["table_count"] == 1
    assert result["internal_link_count"] == 1
    assert result["external_link_count"] == 1


@pytest.mark.asyncio
async def test_fetch_competitor_pages_uses_serp_urls(monkeypatch):
    async def fake_request_text(*_args, **_kwargs):
        return "<article><h1>Guide</h1><p>" + ("Body " * 80) + "</p></article>"

    monkeypatch.setattr(serp_competitor_service, "request_text", fake_request_text)
    pages = await serp_competitor_service.fetch_competitor_pages(
        [{"position": 1, "link": "https://example.com/guide", "title": "Guide"}],
        limit=5,
    )

    assert len(pages) == 1
    assert pages[0]["status"] == "fetched"
    assert pages[0]["url"] == "https://example.com/guide"
    assert pages[0]["heading_counts"]["h1"] == 1
