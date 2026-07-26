"""Publisher adapters: 把 generated article 推到真实站点。

支持两种协议：
- OpenAPIPublisher: 用于 blog/* 与 main/* 站（自定义 OpenAPI 协议）
- WordPressPublisher: 用于 wp/* 站（WordPress REST + Application Password）

所有 adapter 实现 publish() 接口：
    input:  article dict (title/content_md/meta_title/meta_description/slug)
    output: PublishResult (ok / post_id / url / error)

安全机制：默认 dry_run=True，**不真发**。要真发需显式 dry_run=False。
"""
from __future__ import annotations

import base64
import hashlib
from html import escape
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import structlog

from app.clients.http_client import ExternalCallError, request_json
from app.core.article_urls import is_oemapps_site, resolve_article_public_url
from app.core.config import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class PublishRequest:
    """统一 publish 入参。所有 adapter 共用。"""
    title: str
    slug: str
    content_md: str
    meta_title: str = ""
    meta_description: str = ""
    status: str = "draft"           # draft / publish
    excerpt: str = ""
    primary_keyword: str = ""
    author: str | None = None
    category_id: str | None = None
    image_cover_url: str | None = None


@dataclass
class PublishResult:
    """统一 publish 出参。"""
    ok: bool
    dry_run: bool
    post_id: str | None = None
    url: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageUploadRequest:
    """Unified OEMApps image upload input matching ``POST /file/upload``."""

    type: str
    url: str | None = None
    file: str | None = None
    base64: str | None = None

    def payload(self) -> dict[str, str]:
        upload_type = str(self.type or "").strip().casefold()
        if upload_type not in {"url", "file", "base64"}:
            raise ValueError("image upload type must be one of: url, file, base64")
        value = str(getattr(self, upload_type) or "").strip()
        if not value:
            raise ValueError(f"image upload field '{upload_type}' is required")
        if upload_type == "url" and not value.startswith(("http://", "https://")):
            raise ValueError("image upload url must use http:// or https://")
        return {"type": upload_type, upload_type: value}


@dataclass
class ImageUploadResult:
    ok: bool
    dry_run: bool
    image_id: str | None = None
    src: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PublisherBase:
    """所有 publisher 的基类。"""

    connector_type = "unsupported"
    capabilities: tuple[str, ...] = ()

    def __init__(self, site: dict[str, Any], dry_run: bool = True) -> None:
        self.site = site
        self.dry_run = dry_run

    async def publish(self, req: PublishRequest) -> PublishResult:
        raise NotImplementedError

    async def update(self, post_id: str, req: PublishRequest) -> PublishResult:
        return PublishResult(ok=False, dry_run=self.dry_run, error=f"connector {self.connector_type} does not support update_article")

    async def sync_seo_metadata(self, post_id: str, req: PublishRequest) -> PublishResult:
        return PublishResult(ok=False, dry_run=self.dry_run, error=f"connector {self.connector_type} does not support sync_seo_metadata")

    async def upload_image(self, req: ImageUploadRequest) -> ImageUploadResult:
        return ImageUploadResult(ok=False, dry_run=self.dry_run, error=f"connector {self.connector_type} does not support upload_image")

    async def get_article(self, post_id: str) -> dict[str, Any] | None:
        raise ExternalCallError(f"connector {self.connector_type} does not support get_article")

    async def find_article_by_slug(self, slug: str) -> dict[str, Any] | None:
        raise ExternalCallError(f"connector {self.connector_type} does not support find_article_by_slug")

    async def read_articles(self, limit: int = 1) -> list[dict[str, Any]]:
        raise ExternalCallError(f"connector {self.connector_type} does not support read_articles")

    async def check_connection(self) -> dict[str, Any]:
        started = time.perf_counter()
        info = self.connection_info()
        checks = info.pop("checks")
        if not self.capabilities:
            checks.append({"key": "connector", "label": "连接器注册", "status": "failed", "detail": f"未注册 {self.connector_type} 连接器"})
            return {
                **info,
                "ok": False,
                "capabilities": list(self.capabilities),
                "checks": checks,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error": f"no connector registered for site type: {self.connector_type}",
            }
        if any(check["status"] == "failed" for check in checks):
            error = next(check["detail"] for check in checks if check["status"] == "failed")
            checks.append({"key": "read_articles", "label": "读取文章", "status": "skipped", "detail": "基础配置未通过，未发起外部请求"})
            return {
                **info,
                "ok": False,
                "capabilities": list(self.capabilities),
                "checks": checks,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error": error,
            }
        try:
            items = await self.read_articles(limit=1)
            checks.append({"key": "read_articles", "label": "读取文章", "status": "ok", "detail": f"成功读取 {len(items)} 篇样本"})
            return {
                **info,
                "ok": True,
                "capabilities": list(self.capabilities),
                "sample_count": len(items),
                "checks": checks,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            }
        except Exception as error:  # noqa: BLE001
            checks.append({"key": "read_articles", "label": "读取文章", "status": "failed", "detail": str(error)})
            return {
                **info,
                "ok": False,
                "capabilities": list(self.capabilities),
                "checks": checks,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error": str(error),
            }

    def connection_info(self) -> dict[str, Any]:
        cfg = self.site.get("api_config") or {}
        is_wordpress = self.connector_type == "wordpress"
        base_url = ((self.site.get("domain") or self.site.get("base_url")) if is_wordpress else (self.site.get("api_base_url") or self.site.get("base_url") or ""))
        if is_wordpress and base_url and not str(base_url).startswith(("http://", "https://")):
            base_url = f"https://{base_url}"
        base_url = str(base_url).rstrip("/")
        if is_wordpress:
            article_path, publish_path = (
                cfg.get("articlesPath") or "/wp-json/wp/v2/posts",
                cfg.get("publishPath") or "/wp-json/wp/v2/posts",
            )
        elif isinstance(self, OpenAPIPublisher):
            article_path, publish_path = self._article_paths()
        else:
            article_path, publish_path = cfg.get("articlesPath") or "/posts", cfg.get("publishPath") or "/posts/batch"
        endpoint = _join_endpoint(base_url, article_path)
        publish_endpoint = _join_endpoint(base_url, publish_path)
        auth_keys = ["username", "applicationPassword"] if is_wordpress else ["openApiKey", "tokenA", "tokenB"]
        configured_keys = [key for key in auth_keys if cfg.get(key)]
        missing_keys = [key for key in auth_keys if not cfg.get(key)]
        auth_label = "WordPress Basic Auth" if is_wordpress else "X-API-Key + Host / token"
        checks = [
            {"key": "endpoint", "label": "接口地址", "status": "ok" if base_url else "failed", "detail": _display_url(endpoint) if base_url else "未配置 API 地址"},
            {"key": "auth", "label": "鉴权配置", "status": "ok" if (is_wordpress and not missing_keys) or (not is_wordpress and bool(configured_keys)) else "failed", "detail": f"已配置：{', '.join(configured_keys) or '无'}" + (f"；缺失：{', '.join(missing_keys)}" if missing_keys else "")},
        ]
        return {
            "connector_type": self.connector_type,
            "request": {"method": "GET", "url": _display_url(endpoint), "auth": auth_label},
            "config": {"base_url": _display_url(base_url), "article_endpoint": _display_url(endpoint), "publish_endpoint": _display_url(publish_endpoint), "articles_path": article_path, "publish_path": publish_path, "configured_keys": configured_keys, "missing_keys": missing_keys},
            "checks": checks,
        }

    def _dry_response(self, req: PublishRequest) -> PublishResult:
        """不真发，返回"如果真发会是什么样"——便于前端/审计预览。"""
        return PublishResult(
            ok=True,
            dry_run=True,
            post_id=None,
            url=f"{self.site.get('base_url') or self.site.get('api_base_url') or 'https://example.com'}/{req.slug}",
            error=None,
            raw={
                "would_send": {
                    "title": req.title,
                    "slug": req.slug,
                    "content_length": len(req.content_md),
                    "status": req.status,
                    "excerpt_preview": (req.excerpt or req.meta_description)[:100],
                },
                "adapter": self.__class__.__name__,
            },
        )


