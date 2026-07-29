from __future__ import annotations

import httpx
import pytest

from app.connectors.safe_http import (
    BinaryHttpResponse,
    ConnectorHttpError,
    ConnectorResponseTooLarge,
    SafeBinaryHttpClient,
    SafeJsonHttpClient,
)
from app.connectors.custom_data import ConnectorSecurityError


async def public_resolver(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


@pytest.mark.asyncio
async def test_safe_client_returns_json_with_bounded_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer hidden"
        return httpx.Response(200, json={"code": 0, "data": {"list": []}})

    client = SafeJsonHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
    )
    result = await client.request_json(
        method="GET",
        url="https://api.example.com/products",
        headers={"Authorization": "Bearer hidden"},
        allowed_hosts={"api.example.com"},
        max_response_bytes=1024,
        timeout_seconds=5,
    )
    await client.aclose()

    assert result == {"code": 0, "data": {"list": []}}


@pytest.mark.asyncio
async def test_safe_client_rejects_dns_resolution_to_private_address() -> None:
    async def private_resolver(_host: str, _port: int) -> list[str]:
        return ["10.0.0.3"]

    client = SafeJsonHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))),
        resolver=private_resolver,
    )
    with pytest.raises(ConnectorSecurityError, match="public"):
        await client.request_json(
            method="GET",
            url="https://api.example.com/products",
            allowed_hosts={"api.example.com"},
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_safe_client_revalidates_redirect_target_and_blocks_other_host() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example/private"})

    client = SafeJsonHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
    )
    with pytest.raises(ConnectorSecurityError, match="allowlisted"):
        await client.request_json(
            method="GET",
            url="https://api.example.com/products",
            allowed_hosts={"api.example.com"},
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_safe_client_enforces_declared_and_streamed_response_limit() -> None:
    async def declared_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Length": "5000"},
            content=b"{}",
        )

    client = SafeJsonHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(declared_handler)),
        resolver=public_resolver,
    )
    with pytest.raises(ConnectorResponseTooLarge):
        await client.request_json(
            method="GET",
            url="https://api.example.com/products",
            allowed_hosts={"api.example.com"},
            max_response_bytes=100,
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_safe_client_rejects_non_json_and_invalid_json() -> None:
    responses = iter(
        [
            httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html></html>"),
            httpx.Response(200, headers={"Content-Type": "application/json"}, text="not-json"),
        ]
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    client = SafeJsonHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
    )
    kwargs = {
        "method": "GET",
        "url": "https://api.example.com/products",
        "allowed_hosts": {"api.example.com"},
    }
    with pytest.raises(ConnectorHttpError, match="JSON content type"):
        await client.request_json(**kwargs)
    with pytest.raises(ConnectorHttpError, match="invalid JSON"):
        await client.request_json(**kwargs)
    await client.aclose()


@pytest.mark.asyncio
async def test_safe_binary_client_downloads_a_bounded_public_image() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"image-payload"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=png,
        )

    client = SafeBinaryHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
    )
    result = await client.get(
        url="https://cdn.example.com/hero.png",
        allowed_hosts={"cdn.example.com"},
        max_response_bytes=1024,
    )
    await client.aclose()

    assert result == BinaryHttpResponse(
        content=png,
        content_type="image/png",
        final_url="https://cdn.example.com/hero.png",
    )


@pytest.mark.asyncio
async def test_safe_binary_client_revalidates_redirect_and_rejects_non_images() -> None:
    responses = iter(
        [
            httpx.Response(
                302,
                headers={"Location": "https://private.example.com/image.png"},
            ),
            httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                content=b"<html></html>",
            ),
        ]
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    client = SafeBinaryHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
    )
    with pytest.raises(ConnectorSecurityError, match="allowlisted"):
        await client.get(
            url="https://cdn.example.com/hero.png",
            allowed_hosts={"cdn.example.com"},
        )
    with pytest.raises(ConnectorHttpError, match="image content type"):
        await client.get(
            url="https://cdn.example.com/hero.png",
            allowed_hosts={"cdn.example.com"},
        )
    await client.aclose()
