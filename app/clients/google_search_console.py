"""Google Search Console API client。

特性：
  - 自签 JWT → 拿 OAuth2 access_token（缓存 50 分钟，token 实际 60 分钟过期）
  - 通过 httpx 走代理（127.0.0.1:7897）调 Google API
  - searchanalytics.query() 拉取 query × page × country × device 维度的日聚合数据
  - 不依赖 google-auth 库，直接用 PyJWT + cryptography（项目已有依赖）

调用示例：
    from app.core.google_config import get_store
    from app.clients.google_search_console import GSCClient

    src = get_store().get_by_domain('exdivo.com')
    client = GSCClient(src)
    rows = await client.searchanalytics(
        start_date='2026-06-09',
        end_date='2026-07-08',
        dimensions=['date', 'query'],
        row_limit=100,
    )
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from app.core.google_config import GoogleSource

logger = structlog.get_logger(__name__)

_TOKEN_CACHE_TTL_SECONDS = 3000  # 50 分钟（token 实际 60 分钟过期，留余量）
_SEARCHANALYTICS_URL = (
    "https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"
)
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE_SEARCHANALYTICS = "https://www.googleapis.com/auth/webmasters.readonly"


class GSCClientError(Exception):
    """GSC API 调用失败。"""


class _TokenCache:
    """按 SA 指纹缓存 access_token。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, str]] = {}  # fp -> (expires_at_epoch, token)

    async def get_or_fetch(
        self, source: GoogleSource, fetcher: "GSCClient"
    ) -> str:
        fp = source.service_account.private_key_id or source.service_account.client_email
        now = time.time()
        cached = self._cache.get(fp)
        if cached and cached[0] > now + 30:
            return cached[1]
        async with self._lock:
            cached = self._cache.get(fp)
            if cached and cached[0] > now + 30:
                return cached[1]
            token = await fetcher._fetch_token()
            self._cache[fp] = (now + _TOKEN_CACHE_TTL_SECONDS, token)
            return token

    def invalidate(self, source: GoogleSource) -> None:
        fp = source.service_account.private_key_id or source.service_account.client_email
        self._cache.pop(fp, None)


_token_cache = _TokenCache()


class GSCClient:
    """一个 GoogleSource 一个 GSCClient（source 持有 SA key + proxy URL）。"""

    def __init__(self, source: GoogleSource) -> None:
        self.source = source
        self._site_url = source.gsc_site_url

    @property
    def _proxy_url(self) -> str:
        return self.source.google_proxy_url or ""

    def _httpx(self, timeout: float = 30.0) -> httpx.AsyncClient:
        """每次新建一个 client（httpx async client 持有 connection pool，简单起见不复用）。"""
        kwargs: dict[str, Any] = {"timeout": timeout}
        if self._proxy_url:
            kwargs["proxy"] = self._proxy_url
        return httpx.AsyncClient(**kwargs)

    async def _fetch_token(self) -> str:
        """用 SA key 自签 JWT，向 Google 换 access_token。"""
        import jwt  # PyJWT

        sa = self.source.service_account
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": sa.client_email,
                "scope": _SCOPE_SEARCHANALYTICS,
                "aud": sa.token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            sa.private_key,
            algorithm="RS256",
            headers={"kid": sa.private_key_id} if sa.private_key_id else None,
        )
        try:
            async with self._httpx(timeout=15.0) as cli:
                resp = await cli.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code != 200:
                    raise GSCClientError(
                        f"token endpoint {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
                token = data.get("access_token")
                if not token:
                    raise GSCClientError(f"token response missing access_token: {data}")
                return token
        except httpx.HTTPError as e:
            raise GSCClientError(f"token endpoint HTTP error: {e}") from e

    async def get_access_token(self) -> str:
        """拿到当前 source 的 access_token（带缓存）。"""
        return await _token_cache.get_or_fetch(self.source, self)

    async def searchanalytics(
        self,
        start_date: str,
        end_date: str,
        *,
        dimensions: list[str] | None = None,
        row_limit: int = 1000,
        aggregation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """searchanalytics.query() — 返回 rows[]（每行含 keys/clicks/impressions/ctr/position）。

        dimensions: 可选 ['date','query','page','country','device'] 任子集
        """
        token = await self.get_access_token()
        body: dict[str, Any] = {
            "startDate": start_date,
            "endDate": end_date,
            "rowLimit": max(1, min(row_limit, 25000)),
        }
        if dimensions:
            body["dimensions"] = list(dimensions)
        if aggregation_type:
            body["aggregationType"] = aggregation_type  # 'auto' | 'byPage' | 'byProperty' | 'byNewsShowcasePanel'

        url = _SEARCHANALYTICS_URL.format(site_url=quote(self._site_url, safe=""))
        try:
            async with self._httpx(timeout=60.0) as cli:
                resp = await cli.post(
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        },
                    )
                if resp.status_code == 401:
                    # token 失效，重试一次
                    _token_cache.invalidate(self.source)
                    token = await self.get_access_token()
                    async with self._httpx(timeout=60.0) as cli2:
                        resp = await cli2.post(
                            url,
                            json=body,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "application/json",
                            },
                        )
                if resp.status_code != 200:
                    raise GSCClientError(
                        f"searchanalytics {resp.status_code}: {resp.text[:400]}"
                    )
                data = resp.json()
                return data.get("rows", []) or []
        except httpx.HTTPError as e:
            raise GSCClientError(f"searchanalytics HTTP error: {e}") from e

    async def ping(self) -> dict[str, Any]:
        """健康检查：拿 token + 调 searchanalytics（1 行）确认全链路通。"""
        try:
            token = await self.get_access_token()
            rows = await self.searchanalytics(
                start_date=self.source.default_start_date or "2026-07-01",
                end_date=self.source.default_end_date or "2026-07-08",
                dimensions=["date"],
                row_limit=1,
            )
            return {
                "ok": True,
                "token_prefix": token[:12] + "...",
                "rows_count": len(rows),
                "site_url": self._site_url,
                "proxy_url": self._proxy_url,
            }
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(e),
                "site_url": self._site_url,
                "proxy_url": self._proxy_url,
            }
