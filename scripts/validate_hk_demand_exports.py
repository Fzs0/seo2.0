"""Validate the Hong Kong Semrush and SerpAPI research exports."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from collect_hk_demand_serpapi import (
    AUTOCOMPLETE_QUERIES,
    GOOGLE_SEARCH_QUERIES,
    RELATED_QUERY_SEEDS,
    TREND_GROUPS,
    load_serpapi_key,
)


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: validate_hk_demand_exports.py OUTPUT_DIR ENV_FILE", file=sys.stderr)
        return 2

    output_dir = Path(sys.argv[1])
    env_file = Path(sys.argv[2])
    semrush_path = output_dir / "Semrush关键词数据.csv"
    trends_path = output_dir / "SerpAPI-Trends和Related Queries.json"
    google_path = output_dir / "SerpAPI-Google搜索结果.json"

    required = [semrush_path, trends_path, google_path]
    for path in required:
        if not path.exists() or path.stat().st_size == 0:
            raise AssertionError(f"Missing or empty output: {path}")

    api_key = load_serpapi_key(env_file)
    for path in (trends_path, google_path):
        text = path.read_text(encoding="utf-8")
        if api_key in text:
            raise AssertionError(f"API key leaked into {path.name}")
        unredacted = re.search(r"[?&]api_key=(?!\[REDACTED\])[^&\"\s]+", text)
        if unredacted:
            raise AssertionError(f"Unredacted api_key URL parameter in {path.name}")

    with semrush_path.open("r", encoding="utf-8-sig", newline="") as handle:
        semrush_rows = list(csv.DictReader(handle))
    semrush_sources = sorted({row["Source File"] for row in semrush_rows})
    assert_equal(len(semrush_rows), 120, "Semrush row count")
    assert_equal(len(semrush_sources), 10, "Semrush source count")

    trends = json.loads(trends_path.read_text(encoding="utf-8"))
    google = json.loads(google_path.read_text(encoding="utf-8"))

    assert_equal(len(trends["trend_groups"]), 2, "Trend group count")
    assert_equal(len(trends["related_queries"]), 6, "Related Queries count")
    assert_equal(len(trends["autocomplete"]), 10, "Autocomplete count")
    assert_equal(len(google["searches"]), 12, "Google Search count")
    assert_equal(
        [item["queries"] for item in trends["trend_groups"]],
        [item["queries"] for item in TREND_GROUPS],
        "Trend group queries",
    )
    assert_equal(
        [item["seed"] for item in trends["related_queries"]],
        RELATED_QUERY_SEEDS,
        "Related Query seeds",
    )
    assert_equal(
        [item["query"] for item in trends["autocomplete"]],
        AUTOCOMPLETE_QUERIES,
        "Autocomplete queries",
    )
    assert_equal(
        [item["query"] for item in google["searches"]],
        GOOGLE_SEARCH_QUERIES,
        "Google Search queries",
    )

    expected_common = {"market": "Hong Kong", "language": "zh-TW"}
    for payload in (trends, google):
        for key, value in expected_common.items():
            assert_equal(payload["metadata"].get(key), value, f"metadata {key}")
        assert_equal(
            payload["metadata"].get("requests_completed"),
            30,
            "metadata requests_completed",
        )

    related_summary = [
        {
            "seed": item["seed"],
            "top": len(item["top"]),
            "rising": len(item["rising"]),
            "status": item["raw_response"].get("collection_status")
            or item["raw_response"].get("search_metadata", {}).get("status"),
        }
        for item in trends["related_queries"]
    ]
    autocomplete_summary = [
        {
            "query": item["query"],
            "suggestions": len(item["suggestions"]),
            "status": item["raw_response"].get("collection_status")
            or item["raw_response"].get("search_metadata", {}).get("status"),
        }
        for item in trends["autocomplete"]
    ]
    google_summary = [
        {
            "query": item["query"],
            "ads": item["summary"]["ad_count"],
            "organic": len(item["summary"]["organic_results"]),
            "paa": len(item["summary"]["related_questions"]),
            "related_searches": len(item["summary"]["related_searches"]),
            "local_results": len(item["summary"]["local_results"]),
            "shenzhen_in_results": item["summary"]["shenzhen_in_results"],
            "status": item["summary"]["status"],
        }
        for item in google["searches"]
    ]

    result = {
        "validation": "passed",
        "files": {
            path.name: {"bytes": path.stat().st_size}
            for path in required
        },
        "semrush": {
            "rows": len(semrush_rows),
            "sources": len(semrush_sources),
        },
        "serpapi": {
            "requests_completed": trends["metadata"]["requests_completed"],
            "quota_consumed_in_completed_run": trends["metadata"]["quota_consumed"],
            "quota_remaining": trends["metadata"]["account_after"][
                "total_searches_left"
            ],
            "api_key_leak_scan": "passed",
            "trend_groups": [
                {
                    "group_id": item["group_id"],
                    "timeline_points": item["analysis"]["timeline_points"],
                    "query_summaries": item["analysis"]["queries"],
                }
                for item in trends["trend_groups"]
            ],
            "related_queries": related_summary,
            "autocomplete": autocomplete_summary,
            "google_searches": google_summary,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
