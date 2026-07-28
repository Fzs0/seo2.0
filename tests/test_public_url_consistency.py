from __future__ import annotations

import pytest

from app.core.article_urls import resolve_article_public_url


@pytest.mark.parametrize(
    ("site", "remote_url", "canonical_url", "expected_url"),
    [
        pytest.param(
            {
                "site_type": "main",
                "domain": "exdivo.com",
                "base_url": "https://exdivo.com",
                "api_base_url": "https://openapi.oemapps.com",
                "api_config": {},
            },
            "https://exdivo.com/blogs/detail/2591906",
            None,
            "https://exdivo.com/blogs/portable-vape-guide",
            id="exdivo-oemapps",
        ),
        pytest.param(
            {
                "site_type": "main",
                "domain": "avinoti.shop",
                "base_url": "https://avinoti.shop",
                "api_base_url": "https://openapi.oemapps.com",
                "api_config": {},
            },
            "https://avinoti.shop/blogs/detail/2591906",
            None,
            "https://avinoti.shop/blogs/portable-vape-guide",
            id="avinoti-oemapps",
        ),
        pytest.param(
            {
                "site_type": "shopify",
                "domain": "healthyoxy.com",
                "base_url": "https://healthyoxy.com",
                "api_config": {
                    "connector_type": "shopify",
                    "shopDomain": "healthyoxy.myshopify.com",
                    "blogHandle": "news",
                },
            },
            "https://healthyoxy.myshopify.com/blogs/news/portable-vape-guide",
            "https://healthyoxy.myshopify.com/blogs/news/portable-vape-guide",
            "https://healthyoxy.com/blogs/news/portable-vape-guide",
            id="healthyoxy-shopify",
        ),
    ],
)
def test_article_execution_publish_and_effect_urls_share_public_contract(
    site: dict[str, object],
    remote_url: str,
    canonical_url: str | None,
    expected_url: str,
) -> None:
    """All four persisted URL positions must use the same reader-facing URL."""
    common = {"slug": "portable-vape-guide", "article_id": "2591906"}
    urls = {
        "article": resolve_article_public_url(
            site,
            **common,
            remote_url=remote_url,
            canonical_url=canonical_url,
        ),
        "execution": resolve_article_public_url(site, **common, remote_url=remote_url),
        "publish_task": resolve_article_public_url(
            site,
            **common,
            canonical_url=canonical_url,
        ),
        "effect_task": resolve_article_public_url(site, **common),
    }

    assert urls == dict.fromkeys(urls, expected_url)
