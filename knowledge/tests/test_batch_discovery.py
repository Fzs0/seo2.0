from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from knowledge.backend.app.batch_ingestion import (
    BatchDiscovery,
    BatchIngestionService,
    DiscoveredUrl,
    DiscoveryResult,
)
from knowledge.backend.app.errors import UrlFetchError
from knowledge.backend.app.schemas import BatchSourceSpec
from knowledge.backend.app.web_importer import FetchedResource


def _spec(**overrides) -> BatchSourceSpec:
    values = dict(
        seed_url="https://blog.example.test/blog/",
        source_name="Example blog",
        rights_confirmed=True,
    )
    values.update(overrides)
    return BatchSourceSpec(**values)


def _resource(url: str, content: str, media_type: str) -> FetchedResource:
    return FetchedResource(
        requested_url=url,
        final_url=url,
        media_type=media_type,
        content=content.encode(),
        encoding="utf-8",
    )


class FakeSafeClient:
    def __init__(self, resources: dict[str, FetchedResource]):
        self.resources = resources
        self.requested: list[str] = []

    async def fetch(self, url: str, **_kwargs) -> FetchedResource:
        self.requested.append(url)
        try:
            return self.resources[url]
        except KeyError as exc:
            raise UrlFetchError("not available in fake") from exc


