import pytest

from app.services.strategy_decision_service import (
    build_keywordless_new_article_context,
    build_on_page_candidates,
)


def test_builds_safe_on_page_candidates_from_existing_page_audits() -> None:
    candidates = build_on_page_candidates(
        site={"id": "site-1", "business_id": "business-1", "name": "Example"},
        assets=[
            {
                "id": "home-1",
                "url": "https://example.com",
                "page_type": "home",
                "seo_audit": {"missing_tdk": ["meta_description"]},
            },
            {
                "id": "product-1",
                "url": "https://example.com/products/pan",
                "page_type": "product",
                "seo_audit": {"images_missing_alt": 2},
            },
            {
                "id": "category-1",
                "url": "https://example.com/collections/pans",
                "page_type": "category",
                "seo_audit": {"internal_link_count": 0},
            },
        ],
    )

    assert [(item["page_type"], item["subtype"]) for item in candidates] == [
        ("home", "meta"),
        ("product", "alt"),
        ("category", "internal_link"),
    ]
    assert all(item["strategy_type"] == "on_page_fix" for item in candidates)
    assert all(item["site_id"] == "site-1" for item in candidates)


def test_faq_and_meta_keywords_do_not_trigger_major_article_or_on_page_action() -> None:
    candidates = build_on_page_candidates(
        site={"id": "site-1", "business_id": "business-1"},
        assets=[
            {
                "id": "article-1",
                "url": "https://example.com/blog/guide",
                "page_type": "article",
                "seo_audit": {
                    "missing_faq_signal": True,
                    "missing_meta_keywords": True,
                },
            },
            {
                "id": "home-1",
                "url": "https://example.com",
                "page_type": "home",
                "seo_audit": {"missing_meta_keywords": True},
            },
        ],
    )

    assert candidates == []


def test_unsupported_or_unaddressable_pages_are_not_silently_actionable() -> None:
    candidates = build_on_page_candidates(
        site={"id": "site-1", "business_id": "business-1"},
        assets=[
            {"id": "page-1", "url": "", "page_type": "product", "seo_audit": {"images_missing_alt": 1}},
            {"id": "article-1", "url": "https://example.com/blog/x", "page_type": "article", "seo_audit": {"missing_tdk": ["meta_title"]}},
        ],
    )

    assert candidates == []


def test_same_on_page_url_variants_produce_one_candidate_and_one_lock() -> None:
    candidates = build_on_page_candidates(
        site={
            "id": "site-1",
            "business_id": "business-1",
            "base_url": "https://example.com",
        },
        assets=[
            {
                "id": "product-1",
                "url": "HTTP://WWW.EXAMPLE.COM/products/pan/?utm_source=test#details",
                "page_type": "product",
                "seo_audit": {"missing_tdk": ["meta_title"]},
            },
            {
                "id": "product-1-copy",
                "url": "https://example.com/products/pan",
                "page_type": "product",
                "seo_audit": {"missing_tdk": ["meta_title"]},
            },
        ],
    )

    assert len(candidates) == 1
    assert candidates[0]["target_url"] == "https://example.com/products/pan"
    assert candidates[0]["lock_key"] == candidates[0]["scope_key"]


def test_cross_site_on_page_url_never_becomes_a_candidate() -> None:
    candidates = build_on_page_candidates(
        site={
            "id": "site-1",
            "business_id": "business-1",
            "base_url": "https://example.com",
        },
        assets=[
            {
                "id": "product-1",
                "url": "https://attacker.example/products/pan",
                "page_type": "product",
                "seo_audit": {"missing_tdk": ["meta_title"]},
            }
        ],
    )

    assert candidates == []


def test_builds_keywordless_new_article_context_from_verified_evidence() -> None:
    context = build_keywordless_new_article_context(
        {
            "strategy_type": "new_article",
            "query": "how to choose a titanium tea set",
            "risk_gate_passed": True,
            "execution_evidence": {
                "search_intent": {"status": "confirmed", "source": "live_serp", "snapshot_id": "serp-1"},
                "topic_basis": {"product_or_category_match": True, "content_gap": True},
                "cannibalization": {"status": "clear"},
                "product_facts": [{"name": "material", "value": "titanium"}],
                "authority_sources": [],
                "evidence_sources": ["products", "live_serp", "content_audit"],
                "snapshots": {"serp": "serp-1", "content_audit": "audit-1"},
            },
        },
        site={
            "id": "site-1",
            "name": "Tea",
            "business_id": "business-1",
            "market": "US",
            "language_code": "en",
        },
    )

    assert context["id"] is None
    assert context["keyword"] == "how to choose a titanium tea set"
    assert context["assigned_site_id"] == "site-1"
    assert context["evidence_sources"] == ["products", "live_serp", "content_audit"]
    assert context["evidence_snapshot"]["serp"] == "serp-1"


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"search_intent": {"status": "missing"}}, "search intent"),
        ({"topic_basis": {}}, "topic basis"),
        ({"cannibalization": {"status": "overlap"}}, "cannibalization"),
        ({"evidence_sources": []}, "evidence source"),
    ],
)
def test_keywordless_new_article_context_rejects_incomplete_evidence(patch, message) -> None:
    evidence = {
        "search_intent": {"status": "confirmed"},
        "topic_basis": {"product_or_category_match": True, "content_gap": True},
        "cannibalization": {"status": "clear"},
        "product_facts": [{"name": "material", "value": "titanium"}],
        "evidence_sources": ["products"],
        **patch,
    }
    with pytest.raises(ValueError, match=message):
        build_keywordless_new_article_context(
            {
                "strategy_type": "new_article",
                "query": "topic",
                "risk_gate_passed": True,
                "execution_evidence": evidence,
            },
            site={"id": "site-1", "business_id": "business-1", "market": "US", "language_code": "en"},
        )
