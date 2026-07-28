"""已同步文章的结构化分析与版本持久化。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_signals import has_faq_signal
from app.services.serp_competitor_service import parse_content_html


def analyze_post(post: dict[str, Any]) -> dict[str, Any]:
    content_html = str(post.get("content_html") or "")
    content_md = str(post.get("content_md") or "")
    structure = (
        parse_content_html(content_html, str(post.get("url") or ""))
        if content_html
        else _analyze_markdown(content_md, str(post.get("url") or ""))
    )
    fetch_error = str(post.get("fetch_error") or "").strip() or None
    return {
        "schema_version": 1,
        "title": post.get("title"),
        "meta_title": post.get("meta_title"),
        "meta_description": post.get("meta_description"),
        "primary_keyword": post.get("primary_keyword"),
        "meta_keywords": post.get("meta_keywords") or [],
        **{key: structure.get(key) for key in (
            "word_count", "char_count", "headings", "heading_counts", "faq_signal",
            "paragraph_count", "list_count", "table_count", "image_count",
            "internal_link_count", "external_link_count",
        )},
        "published_at": _serialise(post.get("published_at")),
        "modified_at": _serialise(post.get("modified_at")),
        "content_source": post.get("source") or "api",
        "content_status": "available" if content_html or content_md else "fetch_failed" if fetch_error else "missing",
        "fetch_error": fetch_error,
        "fetched_at": _serialise(post.get("fetched_at")),
    }


async def persist_post_analysis(session: AsyncSession, post_id: str, post: dict[str, Any]) -> dict[str, Any]:
    analysis = analyze_post(post)
    content_hash = _analysis_hash(
        analysis,
        str(post.get("content_html") or ""),
        str(post.get("content_md") or ""),
    )
    row = (
        await session.execute(
            text(
                """
                WITH latest AS (
                  SELECT id, content_hash FROM seo_agent.post_analyses
                   WHERE post_id = CAST(:post_id AS uuid)
                   ORDER BY analyzed_at DESC, id DESC LIMIT 1
                ), updated AS (
                  UPDATE seo_agent.post_analyses
                     SET analysis = CAST(:analysis AS jsonb), analyzed_at = clock_timestamp()
                   WHERE id = (SELECT id FROM latest WHERE content_hash = :content_hash)
                  RETURNING id, content_hash, analyzed_at
                ), inserted AS (
                  INSERT INTO seo_agent.post_analyses (post_id, content_hash, analysis, analyzed_at)
                  SELECT CAST(:post_id AS uuid), :content_hash, CAST(:analysis AS jsonb), clock_timestamp()
                   WHERE NOT EXISTS (SELECT 1 FROM updated)
                  RETURNING id, content_hash, analyzed_at
                )
                SELECT * FROM updated UNION ALL SELECT * FROM inserted
                """
            ),
            {
                "post_id": post_id,
                "content_hash": content_hash,
                "analysis": json.dumps(analysis, ensure_ascii=False, default=str),
            },
        )
    ).mappings().one()
    return dict(row)


def _analysis_hash(analysis: dict[str, Any], content_html: str = "", content_md: str = "") -> str:
    version_payload = {key: value for key, value in analysis.items() if key != "fetched_at"}
    payload = json.dumps({
        "analysis": version_payload,
        "content_digest": hashlib.sha256(f"{content_html}\0{content_md}".encode()).hexdigest(),
    }, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _analyze_markdown(source: str, url: str) -> dict[str, Any]:
    headings = [
        {"level": len(markers), "text": _plain(text_value)}
        for markers, text_value in re.findall(r"^(#{1,6})\s+(.+?)\s*#*\s*$", source, re.M)
    ]
    image_count = len(re.findall(r"!\[[^]]*]\([^)]+\)", source))
    links = re.findall(r"(?<!!)\[[^]]+]\(([^)]+)\)", source)
    host = urlsplit(url).netloc.lower()
    internal_links = sum(1 for link in links if not urlsplit(link).netloc or urlsplit(link).netloc.lower() == host)
    plain = _plain(source)
    return {
        "word_count": len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", plain)),
        "char_count": len(plain),
        "headings": headings[:40],
        "heading_counts": {f"h{level}": sum(item["level"] == level for item in headings) for level in range(1, 7)},
        "faq_signal": has_faq_signal(plain),
        "paragraph_count": len([
            block for block in re.split(r"\n\s*\n", source)
            if block.strip() and not block.lstrip().startswith(("#", "-", "*", ">"))
        ]),
        "list_count": len(re.findall(r"(?:^|\n)(?:\s*[-*+]\s+.+\n?)+", source)),
        "table_count": int(bool(re.search(r"^\s*\|.+\|\s*$", source, re.M))),
        "image_count": image_count,
        "internal_link_count": internal_links,
        "external_link_count": max(0, len(links) - internal_links),
    }


def _plain(value: str) -> str:
    value = re.sub(r"!\[[^]]*]\([^)]+\)", " ", value)
    value = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value)
    value = re.sub(r"<[^>]+>|^#{1,6}\s+", " ", value, flags=re.M)
    return re.sub(r"\s+", " ", value).strip()


def _serialise(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value not in (None, "") else None