@pytest.mark.asyncio
async def test_sitemap_indexes_are_followed_recursively_and_filtered_to_scope() -> None:
    root_url = "https://blog.example.test/blog/sitemap-index.xml"
    nested_url = "https://blog.example.test/blog/nested.xml"
    leaf_url = "https://blog.example.test/blog/articles.xml"
    root = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>nested.xml</loc></sitemap>
    </sitemapindex>"""
    nested = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>articles.xml</loc></sitemap>
    </sitemapindex>"""
    leaf = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://blog.example.test/blog/in-scope</loc><lastmod>2026-06-02</lastmod></url>
      <url><loc>https://blog.example.test/blog/archive/</loc><lastmod>2026-06-04</lastmod></url>
      <url><loc>https://blog.example.test/blog/category/seo/</loc><lastmod>2026-06-04</lastmod></url>
      <url><loc>https://blog.example.test/outside</loc><lastmod>2026-06-03</lastmod></url>
      <url><loc>https://other.example/blog/cross-domain</loc></url>
    </urlset>"""
    client = FakeSafeClient(
        {
            root_url: _resource(root_url, root, "application/xml"),
            nested_url: _resource(nested_url, nested, "application/xml"),
            leaf_url: _resource(leaf_url, leaf, "application/xml"),
        }
    )

    result = await BatchDiscovery(client).discover(
        _spec(seed_url=root_url, discovery_mode="sitemap")
    )

    assert result.methods == ("sitemap",)
    assert [item.url for item in result.discovered] == [
        "https://blog.example.test/blog/in-scope"
    ]
    assert result.discovered[0].modified_at == datetime(2026, 6, 2, tzinfo=UTC)
    assert nested_url in client.requested
    assert leaf_url in client.requested


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("seed_url", "xml", "expected_url", "expected_date"),
    [
        (
            "https://blog.example.test/blog/rss.xml",
            """<rss version="2.0"><channel><item>
              <link>https://blog.example.test/blog/rss-post</link>
              <pubDate>Tue, 02 Jun 2026 10:00:00 GMT</pubDate>
            </item></channel></rss>""",
            "https://blog.example.test/blog/rss-post",
            datetime(2026, 6, 2, 10, tzinfo=UTC),
        ),
        (
            "https://blog.example.test/blog/atom.xml",
            """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
              <link rel="alternate" href="https://blog.example.test/blog/atom-post" />
              <published>2026-06-03T11:00:00Z</published>
            </entry></feed>""",
            "https://blog.example.test/blog/atom-post",
            datetime(2026, 6, 3, 11, tzinfo=UTC),
        ),
    ],
    ids=["rss", "atom"],
)
async def test_feed_adapter_supports_rss_and_atom(
    seed_url: str, xml: str, expected_url: str, expected_date: datetime
) -> None:
    client = FakeSafeClient(
        {seed_url: _resource(seed_url, xml, "application/rss+xml")}
    )

    result = await BatchDiscovery(client).discover(
        _spec(seed_url=seed_url, discovery_mode="feed")
    )

    assert result.methods == ("feed",)
    assert [(item.url, item.published_at) for item in result.discovered] == [
        (expected_url, expected_date)
    ]


@pytest.mark.asyncio
async def test_direct_feed_seed_uses_parent_blog_scope() -> None:
    seed = "https://blog.example.test/blog/feed/"
    rss = """<rss><channel>
      <item><link>https://blog.example.test/blog/post</link></item>
      <item><link>https://blog.example.test/other/post</link></item>
    </channel></rss>"""
    client = FakeSafeClient(
        {seed: _resource(seed, rss, "application/rss+xml")}
    )

    result = await BatchDiscovery(client).discover(
        _spec(seed_url=seed, discovery_mode="feed")
    )

    assert [item.url for item in result.discovered] == [
        "https://blog.example.test/blog/post"
    ]


@pytest.mark.asyncio
async def test_plain_blog_seed_keeps_discovery_inside_blog_scope() -> None:
    seed = "https://blog.example.test/blog"
    feed = "https://blog.example.test/blog/custom.xml"
    page = f"""<html><head><link rel="alternate" type="application/rss+xml"
      href="{feed}" /></head></html>"""
    rss = """<rss><channel>
      <item><link>https://blog.example.test/blog/accepted</link></item>
      <item><link>https://blog.example.test/other/rejected</link></item>
    </channel></rss>"""
    client = FakeSafeClient(
        {
            seed: _resource(seed, page, "text/html"),
            feed: _resource(feed, rss, "application/rss+xml"),
        }
    )

    result = await BatchDiscovery(client).discover(
        _spec(seed_url=seed, discovery_mode="feed")
    )

    assert [item.url for item in result.discovered] == [
        "https://blog.example.test/blog/accepted"
    ]


@pytest.mark.asyncio
async def test_auto_discovery_uses_html_feed_link() -> None:
    seed = "https://blog.example.test/blog/"
    feed = "https://blog.example.test/blog/custom-feed.xml"
    page = f"""<html><head><link rel="alternate" type="application/atom+xml"
      href="{feed}" /></head><body>Blog</body></html>"""
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <link href="https://blog.example.test/blog/discovered" />
      <updated>2026-07-01T00:00:00Z</updated>
    </entry></feed>"""
    client = FakeSafeClient(
        {
            seed: _resource(seed, page, "text/html"),
            feed: _resource(feed, atom, "application/atom+xml"),
        }
    )

    result = await BatchDiscovery(client).discover(_spec(discovery_mode="auto"))

    assert result.methods == ("feed",)
    assert [item.url for item in result.discovered] == [
        "https://blog.example.test/blog/discovered"
    ]
    assert result.warnings == ("自动发现仅找到 feed 来源",)


