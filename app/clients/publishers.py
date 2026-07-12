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

    connector_type = "unsupported"
    capabilities: tuple[str, ...] = ()

    def __init__(self, site: dict[str, Any], dry_run: bool = True) -> None:
        self.site = site
        self.dry_run = dry_run

    async def publish(self, req: PublishRequest) -> PublishResult:
        raise NotImplementedError

    async def read_articles(self, limit: int = 1) -> list[dict[str, Any]]:
        raise ExternalCallError(f"connector {self.connector_type} does not support read_articles")

    async def check_connection(self) -> dict[str, Any]:
        try:
            items = await self.read_articles(limit=1)
            return {
                "ok": True,
                "connector_type": self.connector_type,
                "capabilities": list(self.capabilities),
                "sample_count": len(items),
            }
        except Exception as error:  # noqa: BLE001
            return {
                "ok": False,
                "connector_type": self.connector_type,
                "capabilities": list(self.capabilities),
                "error": str(error),
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

    connector_type = "custom_openapi"
    capabilities = ("read_articles", "publish_article")

    async def read_articles(self, limit: int = 1) -> list[dict[str, Any]]:
        api_base = (self.site.get("api_base_url") or self.site.get("base_url") or "").rstrip("/")
        headers = self._auth_headers()
        if not api_base or not headers:
            raise ExternalCallError("site post connector missing api_base_url or auth")
        data = await request_json(
            "GET",
            f"{api_base}/articles",
            client_label="connector_openapi_articles",
            params={"limit": min(max(limit, 1), 100)},
            headers=headers,
            timeout=60,
        )
        items = data if isinstance(data, list) else data.get("items") or data.get("articles") or data.get("data") or []
        return [item for item in items if isinstance(item, dict)][:limit]

    def _auth_headers(self) -> dict[str, str]:
        cfg = self.site.get("api_config") or {}
        if cfg.get("openApiKey"):
            return {"openApiKey": cfg["openApiKey"]}
        if cfg.get("tokenB"):
            return {"token": cfg["tokenB"]}
        if cfg.get("tokenA"):
            return {"token": cfg["tokenA"]}
        return {}

    async def publish(self, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        return await self._real_publish(req)

    async def _real_publish(self, req: PublishRequest) -> PublishResult:
        api_base = self.site.get("api_base_url") or self.site.get("base_url")
        if not api_base:
            return PublishResult(ok=False, dry_run=False, error="site.api_base_url is empty")

        headers = self._auth_headers()
        if not headers:
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

    connector_type = "wordpress"
    capabilities = ("read_articles", "publish_article")

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
            f"{site_url}/wp-json/wp/v2/posts",
            client_label="connector_wordpress_articles",
            params={"per_page": min(max(limit, 1), 100), "page": 1, "status": "publish,draft", "_embed": 1},
            headers=headers,
            timeout=60,
        )
        return data if isinstance(data, list) else []

    async def publish(self, req: PublishRequest) -> PublishResult:
        if self.dry_run:
            return self._dry_response(req)
        return await self._real_publish(req)

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
    """按显式 connector_type/site_type 路由；未知类型不再隐式当作 OpenAPI。"""
    cfg = site.get("api_config") or {}
    st = str(site.get("connector_type") or cfg.get("connector_type") or site.get("site_type") or "").lower()
    if st in {"wp", "wordpress"}:
        return WordPressPublisher(site, dry_run=dry_run)
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
    "PublisherBase",
    "OpenAPIPublisher",
    "WordPressPublisher",
    "UnsupportedPublisher",
    "publisher_for_site",
    "connector_for_site",
]
