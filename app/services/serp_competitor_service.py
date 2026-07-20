"""抓取并压缩 SERP 竞争页面，供内容策略分析使用。"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from app.clients.http_client import ExternalCallError, request_text


async def fetch_competitor_pages(organic_results: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in organic_results[: max(1, min(limit, 5))]:
        url = str(result.get("link") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        page = {
            "position": result.get("position") or len(pages) + 1,
            "url": url,
            "title": result.get("title") or "",
            "status": "fetching",
        }
        try:
            html = await request_text(
                "GET",
                url,
                client_label="serp_competitor_html",
                headers={"User-Agent": "SEO-Agent-Workbench/1.0"},
                timeout=30,
                max_attempts=1,
            )
            page.update(parse_content_html(html, url), status="fetched")
        except ExternalCallError as error:
            page.update(status="fetch-failed", error=str(error))
        pages.append(page)
    return pages


class _CompetitorParser(HTMLParser):
    _containers = ("article-content", "entry-content", "post-content", "blog-content", "article-body")
    _ignored = {"script", "style", "noscript", "template", "svg"}
    _excluded = {"nav", "header", "footer", "aside"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self._skip_depth = 0
        self._exclude_depth = 0
        self._title_depth = 0
        self._heading_tag = ""
        self._heading_parts: list[str] = []
        self._active: list[dict[str, Any]] = []
        self._candidates: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            key = (attrs_map.get("name") or attrs_map.get("property") or "").lower()
            if key in {"description", "og:description", "keywords"} and attrs_map.get("content"):
                self.meta.setdefault(key, attrs_map["content"].strip())
        elif tag == "link" and attrs_map.get("rel", "").lower() == "canonical":
            self.canonical = attrs_map.get("href", "").strip()
        elif tag == "title":
            self._title_depth = 1
        if tag in self._ignored:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._excluded:
            self._exclude_depth += 1
            return
        if self._exclude_depth:
            return
        marker = f"{attrs_map.get('id', '')} {attrs_map.get('class', '')}".lower()
        if tag == "body":
            candidate = {"tag": "body", "text": [], "headings": [], "paragraphs": 0, "lists": 0, "tables": 0, "images": 0, "links": []}
            self._active.append(candidate)
            self._candidates.append(candidate)
        elif tag in {"article", "main"} or any(name in marker for name in self._containers):
            candidate = {"tag": tag, "text": [], "headings": [], "paragraphs": 0, "lists": 0, "tables": 0, "images": 0, "links": []}
            self._active.append(candidate)
            self._candidates.append(candidate)
        for candidate in self._active:
            if tag == "p":
                candidate["paragraphs"] += 1
            elif tag in {"ul", "ol"}:
                candidate["lists"] += 1
            elif tag == "table":
                candidate["tables"] += 1
            elif tag == "img":
                candidate["images"] += 1
            elif tag == "a" and attrs_map.get("href"):
                candidate["links"].append(attrs_map["href"])
        if re.fullmatch(r"h[1-6]", tag):
            self._heading_tag = tag
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._ignored:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in self._excluded:
            self._exclude_depth = max(0, self._exclude_depth - 1)
            return
        if self._exclude_depth:
            return
        if tag == "title":
            self._title_depth = 0
        if tag == self._heading_tag:
            heading = _clean(" ".join(self._heading_parts))
            if heading:
                for candidate in self._active:
                    candidate["headings"].append({"level": int(tag[1]), "text": heading})
            self._heading_tag = ""
            self._heading_parts = []
        if self._active and self._active[-1]["tag"] == tag:
            self._active.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._exclude_depth or not data.strip():
            return
        value = _clean(data)
        if self._title_depth:
            self.title.append(value)
        if self._heading_tag:
            self._heading_parts.append(value)
        for candidate in self._active:
            candidate["text"].append(value)


def parse_content_html(source: str, url: str) -> dict[str, Any]:
    parser = _CompetitorParser()
    try:
        parser.feed(source or "")
        parser.close()
    except Exception:  # noqa: BLE001
        return {"status": "parse-failed"}
    if not parser._candidates and source:
        parser = _CompetitorParser()
        parser.feed(f"<body>{source}</body>")
        parser.close()
    article_candidates = [item for item in parser._candidates if item["tag"] != "body"]
    candidate = max(article_candidates or parser._candidates, key=lambda item: len(" ".join(item["text"])), default=None)
    text = _clean(" ".join(candidate["text"])) if candidate else ""
    headings = candidate["headings"] if candidate else []
    links = candidate["links"] if candidate else []
    host = urlsplit(url).netloc.lower()
    internal_links = sum(1 for link in links if not urlsplit(link).netloc or urlsplit(link).netloc.lower() == host)
    return {
        "meta_title": _clean(" ".join(parser.title)) or None,
        "meta_description": parser.meta.get("description") or parser.meta.get("og:description"),
        "canonical": parser.canonical or None,
        "headings": headings[:40],
        "heading_counts": {f"h{level}": sum(item["level"] == level for item in headings) for level in range(1, 7)},
        "word_count": len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text)),
        "char_count": len(text),
        "paragraph_count": candidate["paragraphs"] if candidate else 0,
        "list_count": candidate["lists"] if candidate else 0,
        "table_count": candidate["tables"] if candidate else 0,
        "image_count": candidate["images"] if candidate else 0,
        "internal_link_count": internal_links,
        "external_link_count": max(0, len(links) - internal_links),
        "faq_signal": bool(re.search(r"\bfaq\b|frequently asked|common questions|常见问题", text, re.I)),
        "content_excerpt": text[:1800],
    }


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


_parse_competitor_html = parse_content_html
