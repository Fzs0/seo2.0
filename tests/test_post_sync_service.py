import pytest
from datetime import datetime, timezone

from app.services import post_sync_service
from app.services.post_sync_service import _normalize_openapi, _normalize_shopify, _parse_public_article_html


@pytest.mark.parametrize(
    ("base_url", "detail_url", "canonical_url"),
    [
        (
            "https://exdivo.com",
            "https://exdivo.com/blogs/detail/2591906",
            "https://exdivo.com/blogs/example-article",
        ),
        (
            "https://avinoti.shop",
            "https://avinoti.shop/blogs/detail/2591906",
            "https://avinoti.shop/blogs/example-article",
        ),
    ],
)
def test_oemapps_main_article_sync_uses_canonical_slug_url(
    base_url: str,
    detail_url: str,
    canonical_url: str,
):
    post = _normalize_openapi(
        {
            "id": 2591906,
            "handle": "example-article",
            "detail_url": detail_url,
            "src": "https://cdn.example.com/cover.png",
        },
        {
            "site_type": "main",
            "base_url": base_url,
            "api_base_url": "https://openapi.oemapps.com",
            "api_config": {"articleUrlPath": "/blogs/detail/{id}"},
        },
    )

    assert post["url"] == canonical_url
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


@pytest.mark.asyncio
async def test_site_sync_invalidates_update_effect_baseline_after_canonical_url_change(
    monkeypatch,
):
    effect_writes: list[dict] = []

    class Result:
        def __init__(self, rows=()):
            self.rows = list(rows)

        def mappings(self):
            return self

        def first(self):
            return self.rows[0] if self.rows else None

        def one(self):
            return self.rows[0]

        def all(self):
            return self.rows

    class Session:
        committed = False

        async def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "FROM seo_agent.sites WHERE id" in sql:
                return Result([{
                    "id": "site-id",
                    "site_key": "avinoti",
                    "name": "avinoti",
                    "site_type": "main",
                    "domain": "avinoti.shop",
                    "base_url": "https://avinoti.shop",
                    "api_base_url": "https://openapi.oemapps.com",
                    "market": "US",
                    "language_code": "en",
                    "api_config": {},
                }])
            if "INSERT INTO seo_agent.posts" in sql:
                return Result([{
                    "id": "post-id",
                    "fetched_at": datetime(2026, 7, 26, tzinfo=timezone.utc),
                }])
            if "WITH linked AS" in sql and "UPDATE seo_agent.articles article" in sql:
                return Result([{"id": "article-id"}])
            if "payload->>'kind' = 'strategy_effect'" in sql and sql.lstrip().startswith("SELECT"):
                return Result([{
                    "id": "effect-id",
                    "target_url": "https://avinoti.shop/blogs/detail/42",
                    "payload": {
                        "kind": "strategy_effect",
                        "action": "update_article",
                        "target_url": "https://avinoti.shop/blogs/detail/42",
                        "baseline": {"gsc": {"clicks": 10}},
                    },
                }])
            if sql.lstrip().startswith("UPDATE seo_agent.tasks") and params.get("payload"):
                effect_writes.append(params)
            return Result()

        async def commit(self):
            self.committed = True

    class Connector:
        connector_type = "custom_openapi"

        async def read_articles(self, limit=1):
            return [{
                "id": 42,
                "handle": "guide",
                "detail_url": "https://avinoti.shop/blogs/detail/42",
                "content_html": "<article>Complete article</article>",
                "meta_title": "Guide",
                "meta_description": "Description",
                "status": 1,
            }]

    async def runtime_connector(*_args, **_kwargs):
        return Connector()

    async def no_analysis(*_args, **_kwargs):
        return None

    monkeypatch.setattr(post_sync_service, "publisher_for_site_runtime", runtime_connector)
    monkeypatch.setattr(post_sync_service, "persist_post_analysis", no_analysis)
    session = Session()

    result = await post_sync_service.sync_site_posts(
        session,  # type: ignore[arg-type]
        site_id="site-id",
        limit=100,
    )

    assert result["ok"] is True
    assert session.committed is True
    assert len(effect_writes) == 1
    payload = __import__("json").loads(effect_writes[0]["payload"])
    assert payload["target_url"] == "https://avinoti.shop/blogs/guide"
    assert payload["baseline_valid"] is False
    assert payload["previous_target_url"] == "https://avinoti.shop/blogs/detail/42"
