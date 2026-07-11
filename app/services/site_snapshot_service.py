"""站点 API 可用性探测：模拟 Node.js /api/site-snapshot。

POST body: { apis: [{ url, name?, headers? }, ... ] }
返回每条 api 的可达性 + 字段名（如果响应是 JSON）。
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.clients.http_client import ExternalCallError, request_json

logger = structlog.get_logger(__name__)


async def _probe_one(api: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    name = api.get("name") or api.get("url")
    url = api.get("url", "")
    if not url:
        return {"name": name, "url": url, "ok": False, "error": "missing url"}
    try:
        data = await request_json(
            "GET",
            url,
            client_label="site_snapshot",
            headers=api.get("headers") or {},
            timeout=timeout,
        )
        return {
            "name": name,
            "url": url,
            "ok": True,
            "status": 200,
            "fieldNames": sorted(list(data.keys())) if isinstance(data, dict) else None,
            "sample": _truncate(data),
        }
    except ExternalCallError as e:
        logger.warning("site_snapshot_probe_failed", url=url, error=str(e))
        return {"name": name, "url": url, "ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"name": name, "url": url, "ok": False, "error": str(e)}


def _truncate(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:5]:
            out[k] = _truncate(v)
        return out
    if isinstance(value, list):
        return [_truncate(v) for v in value[:3]]
    if isinstance(value, str):
        return value[:200]
    return value


async def probe_apis(apis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return await asyncio.gather(*[_probe_one(a) for a in apis])