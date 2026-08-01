from app.core.article_urls import is_content_openapi_site, resolve_article_public_url


def test_oemapps_can_declare_an_explicit_canonical_template():
    site = {
        "site_type": "main",
        "base_url": "https://store.example",
        "api_base_url": "https://openapi.oemapps.com",
        "api_config": {"canonicalArticleUrlPath": "/articles/{slug}"},
    }

    assert (
        resolve_article_public_url(
            site,
            slug="guide",
            article_id="42",
            remote_url="https://technical.example/blogs/detail/42",
        )
        == "https://store.example/articles/guide"
    )


def test_relative_remote_url_is_made_absolute_for_generic_openapi():
    site = {
        "base_url": "https://blog.example",
        "api_base_url": "https://api.example",
    }

    assert (
        resolve_article_public_url(site, remote_url="/blog/guide")
        == "https://blog.example/blog/guide"
    )


def test_percent_encoded_slug_is_not_double_encoded():
    site = {
        "site_type": "main",
        "base_url": "https://store.example",
        "api_base_url": "https://openapi.oemapps.com",
    }

    assert (
        resolve_article_public_url(site, slug="tea%20guide")
        == "https://store.example/blogs/tea%20guide"
    )


def test_api_canonical_url_precedes_oemapps_default_when_no_site_override():
    site = {
        "site_type": "main",
        "base_url": "https://store.example",
        "api_base_url": "https://openapi.oemapps.com",
    }

    assert (
        resolve_article_public_url(
            site,
            slug="guide",
            remote_url="https://technical.example/blogs/detail/42",
            canonical_url="https://store.example/articles/guide",
        )
        == "https://store.example/articles/guide"
    )


def test_shared_api_host_does_not_make_a_non_main_site_oemapps():
    site = {
        "site_type": "blog",
        "base_url": "https://blog.example",
        "api_base_url": "https://openapi.oemapps.com",
    }

    assert (
        resolve_article_public_url(
            site,
            slug="guide",
            remote_url="https://technical.example/blog/guide",
        )
        == "https://blog.example/blog/guide"
    )


def test_known_content_openapi_uses_blog_slug_when_stored_template_is_stale():
    site = {
        "site_type": "blog",
        "base_url": "https://topvapes.de",
        "api_base_url": "https://topvapes.de/api/open/v1",
        "api_config": {},
    }

    assert (
        resolve_article_public_url(site, slug="bester-liquid-hersteller", article_id="12")
        == "https://topvapes.de/blog/bester-liquid-hersteller"
    )
    assert is_content_openapi_site(site) is True
