"""通用 HTTP 客户端封装：httpx + tenacity。"""
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


class ExternalCallError(Exception):
    """外部调用失败，统一抛出。"""


async def request_json(
    method: str,
    url: str,
    *,
    client_label: str,
    json: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """带超时、重试、Prometheus 埋点的外部 HTTP 调用。"""
    timeout_s = timeout if timeout is not None else float(_settings.http_timeout_seconds)
    headers = headers or {}
    start = time.perf_counter()
    attempts = _settings.http_retry_max if max_attempts is None else max_attempts
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, attempts)),
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
                        data=data,
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
        signals = "; ".join(
            f"{key}={e.response.headers[key]}"
            for key in ("server", "cf-ray", "cf-mitigated", "x-cache")
            if e.response.headers.get(key)
        )
        suffix = f"（{signals}）" if signals else ""
        raise ExternalCallError(f"{client_label} 状态码 {e.response.status_code}{suffix}") from e
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.error("external_call_failed", client=client_label, url=url, error=str(e))
        raise ExternalCallError(f"{client_label} 调用失败（{type(e).__name__}）：{str(e) or '无详细信息'}") from e
    finally:
        external_call_duration_seconds.labels(client=client_label).observe(time.perf_counter() - start)


async def request_text(
    method: str,
    url: str,
    *,
    client_label: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    max_attempts: int | None = None,
) -> str:
    """带超时、重试和埋点的 HTML / 文本请求。"""
    timeout_s = timeout if timeout is not None else float(_settings.http_timeout_seconds)
    headers = headers or {}
    start = time.perf_counter()
    attempts = _settings.http_retry_max if max_attempts is None else max_attempts
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, attempts)),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
                    resp = await client.request(method, url, params=params, headers=headers)
                    resp.raise_for_status()
                    return resp.text
    except RetryError as e:
        logger.error("external_call_retry_exhausted", client=client_label, url=url, error=str(e))
        raise ExternalCallError(f"{client_label} 重试耗尽：{e}") from e
    except httpx.HTTPStatusError as e:
        logger.error("external_call_status_error", client=client_label, url=url, status=e.response.status_code)
        signals = "; ".join(
            f"{key}={e.response.headers[key]}"
            for key in ("server", "cf-ray", "cf-mitigated", "x-cache")
            if e.response.headers.get(key)
        )
        suffix = f"（{signals}）" if signals else ""
        raise ExternalCallError(f"{client_label} 状态码 {e.response.status_code}{suffix}") from e
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.error("external_call_failed", client=client_label, url=url, error=str(e))
        raise ExternalCallError(f"{client_label} 调用失败（{type(e).__name__}）：{str(e) or '无详细信息'}") from e
    finally:
        external_call_duration_seconds.labels(client=client_label).observe(time.perf_counter() - start)


async def request_bytes(
    method: str,
    url: str,
    *,
    client_label: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    max_attempts: int | None = None,
) -> bytes:
    """带超时、重试和埋点的二进制请求。"""
    timeout_s = timeout if timeout is not None else float(_settings.http_timeout_seconds)
    headers = headers or {}
    start = time.perf_counter()
    attempts = _settings.http_retry_max if max_attempts is None else max_attempts
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, attempts)),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
                    resp = await client.request(method, url, params=params, headers=headers)
                    resp.raise_for_status()
                    return resp.content
    except RetryError as e:
        logger.error("external_call_retry_exhausted", client=client_label, url=url, error=str(e))
        raise ExternalCallError(f"{client_label} 重试耗尽：{e}") from e
    except httpx.HTTPStatusError as e:
        logger.error("external_call_status_error", client=client_label, url=url, status=e.response.status_code)
        signals = "; ".join(
            f"{key}={e.response.headers[key]}"
            for key in ("server", "cf-ray", "cf-mitigated", "x-cache")
            if e.response.headers.get(key)
        )
        suffix = f"（{signals}）" if signals else ""
        raise ExternalCallError(f"{client_label} 状态码 {e.response.status_code}{suffix}") from e
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.error("external_call_failed", client=client_label, url=url, error=str(e))
        raise ExternalCallError(f"{client_label} 调用失败（{type(e).__name__}）：{str(e) or '无详细信息'}") from e
    finally:
        external_call_duration_seconds.labels(client=client_label).observe(time.perf_counter() - start)
