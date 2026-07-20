import pytest

from app.services import post_sync_service
from app.services.post_sync_service import _normalize_openapi, _normalize_shopify, _parse_public_article_html


def test_openapi_article_url_and_cover_are_separate():
    post = _normalize_openapi(
        {
            "id": 2591906,
            "handle": "example-article",
            "detail_url": "https://example.com/blogs/detail/2591906",
            "src": "https://cdn.example.com/cover.png",
        }
    )

    assert post["url"] == "https://example.com/blogs/detail/2591906"
    assert post["cover_url"] == "https://cdn.example.com/cover.png"


def test_shopify_body_is_normalized_to_article_content():
    post = _normalize_shopify(
        {"id": "gid://shopify/Article/1", "title": "Hello", "handle": "hello", "body": "<p>Body</p>"},
        {"base_url": "https://shop.example.com", "api_config": {"blogHandle": "news"}},
    )

    assert post["content_html"] == "<p>Body</p>"
    assert post["url"] == "https://shop.example.com/blogs/news/hello"


def test_public_article_html_parser_extracts_body_and_tdk():
    result = _parse_public_article_html(
        """
        <html><head><title>SEO title</title><meta name='description' content='SEO description'></head>
        <body><nav>Ignore</nav><article class='article-content'><h1>Heading</h1><p>""" + ("Article body " * 10) + """</p></article></body></html>
        """
    )

    assert result["meta_title"] == "SEO title"
    assert result["meta_description"] == "SEO description"
    assert "Article body" in result["content_html"]


@pytest.mark.asyncio
async def test_public_html_fills_missing_content(monkeypatch):
    async def fake_request_text(*_args, **_kwargs):
        return "<article><p>" + ("Article body " * 10) + "</p></article>"

    monkeypatch.setattr(post_sync_service, "request_text", fake_request_text)
    post = {"url": "https://example.com/blog/hello", "content_html": None, "meta_title": "", "meta_description": ""}

    await post_sync_service._enrich_from_public_html(post)

    assert "Article body" in post["content_html"]
    assert post["source"] == "api+public_html"


def test_bulk_sync_can_be_limited_to_one_strategy_business():
    import inspect

    source = inspect.getsource(post_sync_service.sync_all_site_posts)
    assert "strategy_only: bool = False" in source
    assert "business_id = :business_id AND strategy_enabled = true" in source


def test_successful_complete_sync_reconciles_remote_missing_posts():
    import inspect

    source = inspect.getsource(post_sync_service.sync_site_posts)
    assert "len(posts) < limit" in source
    assert "remote_missing" in source
    assert "status = 'canceled'" in source


def test_remote_url_sync_reconciles_linked_article_and_effect():
    import inspect

    source = inspect.getsource(post_sync_service._sync_linked_article_url)
    assert "published_url = :url" in source
    assert "payload = payload || jsonb_build_object('target_url', :url)" in source
    assert "published_post_id = :external_id" in source
