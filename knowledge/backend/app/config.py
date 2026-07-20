from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit


DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5174",
    "http://localhost:5174",
)


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str | None = field(repr=False)
    cors_origins: tuple[str, ...]
    web_proxy_url: str | None = None
    browser_channel: str | None = None
    ai_base_url: str | None = None
    ai_api_key: str | None = field(default=None, repr=False)
    ai_model: str | None = None
    ai_timeout_seconds: float = 90.0
    ai_max_attempts: int = 2


def _bounded_float(raw: str | None, *, default: float, minimum: float) -> float:
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _bounded_attempts(raw: str | None) -> int:
    if not raw:
        return 2
    try:
        value = int(raw)
    except ValueError:
        return 2
    return min(max(value, 1), 2)


def _local_cors_origins(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_CORS_ORIGINS

    origins: list[str] = []
    for item in raw.split(","):
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("KNOWLEDGE_CORS_ORIGINS may contain only local origins")
        origins.append(origin)
    return tuple(dict.fromkeys(origins)) or DEFAULT_CORS_ORIGINS


def _web_proxy_url(raw: str | None) -> str | None:
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
        raise RuntimeError(
            "KNOWLEDGE_WEB_PROXY must be an HTTP(S) or SOCKS5 proxy URL, for example socks5://127.0.0.1:7897"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("KNOWLEDGE_WEB_PROXY contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise RuntimeError("KNOWLEDGE_WEB_PROXY contains an invalid port")
    return raw.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("KNOWLEDGE_DATABASE_URL") or None,
        cors_origins=_local_cors_origins(os.getenv("KNOWLEDGE_CORS_ORIGINS")),
        web_proxy_url=_web_proxy_url(os.getenv("KNOWLEDGE_WEB_PROXY")),
        browser_channel=os.getenv("KNOWLEDGE_BROWSER_CHANNEL") or None,
        ai_base_url=os.getenv("KNOWLEDGE_AI_BASE_URL") or None,
        ai_api_key=os.getenv("KNOWLEDGE_AI_API_KEY") or None,
        ai_model=os.getenv("KNOWLEDGE_AI_MODEL") or None,
        ai_timeout_seconds=_bounded_float(
            os.getenv("KNOWLEDGE_AI_TIMEOUT_SECONDS"), default=90.0, minimum=1.0
        ),
        ai_max_attempts=_bounded_attempts(os.getenv("KNOWLEDGE_AI_MAX_ATTEMPTS")),
    )
