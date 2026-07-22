"""扫描主站 Sitemap/URL 索引，收集页面结构和基础 SEO 问题。"""
from __future__ import annotations

import asyncio
import gzip
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.http_client import ExternalCallError, request_bytes, request_text
from app.services.site_knowledge_service import save_site_knowledge

MAX_INDEX_BYTES = 5 * 1024 * 1024
MAX_DECOMPRESSED_INDEX_BYTES = 20 * 1024 * 1024
MAX_SITEMAPS = 20
MAX_SCAN_URLS = 500
MAX_STORED_PAGES = 2000
MAX_CONCURRENT_FETCHES = 8


async def scan_site_index(
    session: AsyncSession,
    site_id: str,
    content: bytes,
    filename: str = "sitemap.xml",
    *,
    replace: bool = True,
    persist: bool = True,
    additional_allowed_hosts: set[str] | None = None,
) -> dict[str, Any]:
    if len(content) > MAX_INDEX_BYTES:
        raise ValueError("索引文件不能超过 5 MB")
    source, compressed = _decode_index_content(content, filename)
    site = (
        await session.execute(
            text("SELECT id, name, domain, base_url, is_main, knowledge_profile FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"),
            {"id": site_id},
        )
    ).mappings().first()
    if not site:
        raise ValueError("site not found")
    site = dict(site)
    base_url = _base_url(site)
    allowed_hosts = {host for host in {_hostname(base_url), *(additional_allowed_hosts or set())} if host}
    urls, nested = _parse_index(source, base_url)
    fetched_sitemaps = 0
    for sitemap_url in nested[:MAX_SITEMAPS]:
        if _hostname(sitemap_url) not in allowed_hosts:
            continue
        try:
            nested_content = await request_bytes(
                "GET",
                sitemap_url,
                client_label="site_sitemap",
                headers=_sitemap_headers(base_url),
                timeout=20,
                max_attempts=1,
            )
        except ExternalCallError:
            continue
        more_urls, _ = _parse_index(_decode_index_content(nested_content, sitemap_url)[0], base_url)
        urls.extend(more_urls)
        fetched_sitemaps += 1
    urls = _unique_urls(urls, allowed_hosts)
    pages = await _scan_pages(urls[:MAX_SCAN_URLS])
    profile = site.get("knowledge_profile") if isinstance(site.get("knowledge_profile"), dict) else {}
    existing_index = profile.get("index_scan") if isinstance(profile.get("index_scan"), dict) else {}
    existing_pages = [] if replace else (existing_index.get("pages") if isinstance(existing_index.get("pages"), list) else [])
    merged_pages = _merge_pages(existing_pages, pages)
    existing_urls = [] if replace else (existing_index.get("urls") if isinstance(existing_index.get("urls"), list) else [page.get("url") for page in existing_pages])
    merged_urls = _merge_urls(existing_urls, urls)
    previous_files = [
        item for item in (existing_index.get("files") if isinstance(existing_index.get("files"), list) else [])
        if not replace and isinstance(item, dict) and item.get("filename") != filename
    ]
    files = [*previous_files, {
        "filename": filename,
        "source": "sitemap" if nested or _looks_like_xml(source.encode("utf-8")) else "url_list",
        "compressed": compressed,
        "indexed_urls": len(urls),
        "scanned_urls": len(pages),
    }]
    audit = _audit_pages(merged_pages)
    profile = {
        **profile,
        "site_mode": "commercial_hub" if site.get("is_main") else profile.get("site_mode") or "content_site",
        "core_pages": _core_pages(merged_pages),
        "products": (audit["product_hints"] if replace else _unique([*profile.get("products", []), *audit["product_hints"]]))[:30],
        "index_scan": {
            "filename": f"{len(files)} 个文件",
            "files": files,
            "source": "mixed" if len({item["source"] for item in files}) > 1 else files[-1]["source"],
            "indexed_urls": len(merged_urls),
            "scanned_urls": len(merged_pages),
            "nested_sitemaps": fetched_sitemaps if replace else sum(int(item.get("nested_sitemaps") or 0) for item in [existing_index, {"nested_sitemaps": fetched_sitemaps}]),
            "summary": audit["summary"],
            "issues": audit["issues"],
            "product_hints": audit["product_hints"],
            "urls": merged_urls,
            "pages": merged_pages[:MAX_STORED_PAGES],
        },
        "evidence": [
            *(profile.get("evidence") or []),
            {"source": "site_index", "fact": f"索引文件 {filename}，发现 {len(urls)} 个 URL，已扫描 {len(pages)} 个页面，更新方式：{'覆盖' if replace else '合并'}"},
        ],
    }
    saved = await save_site_knowledge(session, site_id, profile) if persist else profile
    return {
        "site_id": site_id,
        "site_name": site["name"],
        "knowledge_profile": saved,
        "seo_audit": audit,
        "index": {"filename": f"{len(files)} 个文件", "files": files, "indexed_urls": len(merged_urls), "scanned_urls": len(merged_pages), "nested_sitemaps": fetched_sitemaps if replace else sum(int(item.get("nested_sitemaps") or 0) for item in [existing_index, {"nested_sitemaps": fetched_sitemaps}])},
    }


