"""行业无关的 Semrush 关键词导入预筛规则。"""
from __future__ import annotations

import math
import re
from typing import Any

from app.engine.loader import get_store

_INTENT_ALIASES = {
    "i": "informational",
    "informational": "informational",
    "information": "informational",
    "c": "commercial",
    "commercial": "commercial",
    "commercial investigation": "commercial",
    "t": "transactional",
    "transactional": "transactional",
    "n": "navigational",
    "navigational": "navigational",
}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    cleaned = re.sub(r"[$,%\s]", "", str(value)).replace(",", "")
    multiplier = 1
    if cleaned.lower().endswith("k"):
        multiplier, cleaned = 1_000, cleaned[:-1]
    elif cleaned.lower().endswith("m"):
        multiplier, cleaned = 1_000_000, cleaned[:-1]
    try:
        result = float(cleaned) * multiplier
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _contains(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(str(pattern).lower() in lowered for pattern in patterns if pattern)


def normalize_intent(value: Any) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return _INTENT_ALIASES.get(raw, raw if raw in set(_INTENT_ALIASES.values()) else "unknown")


def classify_keyword_type(keyword: str, intent: str) -> str:
    text = re.sub(r"\s+", " ", str(keyword or "").strip().lower())
    normalized_intent = normalize_intent(intent)
    if _contains(text, ["near me", "nearby", "in my area", "local"]):
        return "local"
    if "?" in text or re.search(r"^(who|what|when|where|why|how|can|does|is|are|which)\b", text):
        return "question"
    if _contains(text, ["best", "top", "vs", "versus", "compare", "comparison", "review", "alternative"]):
        return "comparison"
    if normalized_intent == "transactional" or _contains(text, ["buy", "shop", "price", "sale", "discount", "coupon"]):
        return "transactional"
    if normalized_intent == "informational" or _contains(text, ["guide", "tutorial", "meaning", "tips"]):
        return "informational"
    if normalized_intent == "navigational":
        return "navigational"
    return "generic"


def preflight_keyword(keyword: dict[str, Any]) -> dict[str, Any]:
    """Return only generic data-quality and query-shape decisions.

    Business relevance, risk words and site assignment stay in the business rule layer.
    """
    rules = get_store().get("keywordPreflight", {}) or {}
    text = re.sub(r"\s+", " ", str(keyword.get("keyword") or "").strip())
    intent = normalize_intent(keyword.get("intent"))
    reasons: list[str] = []
    min_length = int(rules.get("minLength", 2))
    max_length = int(rules.get("maxLength", 180))

    if not text:
        reasons.append("关键词为空")
    elif len(text) < min_length:
        reasons.append(f"关键词长度小于 {min_length}")
    elif len(text) > max_length:
        reasons.append(f"关键词长度超过 {max_length}")

    volume = _number(keyword.get("volume"))
    kd = _number(keyword.get("kd"))
    cpc = _number(keyword.get("cpc"))
    if kd is not None and not 0 <= kd <= 100:
        reasons.append("KD 不在 0-100 范围")
    if volume is not None and volume < 0:
        reasons.append("搜索量不能为负数")
    if cpc is not None and cpc < 0:
        reasons.append("CPC 不能为负数")

    warnings: list[str] = []
    if rules.get("requireDatabase", True) and not keyword.get("semrush_database") and not keyword.get("database"):
        warnings.append("缺少 Semrush Database")
    if rules.get("unknownIntent", "review") == "review" and intent == "unknown":
        warnings.append("意图缺失或无法标准化")
    if rules.get("missingMetrics", "review") == "review" and volume is None:
        warnings.append("缺少搜索量")
    elif rules.get("missingMetrics", "review") == "review" and volume == 0:
        warnings.append("搜索量为 0")

    keyword_type = classify_keyword_type(text, intent)
    status = "invalid" if reasons else "needs_review" if warnings else "ready"
    reason = "；".join(reasons or warnings)
    return {
        "keyword": text,
        "normalizedIntent": intent,
        "keywordType": keyword_type,
        "wordCount": len(text.split()),
        "isQuestion": keyword_type == "question",
        "isLocal": keyword_type == "local",
        "isComparison": keyword_type == "comparison",
        "preflightStatus": status,
        "preflightReason": reason,
    }
