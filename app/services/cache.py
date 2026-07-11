"""AI Brief 缓存（in-memory，TTL + LRU 上限）。后续可替换为 Redis。"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from app.core.config import get_settings

_settings = get_settings()


class BriefCache:
    def __init__(self, ttl_ms: int, max_entries: int) -> None:
        self.ttl_ms = ttl_ms
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        item = self._data.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at <= time.time() * 1000:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        if len(self._data) >= self.max_entries:
            self._data.popitem(last=False)
        self._data[key] = (time.time() * 1000 + self.ttl_ms, value)


_brief_cache = BriefCache(_settings.ai_brief_cache_ttl_ms, _settings.ai_brief_cache_max)


def get_brief_cache() -> BriefCache:
    return _brief_cache