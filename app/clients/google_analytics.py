"""Google Analytics 4 (Data API) client。

特性：
  - 复用 GSC 的 JWT 流程拿 OAuth token（scope 不同）
  - 通过 httpx 走代理调 https://analyticsdata.googleapis.com
  - runReport() 拉日维度的 sessions/users/pageviews/conversions/revenue
  - channel=all 时站点汇总；按 channel 拆时返回 organic_search/direct/... 多行

调用示例：
    client = GA4Client(source)
    rows = await client.run_report(
        start_date='2026-06-09',
        end_date='2026-07-08',
        channel_breakdown=False,  # True 时按 channel 拆
    )
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import jwt  # noqa: F401  (PyJWT, 跟 GSC 共用签名逻辑)
import structlog

from app.core.google_config import GoogleSource

logger = structlog.get_logger(__name__)

_TOKEN_CACHE_TTL_SECONDS = 3000
_ANALYTICS_DATA_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE_ANALYTICS = "https://www.googleapis.com/auth/analytics.readonly"


class GA4ClientError(Exception):
    """GA4 API 调用失败。"""


class _TokenCache:
    """按 SA 指纹缓存 token（独立于 GSC，因为 scope 不同）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, str]] = {}

    async def get_or_fetch(self, source: GoogleSource, fetcher: "GA4Client") -> str:
        fp = (
            "ga4:"
            + (source.service_account.private_key_id or source.service_account.client_email)
        )
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
        fp = (
            "ga4:"
            + (source.service_account.private_key_id or source.service_account.client_email)
        )
        self._cache.pop(fp, None)


_token_cache = _TokenCache()


class GA4Client:
    """一个 GoogleSource 一个 GA4Client。"""

    def __init__(self, source: GoogleSource) -> None:
        self.source = source
        self._property_id = source.ga4_property_id

    @property
    def _proxy_url(self) -> str:
        return self.source.google_proxy_url or ""

    def _httpx(self, timeout: float = 30.0) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": timeout}
        if self._proxy_url:
            kwargs["proxy"] = self._proxy_url
        return httpx.AsyncClient(**kwargs)

    async def _fetch_token(self) -> str:
        sa = self.source.service_account
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": sa.client_email,
                "scope": _SCOPE_ANALYTICS,
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
                    raise GA4ClientError(
                        f"token endpoint {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
                token = data.get("access_token")
                if not token:
                    raise GA4ClientError(f"token response missing access_token: {data}")
                return token
        except httpx.HTTPError as e:
            raise GA4ClientError(f"token endpoint HTTP error: {e}") from e

    async def get_access_token(self) -> str:
        return await _token_cache.get_or_fetch(self.source, self)

    async def run_report(
        self,
        start_date: str,
        end_date: str,
        *,
        channel_breakdown: bool = False,
        landing_page_breakdown: bool = False,
    ) -> list[dict[str, Any]]:
        """GA4 runReport → 返回日维度指标，可按渠道或落地页拆分。

        channel_breakdown=True 时按 sessionDefaultChannelGroup 拆；
        landing_page_breakdown=True 时按 landingPagePlusQueryString 拆。
        """
        token = await self.get_access_token()
        dimensions = ["date"]
        if channel_breakdown:
            dimensions.append("sessionDefaultChannelGroup")
        if landing_page_breakdown:
            dimensions.append("landingPagePlusQueryString")

        body: dict[str, Any] = {
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [
                {"name": "sessions"},
                {"name": "totalUsers"},
                {"name": "newUsers"},
                {"name": "screenPageViews"},
                {"name": "engagedSessions"},
                {"name": "engagementRate"},
                {"name": "averageSessionDuration"},
                {"name": "bounceRate"},
                {"name": "conversions"},
                {"name": "totalRevenue"},
            ],
            "limit": 100000,
            "keepEmptyRows": False,
        }
        # dimensionFilter: 限制 date 范围（API 自动按 dateRanges 切，这里不加额外 filter）
        url = _ANALYTICS_DATA_URL.format(property_id=self._property_id)
        try:
            async with self._httpx(timeout=90.0) as cli:
                resp = await cli.post(
                    url,
                    json=body,
                    headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                    )
                if resp.status_code == 401:
                    _token_cache.invalidate(self.source)
                    token = await self.get_access_token()
                    async with self._httpx(timeout=90.0) as cli2:
                        resp = await cli2.post(
                            url,
                            json=body,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "application/json",
                            },
                        )
                if resp.status_code != 200:
                    raise GA4ClientError(
                        f"runReport {resp.status_code}: {resp.text[:400]}"
                    )
                data = resp.json()
                rows = data.get("rows", []) or []
                # 把 GA4 的扁平结构转成 dict（dim_value_0, dim_value_1, ...）
                result: list[dict[str, Any]] = []
                for r in rows:
                    dim_vals = [dv.get("value", "") for dv in r.get("dimensionValues", [])]
                    met_vals = [mv.get("value", "0") for mv in r.get("metricValues", [])]
                    dimension_index = 1
                    channel = "all"
                    landing_page = ""
                    if channel_breakdown and len(dim_vals) > dimension_index:
                        channel = dim_vals[dimension_index]
                        dimension_index += 1
                    if landing_page_breakdown and len(dim_vals) > dimension_index:
                        landing_page = dim_vals[dimension_index]
                    row: dict[str, Any] = {
                        "date": dim_vals[0] if len(dim_vals) >= 1 else "",
                        "channel": channel,
                        "landing_page": landing_page,
                        "sessions": int(met_vals[0]) if len(met_vals) >= 1 else 0,
                        "total_users": int(met_vals[1]) if len(met_vals) >= 2 else 0,
                        "new_users": int(met_vals[2]) if len(met_vals) >= 3 else 0,
                        "pageviews": int(met_vals[3]) if len(met_vals) >= 4 else 0,
                        "engaged_sessions": (
                            int(met_vals[4]) if len(met_vals) >= 5 else 0
                        ),
                        "engagement_rate": (
                            float(met_vals[5]) if len(met_vals) >= 6 else 0.0
                        ),
                        "avg_session_duration": (
                            float(met_vals[6]) if len(met_vals) >= 7 else 0.0
                        ),
                        "bounce_rate": (
                            float(met_vals[7]) if len(met_vals) >= 8 else 0.0
                        ),
                        "conversions": (
                            float(met_vals[8]) if len(met_vals) >= 9 else 0.0
                        ),
                        "revenue": (
                            float(met_vals[9]) if len(met_vals) >= 10 else 0.0
                        ),
                    }
                    result.append(row)
                return result
        except httpx.HTTPError as e:
            raise GA4ClientError(f"runReport HTTP error: {e}") from e

    async def ping(self) -> dict[str, Any]:
        """健康检查：1 行测试查询。"""
        try:
            token = await self.get_access_token()
            rows = await self.run_report(
                start_date=self.source.default_start_date or "2026-07-01",
                end_date=self.source.default_end_date or "2026-07-08",
                channel_breakdown=False,
            )
            return {
                "ok": True,
                "token_prefix": token[:12] + "...",
                "rows_count": len(rows),
                "property_id": self._property_id,
                "proxy_url": self._proxy_url,
            }
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(e),
                "property_id": self._property_id,
                "proxy_url": self._proxy_url,
            }
