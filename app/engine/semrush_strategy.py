"""Semrush Keyword Strategy Builder 的只读解析和页面簇预览。"""
from __future__ import annotations

import base64
import hashlib
import re
import zipfile
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.engine.xlsx import xlsx_buffer_to_rows

_TOP10_RE = re.compile(r"^competitor\s+on\s+top\s*10\s*#?\s*(\d+)$", re.I)
_REFERENCE_RE = re.compile(r"^content\s+reference\s+(\d+)$", re.I)

_DEFAULT_CLUSTER_POLICY = {
    "minimumEvidenceUrls": 7,
    "top7StrongOverlap": 3,
    "top10StrongOverlap": 4,
    "top10AcceptableOverlap": 3,
}


def _key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _number(value: Any) -> float:
    cleaned = re.sub(r"[$%,\s]", "", str(value or ""))
    multiplier = 1
    if cleaned.casefold().endswith("k"):
        multiplier, cleaned = 1_000, cleaned[:-1]
    elif cleaned.casefold().endswith("m"):
        multiplier, cleaned = 1_000_000, cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return 0


def _split_labels(value: Any) -> list[str]:
    return [part.strip() for part in re.split(r"[,;/]", str(value or "")) if part.strip()]


def _normalized_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    hostname = (parsed.hostname or "").casefold()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    port = parsed.port
    netloc = hostname if not port or (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443) else f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_") and key.casefold() not in {"gclid", "fbclid"}
        )
    )
    return urlunsplit(("https", netloc, path, query, "")) if netloc else raw.casefold().rstrip("/")


def _cluster_policy(policy: dict[str, Any] | None) -> tuple[str, dict[str, int]]:
    source = policy or {}
    rules = source.get("pageClusterValidation", source)
    if not isinstance(rules, dict):
        rules = {}
    merged = {**_DEFAULT_CLUSTER_POLICY, **{key: rules[key] for key in _DEFAULT_CLUSTER_POLICY if key in rules}}
    return str(source.get("version") or rules.get("ruleVersion") or "1.0.0"), {key: int(value) for key, value in merged.items()}


def _validate_cluster(items: list[dict[str, Any]], rules: dict[str, int]) -> dict[str, str]:
    url_sets = [
        {normalized for url in item["top10Urls"] if (normalized := _normalized_url(url))}
        for item in items
    ]
    minimum = rules["minimumEvidenceUrls"]
    if any(len(urls) < minimum for urls in url_sets):
        center = max(items, key=lambda item: (item["volume"], -item["kd"], item["keyword"]))
        return {
            "validationStatus": "split_review",
            "validationCenterKeyword": center["keyword"],
            "validationReason": f"至少一个关键词的 SERP 证据少于 {minimum} 条，需补查后再判断。",
        }

    # 保留导出排名顺序计算 TOP 7；TOP 10 使用完整集合。
    ordered_urls = [
        list(dict.fromkeys(_normalized_url(url) for url in item["top10Urls"] if _normalized_url(url)))
        for item in items
    ]

    def ranked_overlap(left: int, right: int, limit: int) -> int:
        return len(set(ordered_urls[left][:limit]) & set(ordered_urls[right][:limit]))

    center_index = max(
        range(len(items)),
        key=lambda index: (
            sum(2 * ranked_overlap(index, other, 7) + ranked_overlap(index, other, 10) for other in range(len(items)) if other != index),
            items[index]["volume"],
            -items[index]["kd"],
            items[index]["keyword"],
        ),
    )

    def strong(left: int, right: int) -> bool:
        return (
            ranked_overlap(left, right, 7) >= rules["top7StrongOverlap"]
            or ranked_overlap(left, right, 10) >= rules["top10StrongOverlap"]
        )

    others = [index for index in range(len(items)) if index != center_index]
    if all(strong(center_index, index) for index in others):
        status = "validated"
        reason = "所有关键词都与中心词达到强 SERP 重合，可进入页面簇 AI。"
    elif all(ranked_overlap(center_index, index, 10) >= rules["top10AcceptableOverlap"] for index in others):
        status = "provisional"
        reason = "所有关键词都与中心词达到可接受重合，暂保留为一个页面簇。"
    else:
        reached = {center_index}
        pending = [center_index]
        while pending:
            current = pending.pop()
            for index in range(len(items)):
                if index not in reached and strong(current, index):
                    reached.add(index)
                    pending.append(index)
        if len(reached) == len(items):
            status = "bridge_review"
            reason = "页面簇依靠中间关键词连通，但部分词与中心词边界较弱，需人工复核。"
        else:
            status = "split_review"
            reason = "页面簇存在无法通过强 SERP 重合连通的关键词，需补查或拆簇。"
    return {
        "validationStatus": status,
        "validationCenterKeyword": items[center_index]["keyword"],
        "validationReason": reason,
    }


