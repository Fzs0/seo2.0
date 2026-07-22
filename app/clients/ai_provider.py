"""AI Provider 客户端。OpenAI 兼容 chat/completions 接口；失败回退到本地 brief。

失败定义：网络异常 / 4xx / 5xx / 返回 content 为空。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import structlog

from app.clients.http_client import ExternalCallError, request_json
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
_settings = get_settings()


def _infer_provider(base_url: str, model: str) -> str:
    """Return the model vendor, never the OpenAI-compatible protocol name."""
    host = (urlparse(base_url).hostname or "").lower()
    normalized_model = model.strip().lower()
    host_markers = (
        ("openai.com", "openai"),
        ("deepseek.com", "deepseek"),
        ("anthropic.com", "anthropic"),
        ("googleapis.com", "google"),
        ("openrouter.ai", "openrouter"),
        ("dashscope.aliyuncs.com", "alibaba"),
        ("siliconflow.cn", "siliconflow"),
        ("moonshot.cn", "moonshot"),
        ("zhipuai.cn", "zhipuai"),
        ("minimax", "minimax"),
    )
    for marker, provider in host_markers:
        if marker in host:
            return provider
    model_prefixes = (
        (("gpt-", "o1", "o3", "o4"), "openai"),
        (("deepseek-",), "deepseek"),
        (("claude-",), "anthropic"),
        (("gemini-",), "google"),
        (("qwen-", "qwq-"), "alibaba"),
        (("glm-",), "zhipuai"),
        (("kimi-", "moonshot-"), "moonshot"),
        (("minimax-",), "minimax"),
        (("mistral-", "mixtral-"), "mistral"),
    )
    for prefixes, provider in model_prefixes:
        if normalized_model.startswith(prefixes):
            return provider
    return "unknown"


def _provider_for_stage(stage: str, base_url: str, model: str) -> tuple[str, str]:
    configured = str(getattr(_settings, f"ai_{stage}_provider", "") or "").strip().lower()
    if configured:
        return configured, "configuration"
    return _infer_provider(base_url, model), "inference"


def _response_identity(
    data: dict[str, Any] | Any,
    *,
    configured_provider: str,
    provider_source: str,
    requested_model: str,
) -> tuple[str, str, str, str]:
    response_provider = str(data.get("provider") or "").strip().lower() if isinstance(data, dict) else ""
    response_model = str(data.get("model") or "").strip() if isinstance(data, dict) else ""
    provider = response_provider or configured_provider or "unknown"
    model = response_model or requested_model or "unknown"
    return (
        provider,
        model,
        "response" if response_provider else provider_source,
        "response" if response_model else "configuration",
    )


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
    base_url = getattr(_settings, f"ai_{stage}_base_url", "").rstrip("/")
    requested_model = str(getattr(_settings, f"ai_{stage}_model", "") or "").strip()
    provider, provider_source = _provider_for_stage(stage, base_url, requested_model)
    if not is_stage_configured(stage):
        return {
            "content": "",
            "provider": provider,
            "model": requested_model or "unknown",
            "requestedModel": requested_model or "unknown",
            "providerSource": provider_source,
            "modelSource": "configuration",
            "apiFormat": "openai-compatible",
            "configured": False,
            "status": "ai-not-configured",
        }
    api_key = getattr(_settings, f"ai_{stage}_key", "")

    # 自动补 /chat/completions
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload = {
        "model": requested_model,
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
        actual_provider, actual_model, actual_provider_source, model_source = _response_identity(
            data,
            configured_provider=provider,
            provider_source=provider_source,
            requested_model=requested_model,
        )
        return {
            "content": content or "",
            "provider": actual_provider,
            "model": actual_model,
            "requestedModel": requested_model,
            "providerSource": actual_provider_source,
            "modelSource": model_source,
            "apiFormat": "openai-compatible",
            "configured": True,
            "status": None if content else "ai-empty-response",
        }
    except ExternalCallError as e:
        logger.warning("ai_request_failed", stage=stage, error=str(e))
        return {
            "content": "",
            "provider": provider,
            "model": requested_model or "unknown",
            "requestedModel": requested_model or "unknown",
            "providerSource": provider_source,
            "modelSource": "configuration",
            "apiFormat": "openai-compatible",
            "configured": True,
            "status": "ai-request-failed",
            "error": str(e),
        }
