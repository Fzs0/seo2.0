"""AI Provider 客户端。OpenAI 兼容 chat/completions 接口；失败回退到本地 brief。

失败定义：网络异常 / 4xx / 5xx / 返回 content 为空。
"""
from __future__ import annotations

from typing import Any

import structlog

from app.clients.http_client import ExternalCallError, request_json
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
_settings = get_settings()


def is_stage_configured(stage: str) -> bool:
    return _settings.is_ai_stage_configured(stage)


async def generate_ai_content(
    *,
    stage: str,
    prompt: str,
    project: dict[str, Any] | None = None,
    keyword: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调用 OpenAI 兼容 /chat/completions；任何失败返回 status='ai-request-failed'。"""
    if not is_stage_configured(stage):
        return {
            "content": "",
            "provider": "",
            "model": "",
            "apiFormat": "openai-compatible",
            "configured": False,
            "status": "ai-not-configured",
        }
    base_url = getattr(_settings, f"ai_{stage}_base_url", "").rstrip("/")
    api_key = getattr(_settings, f"ai_{stage}_key", "")
    model = getattr(_settings, f"ai_{stage}_model", "")

    # 自动补 /chat/completions
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        data = await request_json(
            "POST",
            url,
            client_label=f"ai_{stage}",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_settings.ai_timeout_seconds,
            max_attempts=_settings.ai_retry_max,
        )
        content = (
            (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if isinstance(data, dict)
            else ""
        )
        return {
            "content": content or "",
            "provider": "openai-compatible",
            "model": model,
            "apiFormat": "openai-compatible",
            "configured": True,
            "status": None if content else "ai-empty-response",
        }
    except ExternalCallError as e:
        logger.warning("ai_request_failed", stage=stage, error=str(e))
        return {
            "content": "",
            "provider": "openai-compatible",
            "model": model,
            "apiFormat": "openai-compatible",
            "configured": True,
            "status": "ai-request-failed",
            "error": str(e),
        }
