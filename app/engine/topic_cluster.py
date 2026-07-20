"""Deterministic pillar-page / cluster-content grouping for keywords."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any


_MODIFIERS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with",
    "how", "what", "when", "where", "why", "who", "is", "are", "can", "do",
    "does", "best", "top", "buy", "shop", "online", "price", "prices", "coupon",
    "sale", "discount", "review", "reviews", "guide", "tutorial", "near", "me",
    "2024", "2025", "2026", "2027",
}


def _tokens(value: Any) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", str(value or "").lower())
    result: list[str] = []
    for token in raw:
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("s"):
            token = token[:-1]
        result.append(token)
    return result


def _cluster_key(item: dict[str, Any]) -> str:
    keyword = str(item.get("keyword") or "").strip()
    keyword_tokens = _tokens(keyword)
    # Semrush page groups / seed keywords are more authoritative when present.
    for field in ("pageGroup", "page_group", "seedKeyword", "seed_keyword"):
        value = str(item.get(field) or "").strip()
        if value and value.lower() != keyword.lower():
            tokens = [token for token in _tokens(value) if token not in _MODIFIERS]
            if tokens:
                return f"seed:{' '.join(sorted(dict.fromkeys(tokens)))}"
    meaningful = [token for token in keyword_tokens if token not in _MODIFIERS]
    # One-word roots are too broad (e.g. every "vape" query).
    signature = meaningful if len(meaningful) >= 2 else keyword_tokens
    return f"terms:{' '.join(sorted(dict.fromkeys(signature)))}"


def _number(value: Any) -> float:
    try:
        return float(str(value or 0).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def _pillar_rank(item: dict[str, Any]) -> tuple[int, int, float, float, int]:
    priority = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(str(item.get("priority") or ""), 0)
    intent = str(item.get("intent") or "").lower()
    kind = str(item.get("keywordType") or item.get("keyword_type") or "").lower()
    commercial = int(intent in {"commercial", "transactional"} or kind in {"comparison", "transactional"})
    return (priority, commercial, _number(item.get("volume")), -_number(item.get("kd")), -len(str(item.get("keyword") or "")))


def assign_topic_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign stable cluster ids and choose one pillar keyword per cluster."""
    groups: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        groups[_cluster_key(item)].append(index)

    result = [dict(item) for item in items]
    for key, indexes in groups.items():
        cluster_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        pillar_index = max(indexes, key=lambda index: _pillar_rank(result[index]))
        pillar_keyword = str(result[pillar_index].get("keyword") or "")
        cluster_size = len(indexes)
        for index in indexes:
            item = result[index]
            role = "standalone" if cluster_size == 1 else ("pillar" if index == pillar_index else "supporting")
            item.update(
                {
                    "topicClusterId": cluster_id,
                    "topic_cluster_id": cluster_id,
                    "topicCluster": pillar_keyword,
                    "clusterRole": role,
                    "cluster_role": role,
                    "clusterSize": cluster_size,
                    "cluster_size": cluster_size,
                    "pillarKeyword": pillar_keyword,
                    "pillar_keyword": pillar_keyword,
                }
            )
    return result
