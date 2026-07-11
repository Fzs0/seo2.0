"""scorer 单测：锁住 demand 公式（刚修过）、riskPenalty、priority 阈值。

如果某条 fail，检查：
1. scoring.thresholds / scoring.formula.* 在 workflows/seo-standard.json 是否变了
2. 不要修测试，先确认公式是不是该改
"""
from __future__ import annotations

import math

import pytest

from app.engine.scorer import score_keyword


# ---------- demand 公式（关键：使用 log10，不是 volume * coeff） ----------

def test_demand_uses_log10_formula(rule_payload):
    """demand = logCoeff * log10(volume+1) * scale + offset。

    volume=10000: log10(10001) ≈ 4, 6 * 4 = 24 → 但 max 20 → 20
    volume=14800: log10(14801) ≈ 4.17, 6 * 4.17 ≈ 25 → max 20 → 20
    volume=1000:   log10(1001)  ≈ 3,   6 * 3 = 18
    """
    # demand 上限 20，14800 应该是 20
    r = score_keyword({"keyword": "best disposable vapes", "volume": 14800, "kd": 39, "intent": "commercial"})
    assert r["scores"]["demand"] == 20

    r2 = score_keyword({"keyword": "low volume", "volume": 1000, "kd": 50, "intent": "informational"})
    # 6 * log10(1001) = 6 * 3.0004 ≈ 18
    assert 17 <= r2["scores"]["demand"] <= 19


def test_demand_is_not_volume_times_coefficient(rule_payload):
    """回归测试：旧 bug 是 volume * logCoeff（线性），导致 demand 永远 0。"""
    # 旧公式：volume * logCoeff + offset = 14800 * 0 + 0 = 0
    # 新公式：log10(14801) * 6 + 0 = 25.02 → clamp 20 = 20
    r = score_keyword({"keyword": "test", "volume": 14800, "kd": 50, "intent": "informational"})
    assert r["scores"]["demand"] > 0, "demand 永远是 0 意味着 log10 公式被回退成旧版"
    assert r["scores"]["demand"] >= 18


# ---------- difficulty 公式 ----------

def test_difficulty_uses_kd(rule_payload):
    """difficulty = base - kd * slope，与 seo-standard.json 保持一致。

    difficulty 0..20：kd=0 → 20，kd=100 → 20-23 = -3 → clamp 0
    """
    r = score_keyword({"keyword": "k", "volume": 100, "kd": 0, "intent": "informational"})
    assert r["scores"]["difficulty"] == 20

    r2 = score_keyword({"keyword": "k", "volume": 100, "kd": 50, "intent": "informational"})
    # 20 - 50 * 0.23 = 20 - 11.5 = 8.5 → round 9
    assert 8 <= r2["scores"]["difficulty"] <= 10


# ---------- riskPenalty ----------

def test_risk_hold_penalty_28(rule_payload):
    """Hold 关键词 → risk=28（scoring.formula.riskPenalty.hold = 28）。"""
    r = score_keyword({"keyword": "thc vape pen", "volume": 3300, "kd": 45, "intent": "informational"})
    # classifier 会把 thc 标到 Risk → assignedSite="暂不做" → risk=28
    assert r["scores"]["riskPenalty"] == 28
    assert r["priority"] == "Hold"


def test_risk_keyword_risk_8(rule_payload):
    """safe 触发关键词级 risk=8（scoring.formula.riskPenalty.keywordRisk）。"""
    # 'are disposable vapes safe' 会被 classifier 分到博客A（safe 在 exclusion）
    # 因为 safe 在 mainBlogExclusions 里，且不在 risk signals 里 → 不触发 Hold
    # 但 keyword 'safe' 在 scoring.formula.riskPenalty.keywordRisk 列表里 → risk=8
    r = score_keyword(
        {"keyword": "are disposable vapes safe", "volume": 2400, "kd": 28, "intent": "informational"},
    )
    assert r["scores"]["riskPenalty"] == 8
    # priority 偏低：safe 词虽然进博客A（不是 Hold），但总分被扣
    assert r["priority"] in ("P2", "P3", "Hold")  # 不会 P0/P1


# ---------- priority 阈值 ----------

@pytest.mark.parametrize("keyword,expected_priority", [
    # 14800 + kd39 + commercial + best → e2e 实测 P0
    ({"keyword": "best disposable vapes", "volume": 14800, "kd": 39, "intent": "commercial"}, "P0"),
    # 2900 + kd10 + informational + how to → e2e 实测 P0
    ({"keyword": "how to vape properly", "volume": 2900, "kd": 10, "intent": "informational"}, "P0"),
    # 5400 + kd32 + commercial + beginner → e2e 实测 P1
    ({"keyword": "disposable vapes for beginners", "volume": 5400, "kd": 32, "intent": "commercial"}, "P1"),
    # 2400 + kd28 + informational + safe → e2e 实测 P2
    ({"keyword": "are disposable vapes safe", "volume": 2400, "kd": 28, "intent": "informational"}, "P2"),
    # 3300 + kd45 + informational + thc → e2e 实测 Hold
    ({"keyword": "thc vape pen", "volume": 3300, "kd": 45, "intent": "informational"}, "Hold"),
])
def test_priority_thresholds(rule_payload, keyword, expected_priority):
    r = score_keyword(keyword)
    assert r["priority"] == expected_priority, (
        f"keyword={keyword['keyword']!r}, expected={expected_priority}, "
        f"got={r['priority']}, total={r['scores']['total']}, components={r['scores']}"
    )


# ---------- total = clamp(0, maxScore) ----------

def test_total_clamped_to_zero(rule_payload):
    """当 hold 严重扣分时 total 可能 ≤ 0，应 clamp 到 0。"""
    r = score_keyword({"keyword": "thc vape pen", "volume": 1, "kd": 100, "intent": "informational"})
    # demand ≈ 0, difficulty = 20-23 = -3 → clamp 0
    # commercial = 0, content = 0, siteFit = 14
    # 0+0+0+0+14-28 = -14 → clamp 0
    assert r["scores"]["total"] >= 0
    assert r["scores"]["total"] <= 100


def test_total_clamped_to_maxscore(rule_payload):
    """极端高 demand/difficulty 时 total ≤ maxScore (100)。"""
    r = score_keyword({"keyword": "x", "volume": 9999999, "kd": 0, "intent": "commercial, transactional"})
    assert r["scores"]["total"] <= 100


# ---------- 评分公式与原 Node.js 等价性（核心回归） ----------

def test_formula_matches_node_js_baseline(rule_payload):
    """关键回归测试：score 公式跟原 Node.js 的硬编码数值一致。

    原 Node.js (scorer.mjs):
      demand = clamp(round(log10(volume+1) * 6), 0, 20)
      difficulty = clamp(round(20 - kd * 0.23), 0, 20)
      commercial = clamp(15+11+4+2, 3, 20)  # 全 intent + comparison + main_blog
      content = clamp(11+6+4, 5, 20)
      siteFit = 18 (主站)
      risk = 0 (无 risk)
      total = round((sum/92) * 100)
    """
    # 复现原 scorer.mjs 行为
    volume, kd = 10000, 20
    demand_expected = round(math.log10(volume + 1) * 6)
    difficulty_expected = round(20 - kd * 0.23)

    r = score_keyword({"keyword": "x", "volume": volume, "kd": kd, "intent": "commercial, transactional"})
    assert r["scores"]["demand"] == min(20, max(0, demand_expected))
    assert r["scores"]["difficulty"] == min(20, max(0, difficulty_expected))