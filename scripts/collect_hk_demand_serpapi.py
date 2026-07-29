"""Collect Hong Kong demand research data from SerpAPI without exposing the key.

This is a filesystem export workflow. It intentionally does not write Trends,
Autocomplete, or expanded Google Search payloads into ``serp_snapshots`` because
the current table is designed for the narrower Google organic-results contract.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


SEARCH_ENDPOINT = "https://serpapi.com/search.json"
ACCOUNT_ENDPOINT = "https://serpapi.com/account.json"
ARCHIVE_ENDPOINT = "https://serpapi.com/searches/{search_id}.json"
EXPECTED_SEARCHES = 30

TREND_GROUPS = [
    {
        "id": "group_1",
        "queries": ["漏尿", "陰道鬆弛", "陰道乾澀", "私密美白", "小陰唇整形"],
    },
    {
        "id": "group_2",
        "queries": ["漏尿", "盆底肌", "私密緊緻", "性交疼痛", "女性高潮"],
    },
]

RELATED_QUERY_SEEDS = [
    "漏尿",
    "陰道鬆弛",
    "陰道乾澀",
    "私密美白",
    "小陰唇整形",
    "女性高潮",
]

AUTOCOMPLETE_QUERIES = [
    "漏尿",
    "盆底肌",
    "陰道鬆弛",
    "私密緊緻",
    "陰道乾澀",
    "性交疼痛",
    "私密美白",
    "小陰唇",
    "女性高潮",
    "深圳 私密",
]

GOOGLE_SEARCH_QUERIES = [
    "香港 漏尿治療",
    "香港 盆底肌修復",
    "香港 陰道緊緻",
    "香港 陰道乾澀治療",
    "香港 私密處美白",
    "香港 小陰唇整形",
    "香港 女性性功能治療",
    "深圳 漏尿治療",
    "深圳 私密緊緻",
    "深圳 私密處療程",
    "深圳 小陰唇整形",
    "香港人 深圳 私密療程",
]

OFFICIAL_DOCS = {
    "google_trends": "https://serpapi.com/google-trends-api",
    "related_queries": "https://serpapi.com/google-trends-related-queries",
    "google_autocomplete": "https://serpapi.com/google-autocomplete-api",
    "google_search": "https://serpapi.com/search-api",
}

SECRET_URL_RE = re.compile(r"([?&]api_key=)[^&]+", re.IGNORECASE)


class SerpApiError(RuntimeError):
    """A SerpAPI request failed without leaking request credentials."""


def load_serpapi_key(env_path: Path) -> str:
    if not env_path.exists():
        raise SerpApiError(f"Missing env file: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip().upper() == "SERPAPI_KEY":
            key = value.strip().strip("\"'")
            if key:
                return key
    raise SerpApiError("SERPAPI_KEY is not configured in .env")


def sanitize(value: Any) -> Any:
    """Remove API credentials from nested responses and URLs."""
    if isinstance(value, dict):
        return {
            key: sanitize(item)
            for key, item in value.items()
            if key.lower() not in {"api_key", "apikey"}
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return SECRET_URL_RE.sub(r"\1[REDACTED]", value)
    return value


def request_json(endpoint: str, params: dict[str, Any], api_key: str) -> dict[str, Any]:
    request_params = {**params, "api_key": api_key}
    url = f"{endpoint}?{urlencode(request_params)}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "seo2.0-hk-demand-research/1.0",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise SerpApiError(
            f"SerpAPI HTTP {exc.code} for engine={params.get('engine', 'account')}: "
            f"{sanitize(body)}"
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SerpApiError(
            f"SerpAPI request failed for engine={params.get('engine', 'account')}: {exc}"
        ) from exc

    payload = sanitize(payload)
    return payload


def _is_no_results_error(payload: dict[str, Any]) -> bool:
    error = str(payload.get("error") or "")
    return bool(
        re.search(
            r"hasn['’]?t returned any results|no results",
            error,
            flags=re.IGNORECASE,
        )
    )


def search_json(
    params: dict[str, Any],
    api_key: str,
    *,
    poll_interval_seconds: float = 2.0,
    poll_timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    """Submit a search asynchronously and poll its archive record.

    Archive polling does not submit duplicate searches and avoids holding one
    HTTP connection open while slow Google Trends jobs finish.
    """
    submitted = request_json(
        SEARCH_ENDPOINT,
        {**params, "async": "true"},
        api_key,
    )
    if submitted.get("error"):
        if _is_no_results_error(submitted):
            submitted["collection_status"] = "no_results"
            return submitted
        raise SerpApiError(f"SerpAPI returned an error: {submitted['error']}")
    metadata = submitted.get("search_metadata") or {}
    status = str(metadata.get("status") or "")
    if status == "Success":
        return submitted
    search_id = metadata.get("id")
    if not search_id:
        raise SerpApiError(
            f"SerpAPI async submission returned no search ID for engine={params.get('engine')}"
        )

    deadline = time.monotonic() + poll_timeout_seconds
    latest = submitted
    while time.monotonic() < deadline:
        time.sleep(poll_interval_seconds)
        latest = request_json(
            ARCHIVE_ENDPOINT.format(search_id=search_id),
            {},
            api_key,
        )
        if latest.get("error"):
            if _is_no_results_error(latest):
                latest["collection_status"] = "no_results"
                return latest
            raise SerpApiError(f"SerpAPI returned an error: {latest['error']}")
        status = str((latest.get("search_metadata") or {}).get("status") or "")
        if status == "Success":
            return latest
        if status and status not in {"Queued", "Processing"}:
            raise SerpApiError(
                f"SerpAPI archive search {search_id} ended with status={status}"
            )
    raise SerpApiError(
        f"SerpAPI archive search {search_id} did not finish within "
        f"{int(poll_timeout_seconds)} seconds"
    )


def selected_account_fields(account: dict[str, Any]) -> dict[str, Any]:
    return {
        key: account.get(key)
        for key in (
            "account_status",
            "plan_name",
            "searches_per_month",
            "plan_searches_left",
            "total_searches_left",
            "plan_renewal_date",
        )
    }


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def summarize_trends(payload: dict[str, Any]) -> dict[str, Any]:
    timeline = (
        payload.get("interest_over_time", {}).get("timeline_data", [])
        if isinstance(payload.get("interest_over_time"), dict)
        else []
    )
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for point in timeline:
        timestamp = int(point.get("timestamp") or 0)
        for item in point.get("values") or []:
            query = str(item.get("query") or "")
            extracted = item.get("extracted_value")
            try:
                numeric = float(extracted)
            except (TypeError, ValueError):
                continue
            if query:
                series[query].append((timestamp, numeric))

    summaries: list[dict[str, Any]] = []
    for query, points in series.items():
        values = [value for _, value in points]
        segment = max(1, len(values) // 3)
        first_mean = _mean(values[:segment])
        recent_mean = _mean(values[-segment:])
        overall_mean = _mean(values)
        absolute_change = recent_mean - first_mean
        material_change = max(5.0, overall_mean * 0.15)
        if overall_mean == 0:
            direction = "no_signal"
        elif absolute_change >= material_change:
            direction = "up"
        elif absolute_change <= -material_change:
            direction = "down"
        else:
            direction = "flat"

        monthly_values: dict[int, list[float]] = defaultdict(list)
        for timestamp, value in points:
            if timestamp:
                month = datetime.fromtimestamp(timestamp, UTC).month
                monthly_values[month].append(value)
        monthly_average = {
            str(month): round(_mean(month_values), 2)
            for month, month_values in sorted(monthly_values.items())
        }
        month_means = list(monthly_average.values())
        coefficient_of_variation = (
            statistics.pstdev(month_means) / _mean(month_means)
            if len(month_means) > 1 and _mean(month_means) > 0
            else 0.0
        )
        peak_month = (
            max(monthly_average, key=monthly_average.get) if monthly_average else None
        )
        trough_month = (
            min(monthly_average, key=monthly_average.get) if monthly_average else None
        )

        summaries.append(
            {
                "query": query,
                "point_count": len(points),
                "first_period_mean": round(first_mean, 2),
                "recent_period_mean": round(recent_mean, 2),
                "overall_mean": round(overall_mean, 2),
                "direction": direction,
                "recent_growth_flag": first_mean <= 1 and recent_mean >= 3,
                "seasonality_signal": coefficient_of_variation >= 0.25,
                "monthly_coefficient_of_variation": round(coefficient_of_variation, 3),
                "peak_month": peak_month,
                "trough_month": trough_month,
                "monthly_average": monthly_average,
            }
        )
    return {"timeline_points": len(timeline), "queries": summaries}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("places"), list):
            return value["places"]
        return [value]
    return []


def _domain(link: str | None) -> str | None:
    if not link:
        return None
    try:
        return urlparse(link).netloc.lower() or None
    except ValueError:
        return None


def summarize_google_search(payload: dict[str, Any]) -> dict[str, Any]:
    ad_blocks: list[dict[str, Any]] = []
    for field in ("ads", "top_ads", "bottom_ads", "inline_ads", "local_ads"):
        for item in _as_list(payload.get(field)):
            if isinstance(item, dict):
                ad_blocks.append({"placement": field, **item})

    local_blocks: list[dict[str, Any]] = []
    for field in (
        "local_results",
        "places_results",
        "local_ads",
        "local_services",
    ):
        for item in _as_list(payload.get(field)):
            if isinstance(item, dict):
                local_blocks.append({"section": field, **item})

    organic = [
        item
        for item in _as_list(payload.get("organic_results"))[:10]
        if isinstance(item, dict)
    ]
    organic_compact = [
        {
            **item,
            "domain": _domain(item.get("link")),
            "mentions_shenzhen": bool(
                re.search(
                    r"深圳|shenzhen",
                    json.dumps(item, ensure_ascii=False),
                    flags=re.IGNORECASE,
                )
            ),
        }
        for item in organic
    ]

    shenzhen_matches: list[dict[str, Any]] = []
    for kind, items in (("organic", organic_compact), ("local", local_blocks)):
        for item in items:
            match_source = {
                key: value
                for key, value in item.items()
                if key != "mentions_shenzhen"
            }
            text = json.dumps(match_source, ensure_ascii=False)
            if re.search(r"深圳|shenzhen", text, flags=re.IGNORECASE):
                shenzhen_matches.append(
                    {
                        "kind": kind,
                        "title": item.get("title")
                        or item.get("name")
                        or item.get("place_name"),
                        "link": item.get("link") or item.get("website"),
                        "domain": _domain(item.get("link") or item.get("website")),
                    }
                )

    advertisers = [
        {
            "placement": item.get("placement"),
            "title": item.get("title") or item.get("name"),
            "link": item.get("link"),
            "domain": _domain(item.get("link")),
        }
        for item in ad_blocks
    ]

    return {
        "search_id": payload.get("search_metadata", {}).get("id"),
        "status": payload.get("search_metadata", {}).get("status"),
        "ad_count": len(ad_blocks),
        "advertisers": advertisers,
        "organic_results": organic_compact,
        "related_questions": _as_list(payload.get("related_questions")),
        "related_searches": _as_list(payload.get("related_searches")),
        "local_results": local_blocks,
        "shenzhen_in_results": bool(shenzhen_matches),
        "shenzhen_matches": shenzhen_matches,
    }


def progress(label: str, index: int, total: int) -> None:
    print(f"[{index:02d}/{total:02d}] {label}", flush=True)


def write_checkpoint(
    output_dir: Path,
    *,
    completed: int,
    trends_groups: list[dict[str, Any]],
    related_queries: list[dict[str, Any]],
    autocomplete: list[dict[str, Any]],
    search_results: list[dict[str, Any]],
) -> None:
    atomic_write_json(
        output_dir / "collection-checkpoint.json",
        {
            "updated_at": datetime.now(UTC).isoformat(),
            "requests_completed": completed,
            "trend_groups": trends_groups,
            "related_queries": related_queries,
            "autocomplete": autocomplete,
            "google_searches": search_results,
        },
    )


def collect(api_key: str, output_dir: Path, delay_seconds: float) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    account = request_json(ACCOUNT_ENDPOINT, {}, api_key)
    if account.get("error"):
        raise SerpApiError(f"SerpAPI account check failed: {account['error']}")
    account_summary = selected_account_fields(account)
    searches_left = int(account.get("total_searches_left") or 0)
    if searches_left < EXPECTED_SEARCHES:
        raise SerpApiError(
            f"Insufficient SerpAPI quota: {searches_left} left, "
            f"{EXPECTED_SEARCHES} required"
        )

    checkpoint_path = output_dir / "collection-checkpoint.json"
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    else:
        checkpoint = {}
    trends_groups: list[dict[str, Any]] = checkpoint.get("trend_groups") or []
    related_queries: list[dict[str, Any]] = checkpoint.get("related_queries") or []
    autocomplete: list[dict[str, Any]] = checkpoint.get("autocomplete") or []
    search_results: list[dict[str, Any]] = checkpoint.get("google_searches") or []
    completed = (
        len(trends_groups)
        + len(related_queries)
        + len(autocomplete)
        + len(search_results)
    )
    completed_group_ids = {item.get("group_id") for item in trends_groups}
    completed_related = {item.get("seed") for item in related_queries}
    completed_autocomplete = {item.get("query") for item in autocomplete}
    completed_searches = {item.get("query") for item in search_results}

    for group in TREND_GROUPS:
        if group["id"] in completed_group_ids:
            print(f"[checkpoint] Google Trends TIMESERIES {group['id']}", flush=True)
            continue
        params = {
            "engine": "google_trends",
            "q": ",".join(group["queries"]),
            "geo": "HK",
            "hl": "zh-TW",
            "date": "today 5-y",
            "data_type": "TIMESERIES",
        }
        completed += 1
        progress(f"Google Trends TIMESERIES {group['id']}", completed, EXPECTED_SEARCHES)
        raw = search_json(params, api_key)
        trends_groups.append(
            {
                "group_id": group["id"],
                "queries": group["queries"],
                "request_parameters": params,
                "analysis": summarize_trends(raw),
                "raw_response": raw,
            }
        )
        write_checkpoint(
            output_dir,
            completed=completed,
            trends_groups=trends_groups,
            related_queries=related_queries,
            autocomplete=autocomplete,
            search_results=search_results,
        )
        time.sleep(delay_seconds)

    for seed in RELATED_QUERY_SEEDS:
        if seed in completed_related:
            print(f"[checkpoint] Related Queries {seed}", flush=True)
            continue
        params = {
            "engine": "google_trends",
            "q": seed,
            "geo": "HK",
            "hl": "zh-TW",
            "date": "today 5-y",
            "data_type": "RELATED_QUERIES",
        }
        completed += 1
        progress(f"Related Queries {seed}", completed, EXPECTED_SEARCHES)
        raw = search_json(params, api_key)
        related = raw.get("related_queries") or {}
        related_queries.append(
            {
                "seed": seed,
                "request_parameters": params,
                "top": _as_list(related.get("top")),
                "rising": _as_list(related.get("rising")),
                "raw_response": raw,
            }
        )
        write_checkpoint(
            output_dir,
            completed=completed,
            trends_groups=trends_groups,
            related_queries=related_queries,
            autocomplete=autocomplete,
            search_results=search_results,
        )
        time.sleep(delay_seconds)

    for query in AUTOCOMPLETE_QUERIES:
        if query in completed_autocomplete:
            print(f"[checkpoint] Google Autocomplete {query}", flush=True)
            continue
        params = {
            "engine": "google_autocomplete",
            "q": query,
            "gl": "hk",
            "hl": "zh-TW",
        }
        completed += 1
        progress(f"Google Autocomplete {query}", completed, EXPECTED_SEARCHES)
        raw = search_json(params, api_key)
        autocomplete.append(
            {
                "query": query,
                "request_parameters": params,
                "suggestions": _as_list(raw.get("suggestions")),
                "raw_response": raw,
            }
        )
        write_checkpoint(
            output_dir,
            completed=completed,
            trends_groups=trends_groups,
            related_queries=related_queries,
            autocomplete=autocomplete,
            search_results=search_results,
        )
        time.sleep(delay_seconds)

    for query in GOOGLE_SEARCH_QUERIES:
        if query in completed_searches:
            print(f"[checkpoint] Google Search {query}", flush=True)
            continue
        params = {
            "engine": "google",
            "q": query,
            "location": "Hong Kong",
            "gl": "hk",
            "hl": "zh-TW",
            "google_domain": "google.com.hk",
            "device": "mobile",
            "num": 10,
        }
        completed += 1
        progress(f"Google Search {query}", completed, EXPECTED_SEARCHES)
        raw = search_json(params, api_key)
        search_results.append(
            {
                "query": query,
                "request_parameters": params,
                "summary": summarize_google_search(raw),
                "raw_response": raw,
            }
        )
        write_checkpoint(
            output_dir,
            completed=completed,
            trends_groups=trends_groups,
            related_queries=related_queries,
            autocomplete=autocomplete,
            search_results=search_results,
        )
        time.sleep(delay_seconds)

    account_after = request_json(ACCOUNT_ENDPOINT, {}, api_key)
    if account_after.get("error"):
        raise SerpApiError(f"SerpAPI final account check failed: {account_after['error']}")
    ended_at = datetime.now(UTC)
    shared_metadata = {
        "generated_at": ended_at.isoformat(),
        "started_at": started_at.isoformat(),
        "market": "Hong Kong",
        "language": "zh-TW",
        "requests_expected": EXPECTED_SEARCHES,
        "requests_completed": completed,
        "account_before": account_summary,
        "account_after": selected_account_fields(account_after),
        "quota_consumed": searches_left
        - int(account_after.get("total_searches_left") or searches_left),
        "notes": [
            "Google Trends values are relative 0-100 indices, not absolute search volume.",
            "The repeated query 漏尿 is the cross-group anchor. Do not compare the two groups without anchor-based normalization.",
            "Trend direction and seasonality fields are descriptive heuristics; raw timeline data is retained for re-analysis.",
            "No SerpAPI key is stored in any output.",
        ],
        "official_documentation": OFFICIAL_DOCS,
    }

    trends_payload = {
        "metadata": shared_metadata,
        "trend_groups": trends_groups,
        "related_queries": related_queries,
        "autocomplete": autocomplete,
    }
    google_payload = {
        "metadata": shared_metadata,
        "searches": search_results,
    }

    trends_path = output_dir / "SerpAPI-Trends和Related Queries.json"
    google_path = output_dir / "SerpAPI-Google搜索结果.json"
    atomic_write_json(trends_path, trends_payload)
    atomic_write_json(google_path, google_payload)

    return {
        "trends_path": str(trends_path.resolve()),
        "google_path": str(google_path.resolve()),
        "requests_completed": completed,
        "quota_consumed": shared_metadata["quota_consumed"],
        "account_after": shared_metadata["account_after"],
        "trend_group_count": len(trends_groups),
        "related_query_count": len(related_queries),
        "autocomplete_count": len(autocomplete),
        "google_search_count": len(search_results),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to .env containing SERPAPI_KEY.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for sanitized JSON exports.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.25,
        help="Small delay between searches; no automatic paid retries are made.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not math.isfinite(args.delay_seconds) or args.delay_seconds < 0:
        print("delay-seconds must be a non-negative finite number", file=sys.stderr)
        return 2
    try:
        api_key = load_serpapi_key(args.env_file)
        result = collect(api_key, args.output_dir, args.delay_seconds)
    except SerpApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
