from app.engine.locale import locale_for_market
from app.services.site_config_service import _normalize
from app.services.keyword_service import _canonical_site_label, analyze_keywords


def test_site_config_normalizes_market_and_language_codes():
    site = _normalize(
        {
            "name": "vapestest.de",
            "targetMarket": "DE / German",
            "targetLanguage": "German",
            "contentRole": "博客A-知识教程",
        },
        "blog",
    )

    assert site["market"] == "DE"
    assert site["language_code"] == "de"
    assert site["google_gl"] == "de"
    assert site["google_hl"] == "de"
    assert site["semrush_database"] == "de"


def test_site_config_keeps_public_article_url_template():
    site = _normalize(
        {
            "name": "topvapes.de",
            "siteUrl": "https://topvapes.de",
            "apiBaseUrl": "https://topvapes.de/api/open/v1",
            "articleUrlPath": "/blog/{slug}",
        },
        "blog",
    )

    assert site["api_config"]["articleUrlPath"] == "/blog/{slug}"


def test_locale_fallback_sets_google_language_code_without_presets():
    locale = locale_for_market("US / English")

    assert locale["googleHl"] == "en"


def test_role_label_resolves_to_one_concrete_site_for_locale():
    assert _canonical_site_label(
        "博客A-知识教程",
        {"market": "US", "languageCode": "en"},
        [{"name": "vapetopline", "content_role": "博客A-知识教程", "market": "US", "language_code": "en"}],
    ) == "vapetopline"


def test_import_keeps_site_unassigned(rule_payload):
    result = analyze_keywords(
        [{"keyword": "vape flavor", "intent": "informational", "volume": 100}],
        {"market": "US / English"},
        assign_site=False,
    )[0]

    assert result["assignedSite"] == ""
    assert result["status"] == "imported"
