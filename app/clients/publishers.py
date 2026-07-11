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
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.clients.http_client import ExternalCallError, request_json

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


class PublisherBase:
    """所有 publisher 的基类。"""

    def __init__(self, site: dict[str, Any], dry_run: bool = True) -> None:
        self.site = site
        self.dry_run = dry_run

    async def publish(self, req: PublishRequest) -> PublishResult:
        raise NotImplementedError

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


class OpenAPIPublisher(PublisherBase):
    """自定义 OpenAPI 协议：blog 站 (/api/open/v1) 与 main 站 (openapi.oemapps.com)。

    鉴权分两种：
    - blog 站：headers = {"openApiKey": "..."}
    - main 站：headers = {"token": "tokenA 或 tokenB"}

    Payload 形式（基于常见 openapi 协议猜的，dry_run 模式不会真发）：
        POST {api_base_url}
        Headers: {openApiKey | token: ...}
        Body: { title, content_md, slug, status, author, srcPrefix, imageId, ... }

    失败/不识别的站点用 dry_run 模式安全返回。
    """

    async def publish(self, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        return await self._real_publish(req)

    async def _real_publish(self, req: PublishRequest) -> PublishResult:
        api_base = self.site.get("api_base_url") or self.site.get("base_url")
        if not api_base:
            return PublishResult(ok=False, dry_run=False, error="site.api_base_url is empty")

        cfg = self.site.get("api_config") or {}
        # 鉴权：blog 站用 openApiKey，main 站用 token
        if "openApiKey" in cfg:
            headers = {"openApiKey": cfg["openApiKey"]}
        elif "tokenB" in cfg:
            headers = {"token": cfg["tokenB"]}
        elif "tokenA" in cfg:
            headers = {"token": cfg["tokenA"]}
        else:
            return PublishResult(ok=False, dry_run=False, error="no auth token in site.api_config")

        # 真实协议字段不确定（每个 OpenAPI 实现不同），这里按最常见的字段名
        body = {
            "title": req.title,
            "content": req.content_md,
            "contentMd": req.content_md,
            "slug": req.slug,
            "status": req.status,
            "author": req.author or cfg.get("defaultAuthor") or "admin",
            "srcPrefix": cfg.get("defaultSrcPrefix") or "/blogs/",
            "imageId": cfg.get("defaultImageId") or "",
            "excerpt": req.excerpt,
            "metaTitle": req.meta_title,
            "metaDescription": req.meta_description,
        }

        try:
            data = await post_json(f"{api_base.rstrip('/')}/articles/save", headers, body)
        except ExternalCallError as e:
            return PublishResult(ok=False, dry_run=False, error=str(e), raw={"body_sent": body})

        post_id = str(data.get("id") or data.get("post_id") or data.get("articleId") or "")
        url = data.get("url") or data.get("link") or f"{api_base}/article/{post_id}"
        return PublishResult(
            ok=True,
            dry_run=False,
            post_id=post_id,
            url=url,
            raw=data,
        )


class WordPressPublisher(PublisherBase):
    """WordPress REST + Application Password 协议：wp/* 站。

    鉴权：Basic Auth = base64("username:applicationPassword")
    Endpoint: POST {site_url}/wp-json/wp/v2/posts

    Payload (WP 标准):
        { title, content, status, slug, excerpt, meta: { _yoast_wpseo_title, _yoast_wpseo_metadesc } }
    """

    async def publish(self, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        return await self._real_publish(req)

    async def _real_publish(self, req: PublishRequest) -> PublishResult:
        site_url = (self.site.get("domain") or self.site.get("base_url") or "").rstrip("/")
        if not site_url:
            return PublishResult(ok=False, dry_run=False, error="site.domain is empty")

        cfg = self.site.get("api_config") or {}
        username = cfg.get("username")
        app_pwd = cfg.get("applicationPassword")
        if not username or not app_pwd:
            return PublishResult(
                ok=False, dry_run=False,
                error="site.api_config missing username or applicationPassword",
            )
        # Basic Auth: base64("user:pass") (注意 applicationPassword 里的空格保留)
        token = base64.b64encode(f"{username}:{app_pwd}".encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

        body = {
            "title": req.title,
            "content": req.content_md,
            "slug": req.slug,
            "status": req.status,
            "excerpt": req.excerpt or req.meta_description,
            "meta": {
                "_yoast_wpseo_title": req.meta_title,
                "_yoast_wpseo_metadesc": req.meta_description,
            },
        }
        if req.category_id:
            body["categories"] = [int(req.category_id)] if str(req.category_id).isdigit() else [req.category_id]
        if req.author:
            # WordPress author 必须是 numeric ID；这里只传 slug
            pass

        try:
            data = await post_json(
                f"{site_url}/wp-json/wp/v2/posts",
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


def publisher_for_site(site: dict[str, Any], dry_run: bool = True) -> PublisherBase:
    """按 site.site_type 路由到对应 adapter。"""
    st = (site.get("site_type") or "").lower()
    if st == "wp":
        return WordPressPublisher(site, dry_run=dry_run)
    # main / blog / other 都走 OpenAPI 协议（鉴权字段从 api_config 自动识别）
    return OpenAPIPublisher(site, dry_run=dry_run)


__all__ = [
    "PublishRequest",
    "PublishResult",
    "PublisherBase",
    "OpenAPIPublisher",
    "WordPressPublisher",
    "publisher_for_site",
]
