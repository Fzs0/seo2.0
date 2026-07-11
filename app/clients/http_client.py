"""通用 HTTP 客户端封装：httpx + tenacity + 熔断占位。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.middleware.metrics import external_call_duration_seconds

logger = structlog.get_logger(__name__)
_settings = get_settings()


class CircuitOpen(Exception):
    """熔断占位（当前永不抛出；预留接口）。"""


class ExternalCallError(Exception):
    """外部调用失败，统一抛出。"""


async def request_json(
    method: str,
    url: str,
    *,
    client_label: str,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """带超时、重试、Prometheus 埋点的外部 HTTP 调用。"""
    timeout_s = timeout if timeout is not None else float(_settings.http_timeout_seconds)
    headers = headers or {}
    start = time.perf_counter()
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, _settings.http_retry_max)),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    resp = await client.request(
                        method,
                        url,
                        json=json,
                        params=params,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    return resp.json() if resp.content else {}
    except RetryError as e:
        logger.error("external_call_retry_exhausted", client=client_label, url=url, error=str(e))
        raise ExternalCallError(f"{client_label} 重试耗尽：{e}") from e
    except httpx.HTTPStatusError as e:
        logger.error("external_call_status_error", client=client_label, url=url, status=e.response.status_code)
        raise ExternalCallError(f"{client_label} 状态码 {e.response.status_code}") from e
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.error("external_call_failed", client=client_label, url=url, error=str(e))
        raise ExternalCallError(f"{client_label} 调用失败：{e}") from e
    finally:
        external_call_duration_seconds.labels(client=client_label).observe(time.perf_counter() - start)