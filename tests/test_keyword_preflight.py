from __future__ import annotations

from app.engine.csv import normalize_imported_keywords
from app.engine.keyword_preflight import preflight_keyword
from app.services.keyword_service import import_and_analyze_csv, importable_keywords, import_summary


def test_preflight_normalizes_intent_and_query_shape(rule_payload):
    result = preflight_keyword({
        "keyword": "best running shoes near me",
        "database": "us",
        "volume": 1000,
        "kd": 40,
        "intent": "C",
    })

    assert result["preflightStatus"] == "ready"
    assert result["normalizedIntent"] == "commercial"
    assert result["keywordType"] == "local"
    assert result["isLocal"] is True


def test_preflight_marks_bad_metrics_invalid(rule_payload):
    result = preflight_keyword({"keyword": "x", "database": "us", "volume": 10, "kd": 101})

    assert result["preflightStatus"] == "invalid"
    assert "KD" in result["preflightReason"]


def test_csv_keeps_semrush_metrics_and_raw_columns(rule_payload):
    rows = normalize_imported_keywords([
        ["Keyword", "Database", "Volume", "KD %", "Intent", "Trend", "Potential Traffic", "SERP Features", "Extra"],
        ["buy shoes", "us", "1.2K", "35", "T", "12.5", "3.4K", "Shopping, Reviews", "keep me"],
    ])

    assert rows[0]["volume"] == 1200
    assert rows[0]["intent"] == "T"
    assert rows[0]["potentialTraffic"] == 3400
    assert rows[0]["raw"]["extra"] == "keep me"


def test_semrush_keyword_magic_headers_without_database_are_supported(rule_payload):
    rows = normalize_imported_keywords([
        [
            "Keyword", "Intent", "Volume", "Trend", "Potential Traffic",
            "Personal Kewword Difficulty", "Kewword Difficulty", "CPC (USD)",
            "Competitive Density", "SERP Features", "Number of Results", "Positions",
        ],
        ["buy vape online", "T", "1,000", "12.5", "3,400", "22", "35", "2.50", "0.33", "Shopping", "1,200,000", "1-10"],
    ])

    assert rows[0]["pkd"] == 22
    assert rows[0]["kd"] == 35
    assert rows[0]["serpResults"] == 1200000
    assert rows[0]["positions"] == "1-10"


def test_selected_market_supplies_missing_database_before_preflight(rule_payload):
    rows = import_and_analyze_csv(
        "Keyword,Intent,Volume,Kewword Difficulty\n""buy vape online"",T,1000,35",
        {"market": "US / English"},
    )

    assert rows[0]["database"] == "us"
    assert rows[0]["preflightStatus"] == "ready"


def test_import_pool_excludes_invalid_hold_and_out_of_scope_rows(rule_payload):
    rows = [
        {"keyword": "good keyword", "preflightStatus": "ready", "scopeStatus": "relevant", "priority": "P2", "status": "imported"},
        {"keyword": "bad metric", "preflightStatus": "invalid", "preflightReason": "KD 不合法"},
        {"keyword": "unrelated", "preflightStatus": "ready", "scopeStatus": "irrelevant", "reason": "无关词"},
        {"keyword": "risk", "preflightStatus": "ready", "scopeStatus": "relevant", "priority": "Hold", "status": "imported", "reason": "风险词"},
    ]

    accepted = importable_keywords(rows)
    summary = import_summary(rows, accepted)

    assert [row["keyword"] for row in accepted] == ["good keyword"]
    assert summary["accepted"] == 1
    assert summary["rejected"] == 3
