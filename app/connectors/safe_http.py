"""Bounded HTTPS adapter for user-configured read-only connectors."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.connectors.custom_data import ConnectorSecurityError


Resolver = Callable[[str, int], Awaitable[list[str]]]


class ConnectorHttpError(RuntimeError):
    """A safe, credential-free connector transport error."""


class ConnectorResponseTooLarge(ConnectorHttpError):
    """The decoded response exceeded its configured byte budget."""


@dataclass(frozen=True)
class BinaryHttpResponse:
    content: bytes
    content_type: str
    final_url: str


async def resolve_host(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({record[4][0].split("%", 1)[0] for record in records})


class SafeJsonHttpClient:
    """Fetch JSON only after validating the URL, DNS answers, redirects and size."""

    def __init__(self, *, client: httpx.AsyncClient | None = None, resolver: Resolver = resolve_host) -> None:
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._resolver = resolver

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request_json(
        self,
        *,
        method: str,
        url: str,
        allowed_hosts: set[str],
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        max_response_bytes: int = 3 * 1024 * 1024,
        timeout_seconds: float = 20,
        max_redirects: int = 3,
    ) -> dict[str, Any]:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ConnectorSecurityError("custom data connectors only allow GET or read-only POST")
        current_url = url
        allowed = {host.lower().strip(".") for host in allowed_hosts}
        for redirect_count in range(max_redirects + 1):
            await self._validate_target(current_url, allowed)
            try:
                async with self._client.stream(
                    method,
                    current_url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=httpx.Timeout(timeout_seconds),
                    follow_redirects=False,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= max_redirects:
                            raise ConnectorHttpError("connector redirect limit exceeded")
                        location = response.headers.get("Location")
                        if not location:
                            raise ConnectorHttpError("connector redirect did not include Location")
                        current_url = urljoin(current_url, location)
                        continue
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as error:
                        raise ConnectorHttpError(f"connector returned HTTP {response.status_code}") from error
                    declared = response.headers.get("Content-Length")
                    if declared:
                        try:
                            if int(declared) > max_response_bytes:
                                raise ConnectorResponseTooLarge("connector response exceeds configured byte limit")
                        except ValueError:
                            pass
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type not in {"application/json", "text/json"} and not content_type.endswith("+json"):
                        raise ConnectorHttpError("connector response must use a JSON content type")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_response_bytes:
                            raise ConnectorResponseTooLarge("connector response exceeds configured byte limit")
                        chunks.append(chunk)
                    try:
                        payload = json.loads(b"".join(chunks))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise ConnectorHttpError("connector returned invalid JSON") from error
                    if not isinstance(payload, dict):
                        raise ConnectorHttpError("connector root JSON value must be an object")
                    return payload
            except ConnectorHttpError:
                raise
            except (httpx.HTTPError, asyncio.TimeoutError) as error:
                raise ConnectorHttpError(f"connector request failed: {type(error).__name__}") from error
        raise ConnectorHttpError("connector redirect limit exceeded")

    async def _validate_target(self, url: str, allowed_hosts: set[str]) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ConnectorSecurityError("connector requests require credential-free HTTPS URLs")
        if parsed.port not in (None, 443):
            raise ConnectorSecurityError("connector requests only allow HTTPS port 443")
        host = parsed.hostname.lower().strip(".")
        if host not in allowed_hosts:
            raise ConnectorSecurityError("connector target host is not allowlisted")
        addresses = await self._resolver(host, 443)
        if not addresses:
            raise ConnectorSecurityError("connector target did not resolve")
        for value in addresses:
            try:
                address = ipaddress.ip_address(value.split("%", 1)[0])
            except ValueError as error:
                raise ConnectorSecurityError("connector DNS resolver returned an invalid address") from error
            if not address.is_global:
                raise ConnectorSecurityError("connector DNS target must resolve only to public addresses")


class SafeBinaryHttpClient(SafeJsonHttpClient):
    """Download bounded image bytes with the same DNS and redirect policy."""

    async def get(
        self,
        *,
        url: str,
        allowed_hosts: set[str],
        headers: dict[str, str] | None = None,
        max_response_bytes: int = 10 * 1024 * 1024,
        timeout_seconds: float = 30,
        max_redirects: int = 3,
    ) -> BinaryHttpResponse:
        current_url = url
        allowed = {host.lower().strip(".") for host in allowed_hosts}
        for redirect_count in range(max_redirects + 1):
            await self._validate_target(current_url, allowed)
            try:
                async with self._client.stream(
                    "GET",
                    current_url,
                    headers=headers,
                    timeout=httpx.Timeout(timeout_seconds),
                    follow_redirects=False,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= max_redirects:
                            raise ConnectorHttpError(
                                "connector redirect limit exceeded"
                            )
                        location = response.headers.get("Location")
                        if not location:
                            raise ConnectorHttpError(
                                "connector redirect did not include Location"
                            )
                        current_url = urljoin(current_url, location)
                        continue
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as error:
                        raise ConnectorHttpError(
                            f"connector returned HTTP {response.status_code}"
                        ) from error
                    declared = response.headers.get("Content-Length")
                    if declared:
                        try:
                            if int(declared) > max_response_bytes:
                                raise ConnectorResponseTooLarge(
                                    "connector response exceeds configured byte limit"
                                )
                        except ValueError:
                            pass
                    content_type = (
                        response.headers.get("Content-Type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if not content_type.startswith("image/"):
                        raise ConnectorHttpError(
                            "connector response must use an image content type"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_response_bytes:
                            raise ConnectorResponseTooLarge(
                                "connector response exceeds configured byte limit"
                            )
                        chunks.append(chunk)
                    return BinaryHttpResponse(
                        content=b"".join(chunks),
                        content_type=content_type,
                        final_url=current_url,
                    )
            except (ConnectorHttpError, ConnectorSecurityError):
                raise
            except (httpx.HTTPError, asyncio.TimeoutError) as error:
                raise ConnectorHttpError(
                    f"connector request failed: {type(error).__name__}"
                ) from error
        raise ConnectorHttpError("connector redirect limit exceeded")


__all__ = [
    "BinaryHttpResponse",
    "ConnectorHttpError",
    "ConnectorResponseTooLarge",
    "SafeBinaryHttpClient",
    "SafeJsonHttpClient",
    "resolve_host",
]
