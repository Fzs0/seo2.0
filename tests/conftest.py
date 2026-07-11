"""pytest 共享 fixture：给 engine 层注入与生产一致的 rule payload。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# 让 tests/ 目录能 import app/
ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 单测启动前把 RuleStore._payload 填好
from app.engine.loader import get_store  # noqa: E402


def _load_baseline_payload() -> dict[str, Any]:
    """从 workflows/seo-standard.json 直接读，作为测试基准。

    跟 db/scripts/seed_rule_baseline.mjs 灌入的内容完全一致，
    保证单测与生产数据同源。
    """
    p = ROOT / "workflows" / "seo-standard.json"
    if not p.exists():
        # 在 CI / 隔离环境里没有 workflows/，fallback 到一份最小 stub
        # （保证 collect 不挂；具体测试若依赖 keys 失败会自然红）
        return {
            "version": "0.0.0-test",
            "name": "seo-standard",
            "scoring": {"thresholds": {"P0": 78, "P1": 65, "P2": 50}, "maxScore": 100},
            "signals": {
                "risk": ["thc", "cbd"],
                "coreProduct": ["vape"],
                "transaction": ["buy", "shop"],
                "comparison": ["best", "vs", "review"],
                "scenario": ["flavor", "beginner"],
                "knowledge": ["how to", "what is"],
                "mainBlogExclusions": ["legal", "safe", "age"],
            },
            "articleBriefTemplate": {"modules": []},
            "anchorTextRules": {},
            "articleOutputFormat": {},
            "references": {"triggers": {"FDA": {"terms": ["safe", "nicotine"], "label": "FDA", "url": "https://www.fda.gov"}}},
            "imagePlacements": [],
        }
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def rule_payload() -> dict[str, Any]:
    """每个测试都自动注入——不依赖网络/DB。"""
    payload = _load_baseline_payload()
    store = get_store()
    store._payload = payload
    store._version = payload.get("version", "0.0.0-test")
    store._loaded_at = None
    store._effective_at = None
    return payload


@pytest.fixture
def default_project() -> dict[str, Any]:
    return {
        "domain": "vapetopline.com",
        "siteType": "2C商城",
        "market": "US / English",
        "coreProducts": "vape, e-cigarette, disposable",
        "mainPages": "/collections/disposable-vapes\n/collections/e-liquids",
    }