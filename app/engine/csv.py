"""Semrush CSV / XLSX 解析。字段别名从 payload.csvColumnAliases 读。"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from app.engine.loader import get_store

_DEFAULT_ALIASES = {
    "keyword": ["keyword", "keywords", "query"],
    "volume": ["volume", "search volume", "volume total"],
    "difficulty": ["keyword difficulty", "kd", "difficulty"],
    "intent": ["intent", "search intent"],
    "database": ["database", "db", "country database"],
    "cpc": ["cpc", "cost per click"],
    "topic": ["topic", "topic cluster"],
    "pageGroup": ["page group", "pagegroup", "group"],
    "seedKeyword": ["seed keyword", "seed", "parent keyword"],
    "sourcePageType": ["source page type", "page type"],
    "serpFeatures": ["serp features", "features"],
    "url": ["url", "ranking url", "page"],
}


def parse_csv(text: str) -> list[list[str]]:
    """用 Python 标准 csv 模块解析，兼容引号转义。"""
    return list(csv.reader(io.StringIO(text or "")))


def _get_column(row: list[str], headers: list[str], field: str) -> str:
    aliases = [str(a).lower().strip() for a in (get_store().get(f"csvColumnAliases.{field}") or [])]
    aliases += [a for a in _DEFAULT_ALIASES.get(field, []) if a not in aliases]
    for alias in aliases:
        for idx, header in enumerate(headers):
            if header == alias:
                return row[idx] if idx < len(row) else ""
    for alias in aliases:
        for idx, header in enumerate(headers):
            if alias in header:
                return row[idx] if idx < len(row) else ""
    return ""


def _to_number(value: Any) -> float:
    if value is None:
        return 0
    cleaned = re.sub(r"[$%,\s]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return 0


def _make_id(keyword: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (keyword or "keyword").lower()).strip("-")
    return f"{slug}-{index}"


def _find_header_row(rows: list[list[str]]) -> int:
    for idx in range(min(len(rows), 30)):
        headers = [str(h or "").lower().strip() for h in rows[idx]]
        if _get_column(rows[idx], headers, "keyword"):
            return idx
    return 0


def normalize_imported_keywords(rows: list[list[str]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    header_idx = _find_header_row(rows)
    headers = [str(h or "").lower().strip() for h in rows[header_idx]]
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(rows[header_idx + 1:]):
        keyword = _get_column(row, headers, "keyword").strip()
        if not keyword:
            continue
        out.append(
            {
                "id": _make_id(keyword, idx),
                "keyword": keyword,
                "database": _get_column(row, headers, "database"),
                "topicCluster": _get_column(row, headers, "topic") or _get_column(row, headers, "pageGroup") or keyword,
                "seedKeyword": _get_column(row, headers, "seedKeyword"),
                "pageGroup": _get_column(row, headers, "pageGroup"),
                "sourcePageType": _get_column(row, headers, "sourcePageType"),
                "serpFeatures": _get_column(row, headers, "serpFeatures"),
                "volume": _to_number(_get_column(row, headers, "volume")),
                "kd": _to_number(_get_column(row, headers, "difficulty")),
                "cpc": _to_number(_get_column(row, headers, "cpc")),
                "intent": _get_column(row, headers, "intent") or "unknown",
                "url": _get_column(row, headers, "url"),
            }
        )
    return out


def import_csv_keywords(csv_text: str) -> list[dict[str, Any]]:
    return normalize_imported_keywords(parse_csv(csv_text))


def import_keywords_from_file(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """{ filename, contentBase64?, contentText? } → 行。XLSX/TSV/CSV 通用。"""
    import base64

    filename = (payload.get("filename") or "").lower()
    content_b64 = payload.get("contentBase64") or ""
    content_text = payload.get("contentText") or ""
    raw = base64.b64decode(content_b64) if content_b64 else content_text.encode("utf-8")
    if filename.endswith(".xlsx"):
        from app.engine.xlsx import xlsx_buffer_to_rows

        return normalize_imported_keywords(xlsx_buffer_to_rows(raw))
    text = raw.decode("utf-8", errors="ignore").lstrip("\ufeff")
    if filename.endswith(".tsv"):
        rows = [line.split("\t") for line in text.splitlines()]
        return normalize_imported_keywords(rows)
    return import_csv_keywords(text)
