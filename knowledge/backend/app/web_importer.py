from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from lxml import html as lxml_html
from trafilatura import bare_extraction

from .config import get_settings
from .errors import UrlFetchError
from .schemas import normalize_optional_http_url


MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
MIN_CONTENT_CHARS = 100
REQUEST_TIMEOUT_SECONDS = 20.0
USER_AGENT = "LocalKnowledgeImporter/0.1 (user-initiated single-page fetch)"
HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


@dataclass(frozen=True, slots=True)
class FetchedArticle:
    requested_url: str
    final_url: str
    title: str
    raw_content: str
    author: str | None = None
    published_at: datetime | None = None
    language_code: str | None = None


@dataclass(frozen=True, slots=True)
class FetchedResource:
    requested_url: str
    final_url: str
    media_type: str
    content: bytes
    encoding: str

    @property
    def text(self) -> str:
        try:
            return self.content.decode(self.encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return (
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_unspecified
        and not address.is_multicast
    )


async def resolve_public_addresses(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    families = (socket.AF_INET, socket.AF_INET6)
    resolved_any = False
    for family in families:
        try:
            records = await loop.getaddrinfo(
                host,
                port,
                family=family,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror:
            continue

        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses:
            continue
        resolved_any = True
        public_addresses = [address for address in addresses if _is_public_address(address)]
        if public_addresses:
            # Prefer a public IPv4 result when the local resolver also returns a
            # non-routable IPv6 sinkhole. IPv6-only hosts still fall through to
            # the AF_INET6 pass.
            return public_addresses

    if not resolved_any:
        raise UrlFetchError("无法解析该网址的域名")
    raise UrlFetchError("为安全起见，不能抓取本机、私网或保留地址")


def _normalized_target(url: str) -> tuple[str, str, int]:
    try:
        normalized = normalize_optional_http_url(url)
    except ValueError as exc:
        raise UrlFetchError(str(exc).replace("canonical_url", "url")) from exc
    if normalized is None:
        raise UrlFetchError("url must not be blank")

    parsed = urlsplit(normalized)
    host = parsed.hostname
    if host is None:
        raise UrlFetchError("url must include a host")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UrlFetchError("url has an invalid port") from exc
    if port not in {80, 443}:
        raise UrlFetchError("只允许抓取使用标准 HTTP 或 HTTPS 端口的网页")
    if host == "localhost" or host.endswith(".localhost"):
        raise UrlFetchError("为安全起见，不能抓取本机、私网或保留地址")
    if _looks_like_ip(host) and not _is_public_address(host):
        raise UrlFetchError("为安全起见，不能抓取本机、私网或保留地址")
    return normalized, host, port


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def _parse_published_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_optional(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def _article_from_html(
    html: str, *, requested_url: str, final_url: str
) -> FetchedArticle:
    try:
        document = bare_extraction(
            html,
            url=final_url,
            include_comments=False,
            include_tables=True,
            output_format="txt",
            with_metadata=True,
        )
        extracted = document.as_dict() if document is not None else None
    except Exception as exc:
        raise UrlFetchError("网页正文解析失败，请确认该链接指向公开文章页面") from exc
    if not isinstance(extracted, dict):
        raise UrlFetchError("没有从网页中识别到正文，请改用文章的具体页面链接")

    raw_content = str(
        extracted.get("text") or extracted.get("raw_text") or ""
    ).strip()
    if len(raw_content) < MIN_CONTENT_CHARS:
        raise UrlFetchError(
            f"识别到的正文过短（少于 {MIN_CONTENT_CHARS} 个字符），无法可靠生成知识卡片"
        )

    host = urlsplit(final_url).hostname or "网页文章"
    title = _clean_optional(extracted.get("title"), limit=1000) or host
    language_code = _clean_optional(extracted.get("language"), limit=20)
    if language_code is None:
        try:
            language_code = _clean_optional(
                lxml_html.fromstring(html).get("lang"), limit=20
            )
        except (ValueError, TypeError):
            language_code = None

    return FetchedArticle(
        requested_url=requested_url,
        final_url=final_url,
        title=title,
        raw_content=raw_content,
        author=_clean_optional(extracted.get("author"), limit=300),
        published_at=_parse_published_at(extracted.get("date")),
        language_code=language_code,
    )


class WebArticleFetcher:
    """Fetch exactly one public article while enforcing SSRF and size boundaries."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self._client = client
        self._resolver = resolver or resolve_public_addresses
        self._proxy_url = (
            proxy_url if proxy_url is not None else get_settings().web_proxy_url
        )

    async def fetch(self, url: str) -> FetchedArticle:
        resource = await SafeWebClient(
            client=self._client, resolver=self._resolver, proxy_url=self._proxy_url
        ).fetch(
            url,
            accepted_types=HTML_CONTENT_TYPES,
            max_bytes=MAX_RESPONSE_BYTES,
            accept="text/html,application/xhtml+xml",
        )
        return _article_from_html(
            resource.text,
            requested_url=resource.requested_url,
            final_url=resource.final_url,
        )


class SafeWebClient:
    """Bounded public-web fetch shared by article and discovery adapters."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self._client = client
        self._resolver = resolver or resolve_public_addresses
        self._proxy_url = (
            proxy_url if proxy_url is not None else get_settings().web_proxy_url
        )

    async def fetch(
        self,
        url: str,
        *,
        accepted_types: set[str],
        max_bytes: int,
        accept: str,
    ) -> FetchedResource:
        requested_url, _, _ = _normalized_target(url)
        if self._client is not None:
            return await self._fetch_with_client(
                self._client, requested_url, accepted_types, max_bytes, accept
            )
        client_options: dict[str, Any] = {
            "timeout": httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
            "follow_redirects": False,
            "trust_env": False,
            "headers": {"User-Agent": USER_AGENT, "Accept": accept},
        }
        if self._proxy_url:
            client_options["proxy"] = self._proxy_url
        async with httpx.AsyncClient(**client_options) as client:
            return await self._fetch_with_client(
                client, requested_url, accepted_types, max_bytes, accept
            )

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        requested_url: str,
        accepted_types: set[str],
        max_bytes: int,
        accept: str,
    ) -> FetchedResource:
        current_url = requested_url
        for redirect_count in range(MAX_REDIRECTS + 1):
            current_url, host, port = _normalized_target(current_url)
            addresses = await self._resolver(host, port)
            if not addresses or not all(
                _is_public_address(address) for address in addresses
            ):
                raise UrlFetchError(
                    "为安全起见，不能抓取本机、私网或保留地址"
                )
            try:
                async with client.stream(
                    "GET",
                    current_url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": accept,
                    },
                    follow_redirects=False,
                ) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise UrlFetchError("网页返回了无目标地址的重定向")
                        if redirect_count >= MAX_REDIRECTS:
                            raise UrlFetchError("网页重定向次数过多")
                        current_url = urljoin(current_url, location)
                        continue
                    if 300 <= response.status_code < 400:
                        raise UrlFetchError("网页返回了不支持的重定向响应")
                    if response.status_code >= 400:
                        raise UrlFetchError(f"网页抓取失败（HTTP {response.status_code}）")

                    content_type = response.headers.get("content-type", "")
                    media_type = content_type.split(";", 1)[0].strip().lower()
                    if media_type not in accepted_types:
                        raise UrlFetchError("网址返回了不支持的内容类型")

                    declared_size = response.headers.get("content-length")
                    if declared_size:
                        try:
                            if int(declared_size) > max_bytes:
                                raise UrlFetchError("网页内容过大，已停止抓取")
                        except ValueError:
                            pass

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise UrlFetchError("网页内容过大，已停止抓取")
                        chunks.append(chunk)
                    payload = b"".join(chunks)
                    encoding = response.charset_encoding or "utf-8"
            except UrlFetchError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise UrlFetchError("网页连接失败或超时，请稍后重试") from exc

            return FetchedResource(
                requested_url=requested_url,
                final_url=current_url,
                media_type=media_type,
                content=payload,
                encoding=encoding,
            )

        raise UrlFetchError("网页重定向次数过多")
