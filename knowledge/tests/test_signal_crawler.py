from __future__ import annotations

import httpx
import pytest

from knowledge.backend.app.schemas import SignalCrawlJobIn
from knowledge.backend.app.signal_crawler import NEXT_RUN_SQL, PublicSignalCrawler
from knowledge.backend.app.web_importer import SafeWebClient


PUBLIC_IP = "93.184.216.34"


async def _public_resolver(_host: str, _port: int) -> list[str]:
    return [PUBLIC_IP]


def _job(**overrides: object) -> SignalCrawlJobIn:
    values: dict[str, object] = {
        "name": "Forum demand watch",
        "source_name": "Example forum",
        "platform": "forum",
        "rights_confirmed": True,
        "search_url_template": "https://forum.example.test/search?q={query}&page={page}",
        "keywords": ["content strategy"],
        "detail_url_contains": ["/thread/"],
    }
    values.update(overrides)
    return SignalCrawlJobIn.model_validate(values)


@pytest.mark.asyncio
async def test_public_crawler_finds_threads_and_semantic_comments() -> None:
    requests: list[str] = []
    search_html = """
    <html><body>
      <a href="/thread/42">A useful thread</a>
      <a href="/about">Ignore navigation</a>
    </body></html>
    """
    detail_html = """
    <html><head><title>Content strategy discussion</title></head><body>
      <article id="thread-42">
        <h1>Content strategy discussion</h1>
        <p>People need a repeatable way to turn customer language into useful content topics.</p>
        <div role="article" class="comment" id="comment-7">
          <p>The hardest part is knowing which complaints are real buying signals.</p>
        </div>
      </article>
    </body></html>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        if request.url.path == "/search":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text=search_html,
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text=detail_html,
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        crawler = PublicSignalCrawler(
            SafeWebClient(client=client, resolver=_public_resolver)
        )
        signals, preview = await crawler.collect(_job(max_pages=1))
    finally:
        await client.aclose()

    assert preview.urls == ("https://forum.example.test/thread/42",)
    assert {signal.content_kind for signal in signals} == {"forum_thread", "comment"}
    assert any("/thread/42" in url for url in requests)


def test_crawl_job_requires_a_bounded_search_template() -> None:
    with pytest.raises(ValueError, match=r"must contain \{query\}"):
        _job(search_url_template="https://forum.example.test/search")

    with pytest.raises(ValueError, match=r"must contain \{page\}"):
        _job(max_pages=2, search_url_template="https://forum.example.test/search?q={query}")


def test_next_run_sql_uses_postgres_named_argument() -> None:
    assert NEXT_RUN_SQL == "now()+make_interval(hours => interval_hours)"