def _cluster_id(database: str, page: str) -> str:
    return hashlib.sha1(f"{_key(database)}|{_key(page)}".encode("utf-8")).hexdigest()[:16]


def _headers(rows: list[list[str]]) -> tuple[int, list[str]]:
    for index, row in enumerate(rows[:20]):
        values = [_key(value) for value in row]
        if {"keyword", "page", "topic", "page type"}.issubset(values):
            return index, [str(value or "").strip() for value in row]
    raise ValueError("未找到包含 Keyword、Page、Topic、Page type 的 Semrush Keywords 表头")


def _columns(headers: list[str]) -> dict[str, int]:
    return {_key(header): index for index, header in enumerate(headers) if _key(header)}


def _value(row: list[str], columns: dict[str, int], name: str) -> str:
    index = columns.get(_key(name))
    return row[index].strip() if index is not None and index < len(row) else ""


def _matching_columns(columns: dict[str, int], pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    result = []
    for name, index in columns.items():
        match = pattern.match(name)
        if match:
            result.append((int(match.group(1)), index))
    return sorted(result)


def _raw_row(headers: list[str], row: list[str]) -> dict[str, str]:
    return {header: row[index] if index < len(row) else "" for index, header in enumerate(headers) if header}


def parse_semrush_strategy_rows(rows: list[list[str]]) -> list[dict[str, Any]]:
    """把 Keywords sheet 映射为页面簇保留的结构化行，不做通用聚类或数据库写入。"""
    header_index, headers = _headers(rows)
    columns = _columns(headers)
    top10_columns = _matching_columns(columns, _TOP10_RE)
    reference_columns = _matching_columns(columns, _REFERENCE_RE)
    result = []
    for row in rows[header_index + 1:]:
        keyword = _value(row, columns, "Keyword")
        if not keyword and not any(str(value or "").strip() for value in row):
            continue
        database = _value(row, columns, "Database")
        page = _value(row, columns, "Page")
        result.append(
            {
                "database": database,
                "keyword": keyword,
                "tags": _value(row, columns, "Tags"),
                "seedKeyword": _value(row, columns, "Seed keyword"),
                "page": page,
                "pageClusterId": _cluster_id(database, page),
                "topic": _value(row, columns, "Topic"),
                "pageType": _value(row, columns, "Page type"),
                "volume": _number(_value(row, columns, "Volume")),
                "kd": _number(_value(row, columns, "Keyword Difficulty")),
                "cpc": _number(_value(row, columns, "CPC (USD)")),
                "competitiveDensity": _number(_value(row, columns, "Competitive Density")),
                "serpResults": int(_number(_value(row, columns, "Number of Results"))),
                "intent": _value(row, columns, "Intent"),
                "serpFeatures": _split_labels(_value(row, columns, "SERP Features")),
                "trend": [_number(value) for value in _value(row, columns, "Trend").split(",") if value.strip()],
                "clickPotential": _number(_value(row, columns, "Click potential")),
                "contentReferences": [row[index].strip() for _, index in reference_columns if index < len(row) and row[index].strip()],
                "top10Urls": [row[index].strip() for _, index in top10_columns if index < len(row) and row[index].strip()],
                "raw": _raw_row(headers, row),
            }
        )
    return result


def _anomalies(rows: list[dict[str, Any]], clusters: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def add(code: str, label: str, cluster_ids: list[str]) -> None:
        if cluster_ids:
            findings.append({"code": code, "label": label, "count": len(cluster_ids), "clusterIds": cluster_ids[:20]})

    add("missing_page", "缺少 Page，无法确认页面簇", [cluster_id for cluster_id, items in clusters.items() if not items[0]["page"]])
    add("missing_keyword", "页面簇中存在空 Keyword", [cluster_id for cluster_id, items in clusters.items() if any(not item["keyword"] for item in items)])
    add("missing_topic", "缺少 Topic", [cluster_id for cluster_id, items in clusters.items() if any(not item["topic"] for item in items)])
    add("missing_page_type", "缺少 Page type", [cluster_id for cluster_id, items in clusters.items() if any(not item["pageType"] for item in items)])
    add(
        "unsupported_page_type",
        "Page type 不是 Pillar page 或 Sub page",
        [
            cluster_id
            for cluster_id, items in clusters.items()
            if any(_key(item["pageType"]) not in {"pillar page", "sub page"} for item in items if item["pageType"])
        ],
    )
    add("mixed_topic", "同一 Page 对应多个 Topic", [cluster_id for cluster_id, items in clusters.items() if len({_key(item["topic"]) for item in items if item["topic"]}) > 1])
    add("mixed_page_type", "同一 Page 对应多个 Page type", [cluster_id for cluster_id, items in clusters.items() if len({_key(item["pageType"]) for item in items if item["pageType"]}) > 1])
    add("missing_top10", "缺少 TOP 10 竞争 URL", [cluster_id for cluster_id, items in clusters.items() if not {url for item in items for url in item["top10Urls"]}])

    pages_by_keyword: dict[str, set[str]] = defaultdict(set)
    for item in rows:
        if item["keyword"]:
            pages_by_keyword[_key(item["keyword"])].add(_key(item["page"]))
    duplicate_keywords = sum(len(pages) - 1 for pages in pages_by_keyword.values() if len(pages) > 1)
    if duplicate_keywords:
        findings.append({"code": "keyword_in_multiple_pages", "label": "关键词出现在多个 Page 页面簇", "count": duplicate_keywords, "clusterIds": []})
    return findings


def preview_semrush_strategy_rows(rows: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    rule_version, cluster_rules = _cluster_policy(policy)
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        clusters[item["pageClusterId"]].append(item)

    cluster_rows = []
    for cluster_id, items in clusters.items():
        pages = sorted({item["page"] for item in items if item["page"]})
        topics = sorted({item["topic"] for item in items if item["topic"]})
        page_types = sorted({item["pageType"] for item in items if item["pageType"]})
        intents = Counter(label for item in items for label in _split_labels(item["intent"]))
        urls = list(dict.fromkeys(url for item in items for url in item["top10Urls"]))
        references = list(dict.fromkeys(reference for item in items for reference in item["contentReferences"]))
        cluster_rows.append(
            {
                "id": cluster_id,
                "page": pages[0] if pages else "",
                "topics": topics,
                "pageTypes": page_types,
                "keywordCount": len(items),
                "intentCounts": dict(intents),
                "volumeSum": round(sum(item["volume"] for item in items), 2),
                "kdAverage": round(sum(item["kd"] for item in items) / len(items), 2) if items else 0,
                "top10UrlCount": len(urls),
                "contentReferenceCount": len(references),
                **_validate_cluster(items, cluster_rules),
            }
        )
    cluster_rows.sort(key=lambda item: (-item["keywordCount"], item["page"]))
    valid_clusters = [item for item in cluster_rows if item["page"]]
    page_type_counts = Counter(page_type for item in valid_clusters for page_type in item["pageTypes"])
    intent_counts = Counter(label for item in rows for label in _split_labels(item["intent"]))
    missing_top10_rows = sum(not item["top10Urls"] for item in rows)
    validation_counts = Counter(item["validationStatus"] for item in valid_clusters)
    return {
        "rowCount": len(rows),
        "databases": sorted({_key(item["database"]) for item in rows if item["database"]}),
        "topicCount": len({_key(item["topic"]) for item in rows if item["topic"]}),
        "pageClusterCount": len(valid_clusters),
        "pageTypeCounts": dict(page_type_counts),
        "intentCounts": dict(intent_counts),
        "metrics": {
            "volumeSum": round(sum(item["volume"] for item in rows), 2),
            "kdAverage": round(sum(item["kd"] for item in rows) / len(rows), 2) if rows else 0,
            "kdMin": min((item["kd"] for item in rows), default=0),
            "kdMax": max((item["kd"] for item in rows), default=0),
            "top10Coverage": round((len(rows) - missing_top10_rows) / len(rows), 4) if rows else 0,
        },
        "clusterValidation": {
            "ruleVersion": rule_version,
            "counts": dict(validation_counts),
            "aiReadyCount": validation_counts["validated"] + validation_counts["provisional"],
            "reviewCount": validation_counts["bridge_review"] + validation_counts["split_review"],
        },
        "anomalies": _anomalies(rows, clusters),
        "clusters": cluster_rows,
    }


def preview_semrush_strategy_workbook(buf: bytes, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = parse_semrush_strategy_rows(xlsx_buffer_to_rows(buf, "Keywords"))
    return preview_semrush_strategy_rows(rows, policy)


def parse_semrush_strategy_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    filename = str(payload.get("filename") or "")
    if not filename.casefold().endswith(".xlsx"):
        raise ValueError("Semrush Strategy Builder 预览只接受 .xlsx 文件")
    content = payload.get("contentBase64") or ""
    if not content:
        raise ValueError("预览 .xlsx 必须以 contentBase64 传入二进制文件内容")
    try:
        raw = base64.b64decode(content, validate=True)
    except Exception as error:
        raise ValueError("contentBase64 无效") from error
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("文件超过 25 MB 预览限制")
    try:
        return parse_semrush_strategy_rows(xlsx_buffer_to_rows(raw, "Keywords"))
    except (KeyError, ValueError, zipfile.BadZipFile) as error:
        raise ValueError(f"无法解析 Semrush Strategy Builder 文件：{error}") from error


def preview_semrush_strategy_payload(payload: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    preview = preview_semrush_strategy_rows(parse_semrush_strategy_payload(payload), policy)
    return {"previewOnly": True, "sheet": "Keywords", **preview}