def _decode_index_content(content: bytes, filename: str = "") -> tuple[str, bool]:
    raw = content
    compressed = raw[:2] == b"\x1f\x8b"
    if compressed:
        try:
            raw = gzip.decompress(raw)
        except OSError as error:
            raise ValueError("GZip 站点地图解压失败") from error
    if len(raw) > MAX_DECOMPRESSED_INDEX_BYTES:
        raise ValueError("解压后的索引文件不能超过 20 MB")
    return raw.decode("utf-8-sig", errors="ignore"), compressed


def _sitemap_headers(base_url: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/xml,text/xml,application/gzip,*/*",
        "Referer": f"{base_url.rstrip('/')}/",
    }


def _merge_pages(existing: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for page in [*existing, *current]:
        url = str(page.get("url") or "").strip()
        if url:
            normalized = url.rstrip("/") or url
            merged[normalized] = {**page, "url": normalized}
    return list(merged.values())[:MAX_STORED_PAGES]


def _merge_urls(existing: list[Any], current: list[str]) -> list[str]:
    merged: dict[str, str] = {}
    for value in [*existing, *current]:
        url = str(value or "").strip()
        if url:
            normalized = url.rstrip("/") or url
            merged[normalized] = normalized
    return list(merged.values())


def _parse_index(source: str, base_url: str) -> tuple[list[str], list[str]]:
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return [_resolve(line.strip(), base_url) for line in source.splitlines() if _looks_like_url(line.strip())], []
    values = [text_value.strip() for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "loc" and (text_value := "".join(node.itertext()).strip())]
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "sitemapindex":
        return [], [_resolve(value, base_url) for value in values]
    return [_resolve(value, base_url) for value in values], []


async def _scan_pages(urls: list[str]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async def scan(url: str) -> dict[str, Any]:
        async with semaphore:
            try:
                html = await request_text("GET", url, client_label="site_index_page", timeout=15, max_attempts=1)
            except ExternalCallError as error:
                return {"url": url, "status": "error", "error": str(error), "page_type": _page_type(url)}
            parser = _PageParser()
            try:
                parser.feed(html)
                parser.close()
            except Exception:  # noqa: BLE001
                return {"url": url, "status": "parse_error", "page_type": _page_type(url)}
            return {
                "url": url,
                "status": "ok",
                "page_type": _page_type(url),
                "title": parser.title,
                "description": parser.description,
                "canonical": parser.canonical,
                "h1": parser.h1[:3],
                "word_count": len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", " ".join(parser.text_parts))),
            }

    return await asyncio.gather(*(scan(url) for url in urls))


def _audit_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    title_counts: dict[str, int] = {}
    for page in pages:
        title = str(page.get("title") or "").strip().lower()
        if title:
            title_counts[title] = title_counts.get(title, 0) + 1
    issues: list[dict[str, Any]] = []
    for page in pages:
        page_issues: list[str] = []
        title = str(page.get("title") or "").strip()
        description = str(page.get("description") or "").strip()
        if page.get("status") != "ok":
            page_issues.append("页面读取失败")
        if not title:
            page_issues.append("缺少 title")
        elif len(title) < 20 or len(title) > 65:
            page_issues.append("title 长度不理想")
        if not description:
            page_issues.append("缺少 meta description")
        elif len(description) < 70 or len(description) > 170:
            page_issues.append("description 长度不理想")
        if not page.get("h1"):
            page_issues.append("缺少 H1")
        if title_counts.get(title.lower(), 0) > 1:
            page_issues.append("title 重复")
        if page_issues:
            issues.append({"url": page["url"], "page_type": page.get("page_type"), "issues": page_issues})
    return {
        "summary": {
            "pages": len(pages),
            "ok": sum(page.get("status") == "ok" for page in pages),
            "issues": len(issues),
            "missing_title": sum("缺少 title" in issue["issues"] for issue in issues),
            "missing_description": sum("缺少 meta description" in issue["issues"] for issue in issues),
            "missing_h1": sum("缺少 H1" in issue["issues"] for issue in issues),
            "duplicate_title": sum("title 重复" in issue["issues"] for issue in issues),
        },
        "issues": issues[:200],
        "product_hints": _unique([page.get("title") for page in pages if page.get("page_type") == "product" and page.get("title")]),
    }


def _core_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: page.get(key) for key in ("url", "page_type", "title", "h1") if page.get(key)} for page in pages if page.get("page_type") in {"home", "product", "category", "service"}][:60]


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.h1: list[str] = []
        self.text_parts: list[str] = []
        self._active: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta" and attrs_map.get("name", "").lower() == "description":
            self.description = attrs_map.get("content", "").strip()
        if tag.lower() == "link" and attrs_map.get("rel", "").lower() == "canonical":
            self.canonical = attrs_map.get("href", "").strip()
        if tag.lower() in {"title", "h1"}:
            self._active = tag.lower()

    def handle_endtag(self, tag: str) -> None:
        if self._active == tag.lower():
            self._active = None

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if not value:
            return
        if self._active == "title":
            self.title = f"{self.title} {value}".strip()
        elif self._active == "h1" and len(self.h1) < 3:
            self.h1.append(value)
        if len(self.text_parts) < 300:
            self.text_parts.append(value)


def _base_url(site: dict[str, Any]) -> str:
    value = str(site.get("base_url") or site.get("domain") or "").strip()
    return value if value.startswith(("http://", "https://")) else f"https://{value}"


def _resolve(value: str, base_url: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", value)


def _same_host(url: str, base_url: str) -> bool:
    return _hostname(url) == _hostname(base_url)


def _hostname(url: str) -> str:
    return str(urlsplit(url).hostname or "").casefold()


def _unique_urls(values: list[str], allowed_hosts: set[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = value.strip()
        if not url or _hostname(url) not in allowed_hosts:
            continue
        normalized = url.split("#", 1)[0].rstrip("/") or url
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _page_type(url: str) -> str:
    path = urlsplit(url).path.lower().rstrip("/") or "/"
    if path == "/":
        return "home"
    if re.search(r"/(products?|items?|p)/", f"{path}/"):
        return "product"
    if re.search(r"/(collections?|categories?|cat)/", f"{path}/"):
        return "category"
    if re.search(r"/(services?|solutions?)/", f"{path}/"):
        return "service"
    if re.search(r"/(blogs?|articles?|news|posts?)/", f"{path}/"):
        return "article"
    return "page"


def _looks_like_xml(content: bytes) -> bool:
    return content.lstrip().startswith(b"<")


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result