@pytest.mark.asyncio
async def test_sitemap_and_feed_merge_dates_for_the_same_canonical_url() -> None:
    seed = "https://blog.example.test/blog/"
    sitemap = "https://blog.example.test/blog/sitemap.xml"
    feed = "https://blog.example.test/blog/feed.xml"
    article = "https://blog.example.test/blog/shared"
    robots = f"Sitemap: {sitemap}\n"
    page = f"""<html><head><link rel="alternate" type="application/rss+xml"
      href="{feed}" /></head></html>"""
    sitemap_xml = f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{article}</loc><lastmod>2026-07-02T00:00:00Z</lastmod></url>
    </urlset>"""
    rss = f"""<rss><channel><item><link>{article}</link>
      <pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate>
    </item></channel></rss>"""
    client = FakeSafeClient(
        {
            "https://blog.example.test/robots.txt": _resource(
                "https://blog.example.test/robots.txt", robots, "text/plain"
            ),
            seed: _resource(seed, page, "text/html"),
            sitemap: _resource(sitemap, sitemap_xml, "application/xml"),
            feed: _resource(feed, rss, "application/rss+xml"),
        }
    )

    result = await BatchDiscovery(client).discover(_spec(discovery_mode="auto"))

    assert result.methods == ("sitemap", "feed")
    assert len(result.discovered) == 1
    merged = result.discovered[0]
    assert merged.url == article
    assert merged.published_at == datetime(2026, 6, 1, tzinfo=UTC)
    assert merged.modified_at == datetime(2026, 7, 2, tzinfo=UTC)


class NoDatabaseSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("preview must not access the database")


class FakeDiscovery:
    def __init__(self, items: tuple[DiscoveredUrl, ...]):
        self.items = items

    async def discover(self, _spec: BatchSourceSpec) -> DiscoveryResult:
        return DiscoveryResult(("sitemap",), self.items)


@pytest.mark.asyncio
async def test_preview_uses_published_or_modified_date_and_never_writes() -> None:
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    items = (
        DiscoveredUrl(
            "https://blog.example.test/blog/new-published",
            published_at=cutoff + timedelta(days=1),
            modified_at=cutoff - timedelta(days=100),
        ),
        DiscoveredUrl(
            "https://blog.example.test/blog/new-modified",
            published_at=cutoff - timedelta(days=100),
            modified_at=cutoff + timedelta(days=2),
        ),
        DiscoveredUrl(
            "https://blog.example.test/blog/old",
            published_at=cutoff - timedelta(days=2),
            modified_at=cutoff - timedelta(days=1),
        ),
        DiscoveredUrl("https://blog.example.test/blog/unknown"),
    )
    service = BatchIngestionService(  # type: ignore[arg-type]
        NoDatabaseSession(), FakeDiscovery(items)
    )

    preview = await service.preview(_spec(date_from=cutoff, max_articles=10))

    assert preview["discovered_total"] == 4
    assert preview["eligible_total"] == 2
    assert preview["unknown_date_total"] == 1
    assert {item["url"] for item in preview["sample_items"]} == {
        "https://blog.example.test/blog/new-published",
        "https://blog.example.test/blog/new-modified",
    }


@pytest.mark.asyncio
async def test_preview_sorts_by_newest_available_date_and_applies_limit() -> None:
    items = (
        DiscoveredUrl(
            "https://blog.example.test/blog/recently-updated-old-post",
            published_at=datetime(2020, 1, 1, tzinfo=UTC),
            modified_at=datetime(2026, 7, 3, tzinfo=UTC),
        ),
        DiscoveredUrl(
            "https://blog.example.test/blog/newer-post",
            published_at=datetime(2026, 7, 2, tzinfo=UTC),
        ),
        DiscoveredUrl(
            "https://blog.example.test/blog/older-post",
            published_at=datetime(2025, 1, 1, tzinfo=UTC),
        ),
    )
    service = BatchIngestionService(  # type: ignore[arg-type]
        NoDatabaseSession(), FakeDiscovery(items)
    )

    preview = await service.preview(_spec(max_articles=2))

    assert preview["selected_total"] == 2
    assert [item["url"] for item in preview["sample_items"]] == [
        "https://blog.example.test/blog/recently-updated-old-post",
        "https://blog.example.test/blog/newer-post",
    ]


@pytest.mark.asyncio
async def test_unknown_dates_require_explicit_opt_in_and_years_filters_old_items() -> None:
    now = datetime.now(UTC)
    items = (
        DiscoveredUrl(
            "https://blog.example.test/blog/recent",
            published_at=now - timedelta(days=300),
        ),
        DiscoveredUrl(
            "https://blog.example.test/blog/old",
            modified_at=now - timedelta(days=900),
        ),
        DiscoveredUrl("https://blog.example.test/blog/unknown"),
    )
    service = BatchIngestionService(  # type: ignore[arg-type]
        NoDatabaseSession(), FakeDiscovery(items)
    )

    excluded = await service.preview(_spec(years=2, include_unknown_dates=False))
    included = await service.preview(_spec(years=2, include_unknown_dates=True))

    assert excluded["eligible_total"] == 1
    assert included["eligible_total"] == 2
