from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from knowledge.backend.app.errors import UrlFetchError
from knowledge.backend.app.web_importer import (
    MAX_RESPONSE_BYTES,
    FetchedArticle,
    WebArticleFetcher,
    resolve_public_addresses,
)


PUBLIC_IP = "93.184.216.34"
LONG_BODY = (
    "Search intent describes the result format and purpose people expect. "
    "Review the current results before drafting, and preserve direct evidence "
    "for every recommendation. "
) * 3


async def _public_resolver(_host: str, _port: int) -> list[str]:
    return [PUBLIC_IP]


@pytest.mark.asyncio
async def test_public_ipv4_is_preferred_over_non_public_ipv6_sinkhole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Loop:
        async def getaddrinfo(self, _host: str, _port: int, *, family: int, type: int):
            if family == socket.AF_INET:
                return [(socket.AF_INET, type, 6, "", (PUBLIC_IP, 443))]
            return [(socket.AF_INET6, type, 6, "", ("2001::1", 443, 0, 0))]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: Loop())
    assert await resolve_public_addresses("example.test", 443) == [PUBLIC_IP]


def _html(*, title: str = "Search Intent Guide", body: str = LONG_BODY) -> str:
    return (
        '<!doctype html><html lang="en"><head>'
        f"<title>{title}</title></head><body><main><article><p>{body}</p>"
        "</article></main></body></html>"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://example.test/guide", "https://example.test/guide"])
async def test_public_http_pages_return_title_and_article_content(url: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=_html(),
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        article = await WebArticleFetcher(
            client=client, resolver=_public_resolver
        ).fetch(url)
    finally:
        await client.aclose()

    assert isinstance(article, FetchedArticle)
    assert article.requested_url == url
    assert article.final_url == url
    assert article.title == "Search Intent Guide"
    assert "Search intent describes the result format" in article.raw_content
    assert article.language_code == "en"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/article",
        "http://127.0.0.1/article",
        "http://10.0.0.8/article",
        "http://192.168.1.8/article",
        "http://169.254.169.254/latest/meta-data",
        "http://192.0.2.10/article",
        "http://[::1]/article",
    ],
)
async def test_local_private_and_reserved_targets_are_blocked_before_http(
    url: str,
) -> None:
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, text=_html())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError):
            await WebArticleFetcher(
                client=client, resolver=_public_resolver
            ).fetch(url)
    finally:
        await client.aclose()

    assert requests == 0


@pytest.mark.asyncio
async def test_dns_resolution_to_a_private_address_is_blocked_before_http() -> None:
    requests = 0

    async def private_resolver(_host: str, _port: int) -> list[str]:
        return ["10.12.0.9"]

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, text=_html())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError):
            await WebArticleFetcher(
                client=client, resolver=private_resolver
            ).fetch("https://public-name.example/article")
    finally:
        await client.aclose()

    assert requests == 0


@pytest.mark.asyncio
async def test_each_redirect_target_is_resolved_and_revalidated() -> None:
    resolutions: list[tuple[str, int]] = []
    requests: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        resolutions.append((host, port))
        return ["10.0.0.4"] if host == "internal.example" else [PUBLIC_IP]

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://internal.example/private"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError):
            await WebArticleFetcher(client=client, resolver=resolver).fetch(
                "http://origin.example/article"
            )
    finally:
        await client.aclose()

    assert resolutions == [("origin.example", 80), ("internal.example", 443)]
    assert requests == ["http://origin.example/article"]


@pytest.mark.asyncio
async def test_non_html_response_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"content": LONG_BODY},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError):
            await WebArticleFetcher(
                client=client, resolver=_public_resolver
            ).fetch("https://example.test/data")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_response_over_the_byte_limit_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(MAX_RESPONSE_BYTES + 1),
            },
            content=b"ignored",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError):
            await WebArticleFetcher(
                client=client, resolver=_public_resolver
            ).fetch("https://example.test/huge")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_timeout_is_reported_as_a_fetch_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError):
            await WebArticleFetcher(
                client=client, resolver=_public_resolver
            ).fetch("https://example.test/slow")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_page_with_too_little_article_text_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text=_html(body="Too short."),
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UrlFetchError):
            await WebArticleFetcher(
                client=client, resolver=_public_resolver
            ).fetch("https://example.test/thin")
    finally:
        await client.aclose()
