"""关键词评分。纯函数，从 rule_sets.payload.scoring.formula 读所有系数。"""
from __future__ import annotations

from typing import Any

from app.engine.classifier import classify_keyword, _has_any
from app.engine.loader import get_store


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _to_number(value: Any) -> float:
    if value is None:
        return 0.0
    cleaned = str(value).replace("$", "").replace(",", "").replace("%", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _make_id(keyword: str, index: int) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", (keyword or "keyword").lower()).strip("-")
    return f"{slug}-{index}"


def score_keyword(keyword: dict[str, Any], project: dict[str, Any] | None = None) -> dict[str, Any]:
    """对单条关键词打分，返回原字段 + classification + scores + priority。"""
    project = project or {}
    store = get_store()
    formula = store.get("scoring.formula", {}) or {}
    thresholds = (store.get("scoring.thresholds") or {"P0": 78, "P1": 65, "P2": 50})
    max_score = int(store.get("scoring.maxScore", 100))

    classification = classify_keyword(keyword.get("keyword", ""), keyword.get("intent", ""), project)
    volume = _to_number(keyword.get("volume"))
    kd = _to_number(keyword.get("kd"))
    intent = str(keyword.get("intent") or "").lower()
    text = str(keyword.get("keyword") or "").lower()

    demand = formula.get("demand", {}) or {}
    difficulty = formula.get("difficulty", {}) or {}
    commercial = formula.get("commercial", {}) or {}
    content = formula.get("content", {}) or {}
    site_fit = formula.get("siteFit", {}) or {}
    risk = formula.get("riskPenalty", {}) or {}

    import math
    # 兼容 seo-standard.json: demand = logCoeff × log10(volume+1) × scale + offset
    # 默认 logCoeff=6 / scale=1 / offset=0，与原 Node.js Math.log10(volume+1) * 6 完全等价
    demand_score = _clamp(
        round(
            (
                math.log10(volume + 1)
                * float(demand.get("logCoeff", 6))
                + float(demand.get("offset", 0))
            )
            * float(demand.get("scale", 1))
        ),
        float(demand.get("min", 0)),
        float(demand.get("max", 20)),
    )
    difficulty_score = _clamp(
        round(float(difficulty.get("base", 20)) - kd * float(difficulty.get("kdSlope", 0.23))),
        float(difficulty.get("min", 0)),
        float(difficulty.get("max", 20)),
    )
    commercial_score = _clamp(
        (15 if "transactional" in intent else 0)
        + (11 if "commercial" in intent else 0)
        + (4 if _has_any(text, ["best", "vs", "review", "price", "buy", "shop"]) else 0)
        + (2 if classification["assignedSite"] == "主站-博客" else 0),
        float(commercial.get("min", 3)),
        float(commercial.get("max", 20)),
    )
    content_score = _clamp(
        (11 if "informational" in intent else 0)
        + (6 if _has_any(text, ["how", "guide", "what", "best", "vs", "choose", "flavor", "flavour"]) else 0)
        + (4 if "集合页" in classification["pageType"] else 0),
        float(content.get("min", 5)),
        float(content.get("max", 20)),
    )
    site_fit_score = _clamp(
        18 if classification["assignedSite"].startswith("主站") else 14,
        float(site_fit.get("min", 0)),
        float(site_fit.get("max", 20)),
    )
    risk_penalty = (
        float(risk.get("hold", 28))
        if classification["assignedSite"] == "暂不做"
        else float(risk.get("keywordRisk", 8)) if _has_any(text, ["safe", "legal", "age"]) else 0
    )
    denominator = float(formula.get("totalDenominator", 92))
    total = _clamp(
        round(
            ((demand_score + difficulty_score + commercial_score + content_score + site_fit_score - risk_penalty)
             / denominator)
            * 100
        ),
        0,
        max_score,
    )
    priority = (
        "Hold"
        if classification["assignedSite"] == "暂不做"
        else "P0" if total >= thresholds.get("P0", 78)
        else "P1" if total >= thresholds.get("P1", 65)
        else "P2" if total >= thresholds.get("P2", 50)
        else "P3"
    )

    return {
        **keyword,
        **classification,
        "volume": volume,
        "kd": kd,
        "scores": {
            "demand": demand_score,
            "difficulty": difficulty_score,
            "commercial": commercial_score,
            "content": content_score,
            "siteFit": site_fit_score,
            "riskPenalty": risk_penalty,
            "total": total,
        },
        "priority": priority,
    }


def enrich_keywords(keywords: list[dict[str, Any]], project: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """批量打分，每条补 id。"""
    project = project or {}
    return [
        score_keyword(
            {**kw, "id": kw.get("id") or _make_id(kw.get("keyword", ""), idx)},
            project,
        )
        for idx, kw in enumerate(keywords)
    ]