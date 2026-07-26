"""Canonical public article URL rules shared by sync and publishing adapters."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit


def is_oemapps_site(site: dict[str, Any]) -> bool:
    """Return whether the site uses the shared OEMApps main-site backend."""
    raw = str(site.get("api_base_url") or "").strip()
    host = urlsplit(raw if "://" in raw else f"//{raw}").hostname or ""
    return (
        str(site.get("site_type") or "").strip().casefold() == "main"
        and host.casefold() == "openapi.oemapps.com"
    )


def resolve_article_public_url(
    site: dict[str, Any],
    *,
    slug: Any = None,
    article_id: Any = None,
    remote_url: Any = None,
    canonical_url: Any = None,
) -> str | None:
    """Resolve the reader-facing article URL without losing the remote detail URL.

    OEMApps returns ``/blogs/detail/{id}`` as a technical detail URL, while its
    storefront canonical is ``/blogs/{slug}``. Other OpenAPI adapters retain
    their existing remote URL or configured ``articleUrlPath`` contract.
    """
    clean_slug = unquote(str(slug or "").strip().strip("/"))
    clean_id = str(article_id or "").strip()
    raw_remote = str(remote_url or "").strip()
    raw_canonical = str(canonical_url or "").strip()
    base = _public_base(site)

    if is_oemapps_site(site):
        configured_template = str(
            (site.get("api_config") or {}).get("canonicalArticleUrlPath") or ""
        )
        canonical_path = _render_path_template(configured_template, clean_slug, clean_id)
        if canonical_path:
            return _absolute_public_url(base, canonical_path)
        if raw_canonical:
            return _rewrite_public_host(base, raw_canonical)
        canonical_path = _render_path_template("/blogs/{slug}", clean_slug, clean_id)
        if canonical_path:
            return _absolute_public_url(base, canonical_path)
        if raw_remote:
            return _rewrite_public_host(base, raw_remote)
        if clean_id and base:
            return urljoin(f"{base.rstrip('/')}/", f"blogs/detail/{quote(clean_id, safe='')}")
        return None

    if raw_remote:
        return _rewrite_public_host(base, raw_remote)

    path = str((site.get("api_config") or {}).get("articleUrlPath") or "")
    if not path and _is_known_content_openapi(site):
        path = "/blog/{slug}"
    path = _render_path_template(path, clean_slug, clean_id)
    if not path:
        return None
    return _absolute_public_url(base, path)


def _public_base(site: dict[str, Any]) -> str:
    base = str(site.get("base_url") or site.get("domain") or "").strip()
    if base and not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    return base


def _is_known_content_openapi(site: dict[str, Any]) -> bool:
    if str(site.get("site_type") or "").strip().casefold() != "blog":
        return False
    raw = str(site.get("api_base_url") or "").strip()
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    return parsed.path.rstrip("/").casefold() == "/api/open/v1"


def _rewrite_public_host(base: str, remote_url: str) -> str:
    if not base:
        return remote_url
    parsed = urlsplit(remote_url)
    public = urlsplit(base)
    if not parsed.netloc:
        return urljoin(f"{base.rstrip('/')}/", remote_url)
    if not parsed.netloc or not public.netloc or parsed.netloc.casefold() == public.netloc.casefold():
        return remote_url
    return urlunsplit((public.scheme, public.netloc, parsed.path, parsed.query, parsed.fragment))


def _render_path_template(template: str, slug: str, article_id: str) -> str | None:
    if not template:
        return None
    if "{slug}" in template and not slug:
        return None
    if "{id}" in template and not article_id:
        return None
    return template.replace("{slug}", quote(slug, safe="")).replace(
        "{id}",
        quote(article_id, safe=""),
    )


def _absolute_public_url(base: str, path: str) -> str | None:
    if path.startswith(("http://", "https://")):
        return path
    if not base:
        return None
    return urljoin(f"{base.rstrip('/')}/", path.lstrip("/"))


__all__ = ["is_oemapps_site", "resolve_article_public_url"]