def strip_markdown_frontmatter(source: str) -> tuple[str, dict[str, str]]:
    """Remove generated metadata blocks and return the article body plus metadata."""
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    wrapped = re.fullmatch(r"```(?:markdown|md)?\n([\s\S]*?)\n```\s*", text, re.I)
    if wrapped:
        text = wrapped.group(1)
    metadata: dict[str, str] = {}
    fenced_metadata = re.match(r"^```(?:yaml|yml)\n([\s\S]*?)\n```\s*", text, re.I)
    if fenced_metadata:
        metadata.update(_metadata_lines(fenced_metadata.group(1).splitlines()))
        text = text[fenced_metadata.end():].lstrip()
    lines = text.split("\n")
    if text.startswith("---\n"):
        end = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        if end is not None:
            metadata.update(_metadata_lines(lines[1:end]))
            lines = lines[end + 1:]

    known = ("title", "meta title", "meta description", "url slug", "primary keyword", "secondary keywords", "last updated", "evidence needed", "content qa checklist")
    first_h1 = next((index for index, line in enumerate(lines) if re.match(r"^\s*#\s+", line)), None)
    had_metadata = False
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.match(r"^\s*(?:#{1,6}\s*)?post-publish notes\b", line, re.I):
            break
        if re.match(r"^\s*\*?\(?\*?only include references\b", line, re.I):
            index += 1
            continue
        if re.match(r"^\s*(?:---+|\*\*\*+|___+)\s*$", line):
            index += 1
            continue
        heading = re.match(r"^\s*#{2,6}\s+(.+?)\s*$", line)
        direct = re.match(r"^\s*\*{0,2}(Title|Meta[_ -]?Title|Meta[_ -]?Description|URL[_ -]?Slug|Primary Keyword|Secondary Keywords|Last Updated)\s*:\*{0,2}\s*(.*)$", line, re.I)
        if direct and direct.group(1).lower() == "title" and first_h1 is not None and index > first_h1:
            direct = None
        label = re.sub(r"[_-]+", " ", heading.group(1) if heading else direct.group(1) if direct else "").strip().lower()
        matched = next((key for key in known if label == key or label.startswith(f"{key}:")), None)
        if matched:
            had_metadata = True
            inline = ""
            if heading:
                inline = heading.group(1).split(":", 1)[1].strip() if ":" in heading.group(1) else ""
            elif direct:
                inline = direct.group(2).strip()
            values = [inline] if inline else []
            index += 1
            if heading:
                while index < len(lines) and not re.match(r"^\s*#{1,6}\s+", lines[index]):
                    if lines[index].strip():
                        values.append(lines[index].strip())
                    index += 1
            else:
                while index < len(lines) and not lines[index].strip():
                    index += 1
            value = " ".join(values).strip()
            if value and matched not in metadata:
                metadata[matched.replace(" ", "_")] = value.strip("'\"")
            continue
        output.append(line)
        index += 1

    if had_metadata and first_h1 is not None:
        # Metadata templates are meant to precede the H1; discard any remaining preamble text too.
        h1 = next((index for index, line in enumerate(output) if re.match(r"^\s*#\s+", line)), None)
        if h1 is not None:
            output = output[h1:]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip(), metadata


def _metadata_lines(lines: list[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _-]*):\s*(.*)$", line)
        if match:
            value = match.group(2).strip()
            key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", match.group(1))
            key = re.sub(r"[\s-]+", "_", key).lower()
            metadata[key] = value.strip("'\"")
    return metadata


class OpenAPIPublisher(PublisherBase):
    """自定义 OpenAPI 协议：blog 站 (/api/open/v1) 与 main 站 (openapi.oemapps.com)。

    鉴权分两种：
    - blog 站读/新建：X-API-Key；单篇更新：Authorization Bearer
    - main 站：headers = {"token": "tokenA 或 tokenB"}

    Payload 形式（基于常见 openapi 协议猜的，dry_run 模式不会真发）：
        POST {api_base_url}
        Headers: {openApiKey | token: ...}
        Body: { title, content_md, slug, status, author, srcPrefix, imageId, ... }

    失败/不识别的站点用 dry_run 模式安全返回。
    """

    connector_type = "custom_openapi"
    capabilities = ("read_articles", "get_article", "find_article_by_slug", "publish_article", "update_article", "upload_image")

    def _is_oemapps(self) -> bool:
        return is_oemapps_site(self.site)

    def _dry_response(self, req: PublishRequest) -> PublishResult:
        result = super()._dry_response(req)
        result.url = self._public_article_url(req.slug, "")
        return result

    def _article_paths(self) -> tuple[str, str]:
        """Resolve the documented article protocol for the selected site.

        OEMApps self-hosted sites share the fixed ``/posts`` contract.  Earlier
        versions of the form supplied generic ``/articles`` defaults, so accept
        those stale values but do not send requests to an endpoint the platform
        does not implement.  Other custom APIs retain their explicit paths.
        """
        cfg = self.site.get("api_config") or {}
        if self._is_oemapps():
            return "/posts", "/posts"
        return str(cfg.get("articlesPath") or "/posts"), str(cfg.get("publishPath") or "/posts/batch")

    async def upload_image(self, req: ImageUploadRequest) -> ImageUploadResult:
        if not self._is_oemapps():
            return ImageUploadResult(ok=False, dry_run=self.dry_run, error="image upload is only configured for OEMApps sites")
        try:
            body = req.payload()
        except ValueError as error:
            return ImageUploadResult(ok=False, dry_run=self.dry_run, error=str(error))
        if self.dry_run:
            return ImageUploadResult(ok=True, dry_run=True, raw={"endpoint": "/file/upload", "type": body["type"]})

        api_base = str(self.site.get("api_base_url") or "").rstrip("/")
        headers = self._auth_headers()
        if not api_base or not headers:
            return ImageUploadResult(ok=False, dry_run=False, error="OEMApps image connector missing api_base_url or site token")
        try:
            data = await request_json(
                "POST",
                _join_endpoint(api_base, "/file/upload"),
                client_label="connector_oemapps_image_upload",
                headers=headers,
                json=body,
                timeout=120,
            )
        except ExternalCallError as error:
            return ImageUploadResult(ok=False, dry_run=False, error=str(error))

        item = data.get("data") if isinstance(data, dict) else None
        if data.get("code") not in (0, "0") or not isinstance(item, dict) or not item.get("src"):
            return ImageUploadResult(
                ok=False,
                dry_run=False,
                error=str(data.get("msg") or "OEMApps image upload did not return an image URL"),
                raw=data,
            )
        return ImageUploadResult(
            ok=True,
            dry_run=False,
            image_id=str(item.get("id")) if item.get("id") is not None else None,
            src=str(item["src"]),
            raw=data,
        )

    async def read_articles(self, limit: int = 1) -> list[dict[str, Any]]:
        api_base = (self.site.get("api_base_url") or self.site.get("base_url") or "").rstrip("/")
        headers = self._auth_headers()
        if not api_base or not headers:
            raise ExternalCallError("site post connector missing api_base_url or auth")
        article_path, _ = self._article_paths()
        endpoint = _join_endpoint(api_base, article_path)
        wanted = min(max(limit, 1), 200)
        collected: list[dict[str, Any]] = []
        page = 1
        while len(collected) < wanted:
            data = await request_json(
                "GET",
                endpoint,
                client_label="connector_openapi_articles",
                params={"page": page, "page_size": min(wanted, 100)},
                headers=headers,
                timeout=60,
            )
            items, pagination = _openapi_page(data)
            collected.extend(item for item in items if isinstance(item, dict))
            total_pages = int(pagination.get("pageTotal") or pagination.get("totalPages") or 0) if pagination else 0
            next_page = int(pagination.get("next") or 0) if pagination else 0
            if not items or (total_pages and page >= total_pages) or (not total_pages and not next_page):
                break
            page = next_page or page + 1
        return collected[:wanted]

    async def get_article(self, post_id: str) -> dict[str, Any] | None:
        api_base = str(self.site.get("api_base_url") or self.site.get("base_url") or "").rstrip("/")
        headers = self._auth_headers()
        if not api_base or not headers:
            raise ExternalCallError("site post connector missing api_base_url or auth")
        path = self._article_paths()[0].rstrip("/")
        try:
            data = await request_json(
                "GET",
                _join_endpoint(api_base, f"{path}/{quote(str(post_id), safe='')}"),
                client_label="connector_openapi_article",
                headers=headers,
                timeout=60,
            )
            item = _openapi_item(data)
            if item:
                return item
        except ExternalCallError:
            pass
        wanted = str(post_id)
        return next(
            (
                item
                for item in await self.read_articles(limit=200)
                if str(item.get("id") or item.get("post_id") or item.get("articleId") or "") == wanted
            ),
            None,
        )

    async def find_article_by_slug(self, slug: str) -> dict[str, Any] | None:
        wanted = str(slug).strip().strip("/").casefold()
        # ponytail: custom API has no documented slug endpoint; use its bounded list until one exists.
        for item in await self.read_articles(limit=200):
            remote_slug = str(item.get("slug") or item.get("handle") or "").strip().strip("/").casefold()
            if remote_slug == wanted:
                return item
        return None

    def _auth_headers(self) -> dict[str, str]:
        cfg = self.site.get("api_config") or {}
        if cfg.get("openApiKey"):
            headers = {"X-API-Key": cfg["openApiKey"]}
            host = self._site_host()
            if host:
                headers["Host"] = host
            return headers
        if cfg.get("tokenB"):
            return {"token": cfg["tokenB"]}
        if cfg.get("tokenA"):
            return {"token": cfg["tokenA"]}
        return {}

    def _site_host(self) -> str:
        raw = str(self.site.get("domain") or self.site.get("base_url") or "").strip()
        if not raw:
            return ""
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        return parsed.netloc or parsed.path.split("/", 1)[0]

    async def publish(self, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        return await self._real_publish(req)

    async def update(self, post_id: str, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        api_base = str(self.site.get("api_base_url") or self.site.get("base_url") or "").rstrip("/")
        api_key = str((self.site.get("api_config") or {}).get("openApiKey") or "")
        if self._is_oemapps():
            headers = self._auth_headers()
            if not api_base or not headers:
                return PublishResult(ok=False, dry_run=False, error="OEMApps article connector missing api_base_url or site token")
        else:
            if not api_base or not api_key:
                return PublishResult(ok=False, dry_run=False, error="site post connector missing api_base_url or openApiKey")
            headers = {"Authorization": f"Bearer {api_key}"}
        content_md, _ = strip_markdown_frontmatter(req.content_md)
        if self._is_oemapps():
            body = self._oemapps_article_body(req, content_md)
        else:
            body = {
                "title": req.title,
                "content_md": content_md,
                "format": "markdown",
                "status": "published" if req.status in {"publish", "published"} else "draft",
            }
            if req.excerpt or req.meta_description:
                body["excerpt"] = req.excerpt or req.meta_description
            if req.image_cover_url:
                body["cover_url"] = req.image_cover_url
        try:
            data = await request_json(
                "PUT",
                _join_endpoint(api_base, f"{self._article_paths()[0].rstrip('/')}/{quote(str(post_id), safe='')}"),
                client_label="connector_openapi_update",
                headers=headers,
                json=body,
                timeout=60,
            )
        except ExternalCallError as error:
            return PublishResult(ok=False, dry_run=False, error=str(error), raw={"body_sent": body})
        item = _openapi_item(data) or data
        remote_id = str(item.get("id") or data.get("data") or post_id)
        slug = str(item.get("handle") or item.get("slug") or req.slug)
        return PublishResult(ok=True, dry_run=False, post_id=remote_id, url=self._public_article_url(slug, remote_id), raw=data)

    def _public_article_url(self, slug: str, post_id: str) -> str | None:
        return resolve_article_public_url(
            self.site,
            slug=slug,
            article_id=post_id,
        )

    async def _real_publish(self, req: PublishRequest) -> PublishResult:
        api_base = self.site.get("api_base_url") or self.site.get("base_url")
        if not api_base:
            return PublishResult(ok=False, dry_run=False, error="site.api_base_url is empty")

        headers = self._auth_headers()
        if not headers:
            return PublishResult(ok=False, dry_run=False, error="no auth token in site.api_config")

        cfg = self.site.get("api_config") or {}
        # 真实协议字段不确定（每个 OpenAPI 实现不同），这里按最常见的字段名
        content_md, _ = strip_markdown_frontmatter(req.content_md)
        if self._is_oemapps():
            body = self._oemapps_article_body(req, content_md)
        else:
            body = {
            "items": [{
                "title": req.title,
                "slug": req.slug,
                "excerpt": req.excerpt or req.meta_description,
                "author": req.author or cfg.get("defaultAuthor") or "admin",
                "content_md": content_md,
                "format": "markdown",
                "status": "published" if req.status in {"publish", "published"} else "draft",
                "cover_url": req.image_cover_url or "",
                "category_id": int(req.category_id) if str(req.category_id or "").isdigit() else None,
            }],
            }

        try:
            data = await post_json(_join_endpoint(api_base.rstrip('/'), self._article_paths()[1]), headers, body)
        except ExternalCallError as e:
            return PublishResult(ok=False, dry_run=False, error=str(e), raw={"body_sent": body})

        if self._is_oemapps():
            if data.get("code") != 0 or not data.get("data"):
                return PublishResult(ok=False, dry_run=False, error=str(data.get("msg") or "OEMApps did not create the article"), raw=data)
            post_id = str(data["data"])
            return PublishResult(
                ok=True,
                dry_run=False,
                post_id=post_id,
                url=self._public_article_url(req.slug, post_id),
                raw=data,
            )

        created = data.get("created") if isinstance(data, dict) else None
        if not isinstance(created, list) or not created:
            failed = data.get("failed") if isinstance(data, dict) else None
            return PublishResult(
                ok=False,
                dry_run=False,
                error=str(failed[0] if isinstance(failed, list) and failed else "remote API did not create the article"),
                raw=data if isinstance(data, dict) else {},
            )
        created_item = created[0] if isinstance(created[0], dict) else {}
        post_id = str(created_item.get("id") or created_item.get("post_id") or created_item.get("articleId") or "")
        url = created_item.get("url") or created_item.get("link") or f"{api_base.rstrip('/')}/posts/{created_item.get('slug') or req.slug}"
        return PublishResult(
            ok=True,
            dry_run=False,
            post_id=post_id,
            url=url,
            raw=data,
        )

    def _oemapps_article_body(self, req: PublishRequest, content_md: str) -> dict[str, Any]:
        """Map the shared OEMApps article contract; only the site token varies."""
        description = req.meta_description or req.excerpt
        body: dict[str, Any] = {
            "title": req.title,
            "handle": req.slug,
            "content": oemapps_html_from_markdown(content_md),
            "status": 1 if req.status in {"publish", "published"} else 0,
            "descript": description,
            "meta_title": req.meta_title or req.title,
            "meta_descript": description,
            "meta_keywords": [req.primary_keyword] if req.primary_keyword else [],
            "author_name": req.author or (self.site.get("api_config") or {}).get("defaultAuthor") or "admin",
            "related_product_ids": [],
            "is_top": 0,
        }
        if req.image_cover_url:
            body["src"] = req.image_cover_url
        if str(req.category_id or "").isdigit():
            body["news_id"] = int(str(req.category_id))
        return body


class WordPressPublisher(PublisherBase):
    """WordPress REST + Application Password 协议：wp/* 站。

    鉴权：Basic Auth = base64("username:applicationPassword")
    Endpoint: POST {site_url}/wp-json/wp/v2/posts

    Payload (WP 标准):
        { title, content, status, slug, excerpt, meta: { _yoast_wpseo_title, _yoast_wpseo_metadesc } }
    """

    connector_type = "wordpress"
    capabilities = ("read_articles", "get_article", "find_article_by_slug", "publish_article", "update_article")

    def _site_url(self) -> str:
        site_url = (self.site.get("domain") or self.site.get("base_url") or "").rstrip("/")
        if site_url and not site_url.startswith(("http://", "https://")):
            site_url = f"https://{site_url}"
        return site_url

    def _auth_headers(self) -> dict[str, str]:
        cfg = self.site.get("api_config") or {}
        username = cfg.get("username")
        app_pwd = cfg.get("applicationPassword")
        if not username or not app_pwd:
            return {}
        token = base64.b64encode(f"{username}:{app_pwd}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    async def read_articles(self, limit: int = 1) -> list[dict[str, Any]]:
        site_url = self._site_url()
        headers = self._auth_headers()
        if not site_url or not headers:
            raise ExternalCallError("wordpress connector missing site URL or credentials")
        data = await request_json(
            "GET",
            _join_endpoint(site_url, (self.site.get("api_config") or {}).get("articlesPath") or "/wp-json/wp/v2/posts"),
            client_label="connector_wordpress_articles",
            params={"per_page": min(max(limit, 1), 100), "page": 1, "status": "publish,draft", "_embed": 1},
            headers=headers,
            timeout=60,
        )
        return data if isinstance(data, list) else []

    async def get_article(self, post_id: str) -> dict[str, Any] | None:
        site_url = self._site_url()
        headers = self._auth_headers()
        if not site_url or not headers:
            raise ExternalCallError("wordpress connector missing site URL or credentials")
        path = str((self.site.get("api_config") or {}).get("articlesPath") or "/wp-json/wp/v2/posts").rstrip("/")
        data = await request_json(
            "GET",
            _join_endpoint(site_url, f"{path}/{quote(str(post_id), safe='')}"),
            client_label="connector_wordpress_article",
            params={"context": "edit"},
            headers=headers,
            timeout=60,
        )
        return data if isinstance(data, dict) and data.get("id") is not None else None

    async def find_article_by_slug(self, slug: str) -> dict[str, Any] | None:
        site_url = self._site_url()
        headers = self._auth_headers()
        if not site_url or not headers:
            raise ExternalCallError("wordpress connector missing site URL or credentials")
        data = await request_json(
            "GET",
            _join_endpoint(site_url, (self.site.get("api_config") or {}).get("articlesPath") or "/wp-json/wp/v2/posts"),
            client_label="connector_wordpress_find_article",
            params={"slug": slug, "status": "publish,draft,pending,private,future", "per_page": 1, "context": "edit"},
            headers=headers,
            timeout=60,
        )
        return data[0] if isinstance(data, list) and data else None

    async def publish(self, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        return await self._real_publish(req)

    async def update(self, post_id: str, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        return await self._real_update(post_id, req)

    def _wordpress_body(self, req: PublishRequest, *, include_slug: bool = True) -> dict[str, Any]:
        content_md, frontmatter = strip_markdown_frontmatter(req.content_md)
        content_md = re.sub(r"^[ \t]{0,3}#\s+.*(?:\n+|$)", "", content_md, count=1, flags=re.M)
        body: dict[str, Any] = {
            "title": req.title,
            "content": markdown_to_gutenberg(content_md),
            "status": req.status,
            "excerpt": req.excerpt or req.meta_description,
            "meta": {
                "rank_math_title": req.meta_title or frontmatter.get("title", ""),
                "rank_math_description": req.meta_description or frontmatter.get("meta_description", ""),
                "rank_math_focus_keyword": req.primary_keyword,
            },
        }
        if include_slug:
            body["slug"] = req.slug
        if req.category_id:
            body["categories"] = [int(req.category_id)] if str(req.category_id).isdigit() else [req.category_id]
        return body

    async def _real_publish(self, req: PublishRequest) -> PublishResult:
        site_url = self._site_url()
        if not site_url:
            return PublishResult(ok=False, dry_run=False, error="site.domain is empty")

        cfg = self.site.get("api_config") or {}
        headers = self._auth_headers()
        if not headers:
            return PublishResult(
                ok=False, dry_run=False,
                error="site.api_config missing username or applicationPassword",
            )
        headers["Content-Type"] = "application/json"

        body = self._wordpress_body(req)
        if req.author:
            # WordPress author 必须是 numeric ID；这里只传 slug
            pass

        try:
            data = await post_json(
                _join_endpoint(site_url, cfg.get("publishPath") or "/wp-json/wp/v2/posts"),
                headers,
                body,
            )
        except ExternalCallError as e:
            return PublishResult(ok=False, dry_run=False, error=str(e), raw={"body_sent": body})

        return PublishResult(
            ok=True,
            dry_run=False,
            post_id=str(data.get("id") or ""),
            url=data.get("link") or "",
            raw=data,
        )

    async def _real_update(self, post_id: str, req: PublishRequest) -> PublishResult:
        if not str(post_id).isdigit():
            return PublishResult(ok=False, dry_run=False, error="WordPress post_id must be numeric")
        site_url = self._site_url()
        if not site_url:
            return PublishResult(ok=False, dry_run=False, error="site.domain is empty")
        headers = self._auth_headers()
        if not headers:
            return PublishResult(ok=False, dry_run=False, error="site.api_config missing username or applicationPassword")
        headers["Content-Type"] = "application/json"
        cfg = self.site.get("api_config") or {}
        body = self._wordpress_body(req, include_slug=False)
        endpoint = _join_endpoint(site_url, f"{(cfg.get('publishPath') or '/wp-json/wp/v2/posts').rstrip('/')}/{post_id}")
        try:
            data = await post_json(endpoint, headers, body)
        except ExternalCallError as e:
            return PublishResult(ok=False, dry_run=False, error=str(e), raw={"body_sent": body})
        return PublishResult(ok=True, dry_run=False, post_id=str(data.get("id") or post_id), url=data.get("link") or "", raw=data)


_SHOPIFY_TOKEN_CACHE: dict[str, tuple[float, str, str]] = {}


def evict_shopify_token_cache(site_id: str) -> None:
    prefix = f"{site_id}:"
    for key in [key for key in _SHOPIFY_TOKEN_CACHE if key.startswith(prefix)]:
        _SHOPIFY_TOKEN_CACHE.pop(key, None)


class ShopifyPublisher(PublisherBase):
    """Shopify Admin GraphQL connector using the client credentials grant."""

    connector_type = "shopify"
    capabilities = (
        "read_articles",
        "get_article",
        "publish_article",
        "update_article",
        "sync_seo_metadata",
        "read_products",
        "update_product_seo",
    )

    def __init__(
        self,
        site: dict[str, Any],
        dry_run: bool = True,
        *,
        credentials: dict[str, str] | None = None,
    ) -> None:
        super().__init__(site, dry_run=dry_run)
        self._credentials = {
            key: str(value).strip()
            for key, value in (credentials or {}).items()
            if key in {"client_id", "client_secret"} and str(value).strip()
        }

    async def test_credentials(self) -> dict[str, Any]:
        shop = self._shop_domain()
        _, scopes = await self._access_token(shop, use_cache=False)
        identity = await self._graphql(
            """
            query ConnectionProbe($first: Int!) {
              shop { id name myshopifyDomain }
              blogs(first: $first) { nodes { id handle } }
            }
            """,
            {"first": 250},
        )
        remote_domain = str((identity.get("shop") or {}).get("myshopifyDomain") or "").casefold()
        if remote_domain != shop.casefold():
            raise ExternalCallError("Shopify returned a different shop identity")
        expected_blog = str(self._config("blogHandle", "blog_handle") or "news").casefold()
        blogs = identity.get("blogs", {}).get("nodes", [])
        if not any(str(item.get("handle") or "").casefold() == expected_blog for item in blogs):
            raise ExternalCallError(f"Shopify cannot find blog handle '{expected_blog}'")
        normalized_scopes = sorted({value.strip().casefold() for value in scopes.split(",") if value.strip()})
        return {"shop_domain": shop, "scopes": normalized_scopes, "blog_handle": expected_blog}

    async def read_products(self, limit: int = 250) -> list[dict[str, Any]]:
        """Read a bounded product snapshot without mutating the shop."""
        remaining = min(max(int(limit), 1), 1000)
        cursor: str | None = None
        products: list[dict[str, Any]] = []
        while remaining > 0:
            first = min(remaining, 100)
            data = await self._graphql(
                """
                query ProductsForSeo($first: Int!, $after: String) {
                  products(first: $first, after: $after, sortKey: UPDATED_AT, reverse: true) {
                    nodes {
                      id title handle descriptionHtml status productType vendor updatedAt
                      onlineStoreUrl
                      seo { title description }
                      featuredMedia {
                        ... on MediaImage { id alt image { url } }
                      }
                      media(first: 50) {
                        nodes {
                          ... on MediaImage { id alt image { url } }
                        }
                      }
                      variants(first: 100) {
                        nodes { id title sku price compareAtPrice inventoryQuantity }
                      }
                    }
                    pageInfo { hasNextPage endCursor }
                  }
                }
                """,
                {"first": first, "after": cursor},
            )
            connection = data.get("products") or {}
            batch = [item for item in connection.get("nodes") or [] if isinstance(item, dict)]
            products.extend(batch)
            remaining -= len(batch)
            page_info = connection.get("pageInfo") or {}
            cursor = page_info.get("endCursor")
            if not batch or not page_info.get("hasNextPage") or not cursor:
                break
        return products

    async def get_product_for_seo(self, product_id: str) -> dict[str, Any] | None:
        data = await self._graphql(
            """
            query ProductForSeo($id: ID!) {
              product(id: $id) {
                id title handle updatedAt seo { title description }
              }
            }
            """,
            {"id": product_id},
        )
        product = data.get("product")
        if not isinstance(product, dict) or str(product.get("id") or "") != product_id:
            return None
        return product

    async def update_product_seo(
        self,
        product_id: str,
        *,
        title: str,
        description: str,
        expected_updated_at: str,
    ) -> dict[str, Any]:
        """Update only ProductUpdateInput.id and .seo, with a stale-read guard."""
        if self.dry_run:
            raise ExternalCallError("Shopify product SEO writes require a live connector")
        current = await self.get_product_for_seo(product_id)
        if not current:
            raise ExternalCallError("Shopify product not found")
        current_version = _canonical_shopify_timestamp(current.get("updatedAt"))
        expected_version = _canonical_shopify_timestamp(expected_updated_at)
        if current_version != expected_version:
            raise ExternalCallError("Shopify product changed after review; sync and review it again")
        product_input = {
            "id": product_id,
            "seo": {"title": title, "description": description},
        }
        data = await self._graphql(
            """
            mutation UpdateProductSeo($product: ProductUpdateInput!) {
              productUpdate(product: $product) {
                product { id title handle updatedAt seo { title description } }
                userErrors { field message }
              }
            }
            """,
            {"product": product_input},
            max_attempts=1,
        )
        result = data.get("productUpdate") or {}
        errors = result.get("userErrors") or []
        if errors:
            detail = "; ".join(str(error.get("message") or error) for error in errors)
            raise ExternalCallError(f"Shopify productUpdate: {detail}")
        updated = result.get("product")
        if not isinstance(updated, dict) or str(updated.get("id") or "") != product_id:
            raise ExternalCallError("Shopify did not return the updated product")
        seo = updated.get("seo") or {}
        if str(seo.get("title") or "") != title or str(seo.get("description") or "") != description:
            raise ExternalCallError("Shopify product SEO verification mismatch")
        verified = await self.get_product_for_seo(product_id)
        verified_seo = (verified or {}).get("seo") or {}
        if (
            not verified
            or str(verified_seo.get("title") or "") != title
            or str(verified_seo.get("description") or "") != description
        ):
            raise ExternalCallError("Shopify product SEO readback verification mismatch")
        return verified

    async def read_articles(self, limit: int = 1) -> list[dict[str, Any]]:
        first = min(max(limit, 1), 50)
        data = await self._graphql(
            """
            query Articles($first: Int!) {
              articles(first: $first) {
                nodes { id title handle body summary }
              }
            }
            """,
            {"first": first},
        )
        return data.get("articles", {}).get("nodes", [])

    async def get_article(self, post_id: str) -> dict[str, Any] | None:
        data = await self._graphql(
            """
            query Article($id: ID!) {
              article(id: $id) { id title handle body summary isPublished }
            }
            """,
            {"id": post_id},
        )
        article = data.get("article")
        if not isinstance(article, dict) or not article.get("id"):
            return None
        return {**article, "url": self._article_url(str(article.get("handle") or ""))}

    async def publish(self, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)

        content, _ = strip_markdown_frontmatter(req.content_md)
        article: dict[str, Any] = {
            "title": req.title,
            "handle": req.slug,
            "body": shopify_body_from_markdown(content, req.title),
            "summary": req.excerpt or req.meta_description,
            "author": {"name": self._config("author") or "SEO Workbench"},
            "isPublished": req.status in {"publish", "published"},
        }
        seo_metafields = _shopify_seo_metafields(req)
        if seo_metafields:
            article["metafields"] = seo_metafields
        article["blogId"] = await self._blog_id_for_publish()

        try:
            data = await self._graphql(
                """
                mutation CreateArticle($article: ArticleCreateInput!) {
                  articleCreate(article: $article) {
                    article { id title handle }
                    userErrors { field message }
                  }
                }
                """,
                {"article": article},
            )
        except ExternalCallError as e:
            return PublishResult(ok=False, dry_run=False, error=str(e), raw={"article": article})

        result = data.get("articleCreate", {})
        errors = result.get("userErrors") or []
        if errors:
            detail = "; ".join(str(error.get("message") or error) for error in errors)
            return PublishResult(ok=False, dry_run=False, error=detail, raw=data)

        created = result.get("article") or {}
        if not created.get("id"):
            return PublishResult(ok=False, dry_run=False, error="Shopify did not return the created article", raw=data)
        return PublishResult(
            ok=True,
            dry_run=False,
            post_id=str(created["id"]),
            url=self._article_url(created.get("handle") or req.slug),
            raw=data,
        )

    async def update(self, post_id: str, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)

        content, _ = strip_markdown_frontmatter(req.content_md)
        article = {
            "title": req.title,
            "handle": req.slug,
            "body": shopify_body_from_markdown(content, req.title),
            "summary": req.excerpt or req.meta_description,
            "isPublished": req.status in {"publish", "published"},
        }
        seo_metafields = _shopify_seo_metafields(req)
        if seo_metafields:
            article["metafields"] = seo_metafields
        try:
            data = await self._graphql(
                """
                mutation UpdateArticle($id: ID!, $article: ArticleUpdateInput!) {
                  articleUpdate(id: $id, article: $article) {
                    article { id title handle }
                    userErrors { field message }
                  }
                }
                """,
                {"id": post_id, "article": article},
            )
        except ExternalCallError as e:
            return PublishResult(ok=False, dry_run=False, error=str(e), raw={"id": post_id, "article": article})

        result = data.get("articleUpdate", {})
        errors = result.get("userErrors") or []
        if errors:
            detail = "; ".join(str(error.get("message") or error) for error in errors)
            return PublishResult(ok=False, dry_run=False, error=detail, raw=data)
        updated = result.get("article") or {}
        if not updated.get("id"):
            return PublishResult(ok=False, dry_run=False, error="Shopify did not return the updated article", raw=data)
        return PublishResult(
            ok=True,
            dry_run=False,
            post_id=str(updated["id"]),
            url=self._article_url(str(updated.get("handle") or req.slug)),
            raw=data,
        )

    async def sync_seo_metadata(self, post_id: str, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        metafields = _shopify_seo_metafields(req)
        if not metafields:
            return PublishResult(ok=False, dry_run=False, error="article has no SEO title or meta description to sync")
        try:
            data = await self._graphql(
                """
                mutation UpdateArticleSeo($id: ID!, $article: ArticleUpdateInput!) {
                  articleUpdate(id: $id, article: $article) {
                    article { id title handle }
                    userErrors { field message }
                  }
                }
                """,
                {"id": post_id, "article": {"metafields": metafields}},
            )
        except ExternalCallError as e:
            return PublishResult(ok=False, dry_run=False, error=str(e), raw={"id": post_id, "metafields": metafields})
        result = data.get("articleUpdate", {})
        errors = result.get("userErrors") or []
        if errors:
            detail = "; ".join(str(error.get("message") or error) for error in errors)
            return PublishResult(ok=False, dry_run=False, error=detail, raw=data)
        updated = result.get("article") or {}
        if not updated.get("id"):
            return PublishResult(ok=False, dry_run=False, error="Shopify did not return the updated article", raw=data)
        return PublishResult(
            ok=True,
            dry_run=False,
            post_id=str(updated["id"]),
            url=self._article_url(str(updated.get("handle") or req.slug)),
            raw=data,
        )

    async def _blog_id_for_publish(self) -> str:
        configured_id = str(self._config("blogId", "blog_id") or "").strip()
        if configured_id:
            return configured_id

        expected_handle = str(self._config("blogHandle", "blog_handle") or "news").strip().casefold()
        data = await self._graphql(
            """
            query Blogs($first: Int!) {
              blogs(first: $first) { nodes { id handle } }
            }
            """,
            {"first": 250},
        )
        for blog in data.get("blogs", {}).get("nodes", []):
            if str(blog.get("handle") or "").casefold() == expected_handle and blog.get("id"):
                return str(blog["id"])
        raise ExternalCallError(
            f"Shopify cannot find blog handle '{expected_handle}'. Configure api_config.blogId or create the blog first."
        )

    def connection_info(self) -> dict[str, Any]:
        shop = self._shop_domain(raise_error=False)
        client_id = self._credentials.get("client_id", "")
        client_secret = self._credentials.get("client_secret", "")
        api_version = self._api_version()
        endpoint = f"https://{shop}/admin/api/{api_version}/graphql.json" if shop else ""
        checks = [
            {"key": "endpoint", "label": "Shopify GraphQL 地址", "status": "ok" if endpoint else "failed", "detail": _display_url(endpoint) if endpoint else "站点 domain 必须是 *.myshopify.com"},
            {"key": "auth", "label": "站点 Client Credentials", "status": "ok" if client_id and client_secret else "failed", "detail": "已加载站点加密凭据" if client_id and client_secret else "请在该站点的 Shopify 连接卡片中配置凭据"},
        ]
        return {
            "connector_type": self.connector_type,
            "request": {"method": "POST", "url": _display_url(endpoint), "auth": "X-Shopify-Access-Token"},
            "config": {"base_url": _display_url(f"https://{shop}" if shop else ""), "api_version": api_version, "configured_keys": ["client_id", "client_secret"] if client_id and client_secret else []},
            "checks": checks,
        }

    async def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        shop = self._shop_domain()
        token, _ = await self._access_token(shop)
        response = await request_json(
            "POST",
            f"https://{shop}/admin/api/{self._api_version()}/graphql.json",
            client_label="shopify_graphql",
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
            json={"query": query, "variables": variables or {}},
            timeout=60,
            max_attempts=max_attempts,
        )
        errors = response.get("errors") or []
        if errors:
            detail = "; ".join(str(error.get("message") or error) for error in errors)
            raise ExternalCallError(f"Shopify GraphQL: {detail}")
        return response.get("data") or {}

    async def _access_token(self, shop: str, *, use_cache: bool = True) -> tuple[str, str]:
        client_id = self._credentials.get("client_id", "")
        client_secret = self._credentials.get("client_secret", "")
        if not client_id or not client_secret:
            raise ExternalCallError("Shopify 站点缺少加密 client_id/client_secret")
        site_id = str(self.site.get("id") or self.site.get("site_id") or "")
        credential_hash = hashlib.sha256(f"{client_id}\0{client_secret}".encode()).hexdigest()[:16]
        cache_key = f"{site_id}:{shop}:{credential_hash}"
        now = time.time()
        for key, value in list(_SHOPIFY_TOKEN_CACHE.items()):
            if value[0] <= now:
                _SHOPIFY_TOKEN_CACHE.pop(key, None)
        if len(_SHOPIFY_TOKEN_CACHE) > 256:
            for key, _ in sorted(_SHOPIFY_TOKEN_CACHE.items(), key=lambda item: item[1][0])[:64]:
                _SHOPIFY_TOKEN_CACHE.pop(key, None)
        cached = _SHOPIFY_TOKEN_CACHE.get(cache_key) if use_cache else None
        if cached and cached[0] > time.time() + 60:
            return cached[1], cached[2]
        response = await request_json(
            "POST",
            f"https://{shop}/admin/oauth/access_token",
            client_label="shopify_token",
            headers={"Accept": "application/json"},
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=30,
            max_attempts=1,
        )
        token = str(response.get("access_token") or "")
        if not token:
            raise ExternalCallError("Shopify token response missing access_token")
        expires_in = max(300, int(response.get("expires_in") or 86400))
        scopes = str(response.get("scope") or "")
        _SHOPIFY_TOKEN_CACHE[cache_key] = (time.time() + expires_in, token, scopes)
        return token, scopes

    def _config(self, *keys: str) -> str:
        config = self.site.get("api_config") or {}
        for key in keys:
            if config.get(key):
                return str(config[key]).strip()
        return ""

    def _api_version(self) -> str:
        value = self._config("apiVersion", "api_version") or get_settings().shopify_api_version
        return value if re.fullmatch(r"\d{4}-\d{2}", value) else "2026-07"

    def _shop_domain(self, *, raise_error: bool = True) -> str:
        raw = self._config("shopDomain", "shop_domain") or self.site.get("domain") or self.site.get("base_url") or ""
        parsed = urlsplit(str(raw).strip() if "://" in str(raw) else f"//{str(raw).strip()}")
        host = (parsed.hostname or "").lower().rstrip(".")
        valid = bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", host))
        if not valid and raise_error:
            raise ExternalCallError("Shopify domain 必须是合法的 *.myshopify.com")
        return host if valid else ""

    def _article_url(self, handle: str) -> str:
        blog_handle = self._config("blogHandle", "blog_handle") or "news"
        return f"https://{self._shop_domain()}/blogs/{blog_handle}/{handle}"


def _canonical_shopify_timestamp(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def markdown_to_gutenberg(source: str) -> str:
    """Convert generated Markdown into separate core WordPress blocks."""
    if "<!-- wp:" in (source or ""):
        return source
    lines = (source or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = re.match(r"^\s*```(.*)$", line)
        if fence:
            code: list[str] = []
            index += 1
            while index < len(lines) and not re.match(r"^\s*```\s*$", lines[index]):
                code.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(_wp_block("code", f'<pre class="wp-block-code"><code>{escape(chr(10).join(code))}</code></pre>'))
            continue
        heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level = len(heading.group(1))
            blocks.append(_wp_block("heading", f"<h{level}>{_wp_inline(heading.group(2))}</h{level}>", f'{{"level":{level}}}'))
            index += 1
            continue
        if re.match(r"^\s*(?:---+|\*\*\*+|___+)\s*$", line):
            index += 1
            continue
        if index + 1 < len(lines) and "|" in line and _is_table_divider(lines[index + 1]):
            headers = _split_table_row(line)
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                rows.append(_split_table_row(lines[index]))
                index += 1
            head = "".join(f"<th>{_wp_inline(cell)}</th>" for cell in headers)
            body = "".join("<tr>" + "".join(f"<td>{_wp_inline(cell)}</td>" for cell in row) + "</tr>" for row in rows)
            blocks.append(_wp_block("table", f'<figure class="wp-block-table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></figure>'))
            continue
        list_item = re.match(r"^\s*([-+*]|\d+[.)])\s+(.+)$", line)
        if list_item:
            ordered = list_item.group(1)[0].isdigit()
            items = [list_item.group(2)]
            index += 1
            while index < len(lines):
                next_item = re.match(r"^\s*([-+*]|\d+[.)])\s+(.+)$", lines[index])
                if not next_item or next_item.group(1)[0].isdigit() != ordered:
                    break
                items.append(next_item.group(2))
                index += 1
            tag = "ol" if ordered else "ul"
            blocks.append(_wp_block("list", f"<{tag}>" + "".join(f"<li>{_wp_inline(item)}</li>" for item in items) + f"</{tag}>"))
            continue
        if re.match(r"^\s*>", line):
            quote: list[str] = []
            while index < len(lines) and re.match(r"^\s*>", lines[index]):
                quote.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            blocks.append(_wp_block("quote", f'<blockquote class="wp-block-quote"><p>{_wp_inline(chr(10).join(quote))}</p></blockquote>'))
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip() and not _is_markdown_block_start(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append(_wp_block("paragraph", f"<p>{_wp_inline(' '.join(paragraph))}</p>"))
    return "\n\n".join(blocks)


def oemapps_html_from_markdown(source: str) -> str:
    """Render generated Markdown as HTML accepted by the OEMApps ``content`` field."""
    rendered = markdown_to_gutenberg(source)
    return re.sub(r"<!--\s*/?wp:[\s\S]*?-->", "", rendered).strip()


def shopify_body_from_markdown(source: str, article_title: str) -> str:
    """Prepare an article body for Shopify, whose article title is stored separately.

    Shopify renders ``Article.title`` outside ``Article.body``.  The generator's
    leading Markdown H1 is therefore redundant on Shopify and must not create a
    second document H1.  A non-title H1 is demoted as well, so subsections cannot
    introduce a competing page-level heading.
    """
    title_key = _heading_key(article_title)
    removed_title = False
    normalized_lines: list[str] = []
    for line in (source or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        heading = re.match(r"^(\s*)#\s+(.+?)\s*#*\s*$", line)
        if not heading:
            normalized_lines.append(line)
            continue
        heading_text = heading.group(2)
        if not removed_title and title_key and _heading_key(heading_text) == title_key:
            removed_title = True
            continue
        normalized_lines.append(f"{heading.group(1)}## {heading_text}")

    body = markdown_to_gutenberg("\n".join(normalized_lines))
    # Preserve compatibility with a previously generated Gutenberg body that is
    # sent back through the connector, while still enforcing Shopify's no-H1 body rule.
    body = re.sub(r"<h1\b([^>]*)>", r"<h2\1>", body, flags=re.I)
    return re.sub(r"</h1\s*>", "</h2>", body, flags=re.I)


def _heading_key(value: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"[^\w]+", "", plain, flags=re.UNICODE).casefold()


def _shopify_seo_metafields(req: PublishRequest) -> list[dict[str, str]]:
    """Map generated SEO values to Shopify's built-in article SEO metafields."""
    fields: list[dict[str, str]] = []
    for key, value in (("title_tag", req.meta_title), ("description_tag", req.meta_description)):
        text = str(value or "").strip()
        if text:
            fields.append({
                "namespace": "global",
                "key": key,
                "type": "single_line_text_field",
                "value": text,
            })
    return fields


def _wp_block(name: str, content: str, attrs: str = "") -> str:
    suffix = f" {attrs}" if attrs else ""
    return f"<!-- wp:{name}{suffix} -->{content}<!-- /wp:{name} -->"


def _wp_inline(text: str) -> str:
    value = escape(text, quote=False)
    value = re.sub(r"!\[([^]]*)\]\((https?://[^)]+|/[^)]+)\)", r'<img src="\2" alt="\1" />', value)
    value = re.sub(r"\[([^]]+)\]\((https?://[^)]+|/[^)]+)\)", r'<a href="\2">\1</a>', value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", value)
    return re.sub(r"(?<!\w)\*([^*]+)\*|(?<!\w)_([^_]+)_", lambda m: f"<em>{m.group(1) or m.group(2)}</em>", value)


def _is_table_divider(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", line))


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_markdown_block_start(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(
        re.match(r"^\s*(?:#{1,6}\s|```|>|(?:---+|\*\*\*+|___+)\s*$)", line)
        or re.match(r"^\s*([-+*]|\d+[.)])\s+", line)
        or ("|" in line and index + 1 < len(lines) and _is_table_divider(lines[index + 1]))
    )


async def post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """走 app.clients.http_client 的 retry+timeout 链路。"""
    return await request_json(
        "POST",
        url,
        client_label="publish",
        headers=headers,
        json=body,
        timeout=60,
    )


def _join_endpoint(base_url: str, path: str) -> str:
    if str(path).startswith(("http://", "https://")):
        return str(path)
    return f"{str(base_url).rstrip('/')}/{str(path).lstrip('/')}"


def _display_url(url: str) -> str:
    parts = urlsplit(str(url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _openapi_page(data: Any) -> tuple[list[Any], dict[str, Any]]:
    if isinstance(data, list):
        return data, {}
    if not isinstance(data, dict):
        return [], {}
    if data.get("code") not in (None, 0, "0"):
        raise ExternalCallError(str(data.get("msg") or data.get("message") or f"remote API returned code {data.get('code')}"))
    payload = data.get("data")
    if isinstance(payload, list):
        return payload, data.get("paginate") or data.get("pagination") or {}
    if isinstance(payload, dict):
        items = payload.get("list") or payload.get("items") or payload.get("articles") or payload.get("data") or []
        pagination = payload.get("paginate") or payload.get("pagination") or data.get("paginate") or data.get("pagination") or {}
        return items if isinstance(items, list) else [], pagination if isinstance(pagination, dict) else {}
    items = data.get("list") or data.get("items") or data.get("articles") or []
    return items if isinstance(items, list) else [], data.get("paginate") or data.get("pagination") or {}


def _openapi_item(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    if data.get("code") not in (None, 0, "0"):
        raise ExternalCallError(str(data.get("msg") or data.get("message") or f"remote API returned code {data.get('code')}"))
    item = data.get("data") or data.get("item") or data.get("post") or data.get("article") or data
    return item if isinstance(item, dict) and any(item.get(key) is not None for key in ("id", "post_id", "articleId")) else None


def publisher_for_site(site: dict[str, Any], dry_run: bool = True) -> PublisherBase:
    """按显式 connector_type/site_type 路由；未知类型不再隐式当作 OpenAPI。"""
    cfg = site.get("api_config") or {}
    st = str(site.get("connector_type") or cfg.get("connector_type") or site.get("site_type") or "").lower()
    if st in {"wp", "wordpress"}:
        return WordPressPublisher(site, dry_run=dry_run)
    if st in {"shopify", "shopify_admin"}:
        return ShopifyPublisher(site, dry_run=dry_run)
    if st in {"main", "blog", "openapi", "custom_blog", "custom_saas", "custom_openapi"}:
        return OpenAPIPublisher(site, dry_run=dry_run)
    return UnsupportedPublisher(site, dry_run=dry_run, connector_type=st or "unknown")


def connector_for_site(site: dict[str, Any], dry_run: bool = True) -> PublisherBase:
    """Connector 命名入口；publisher_for_site 保留给旧调用方。"""
    return publisher_for_site(site, dry_run=dry_run)


class UnsupportedPublisher(PublisherBase):
    def __init__(self, site: dict[str, Any], dry_run: bool = True, connector_type: str = "unknown") -> None:
        super().__init__(site, dry_run=dry_run)
        self.connector_type = connector_type

    async def publish(self, req: PublishRequest) -> PublishResult:
        return PublishResult(
            ok=False,
            dry_run=self.dry_run,
            error=f"no connector registered for site type: {self.connector_type}",
        )


__all__ = [
    "PublishRequest",
    "PublishResult",
    "ImageUploadRequest",
    "ImageUploadResult",
    "PublisherBase",
    "OpenAPIPublisher",
    "WordPressPublisher",
    "ShopifyPublisher",
    "evict_shopify_token_cache",
    "UnsupportedPublisher",
    "publisher_for_site",
    "connector_for_site",
]
