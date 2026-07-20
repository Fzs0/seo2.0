from app.services.post_analysis_service import _analysis_hash, analyze_post
from app.services.serp_competitor_service import parse_content_html


def test_html_post_analysis_covers_required_structure():
    result = analyze_post({
        "title": "Guide",
        "url": "https://example.com/guide",
        "content_html": "<h1>Guide</h1><h2>FAQ</h2><p>Answer</p><img src='x'><a href='/inside'>In</a><a href='https://other.test'>Out</a>",
        "meta_title": "SEO Guide",
        "meta_description": "Description",
        "primary_keyword": "guide",
        "source": "wp_api",
        "published_at": "2026-07-01T00:00:00Z",
    })

    assert result["title"] == "Guide"
    assert result["heading_counts"]["h2"] == 1
    assert result["faq_signal"] is True
    assert result["image_count"] == 1
    assert result["internal_link_count"] == 1
    assert result["external_link_count"] == 1
    assert result["content_status"] == "available"


def test_markdown_post_analysis_and_fetch_failure_status():
    markdown = "# Guide\n\n## Common questions\n\nAnswer with [internal](/inside), [external](https://other.test) and ![image](x.jpg)."
    result = analyze_post({"url": "https://example.com/guide", "content_md": markdown})
    failed = analyze_post({"fetch_error": "connector_public_article_html 状态码 404"})

    assert result["heading_counts"] == {"h1": 1, "h2": 1, "h3": 0, "h4": 0, "h5": 0, "h6": 0}
    assert result["image_count"] == 1
    assert result["internal_link_count"] == result["external_link_count"] == 1
    assert failed["content_status"] == "fetch_failed"


def test_html_fragment_is_analyzed_without_page_wrapper():
    result = parse_content_html("<h1>Title</h1><p>Body</p>", "https://example.com/post")

    assert result["heading_counts"]["h1"] == 1
    assert result["word_count"] == 2


def test_analysis_versions_ignore_fetch_time_but_track_content_changes():
    first = {"title": "Guide", "word_count": 10, "fetched_at": "2026-07-17T10:00:00Z"}
    refreshed = {**first, "fetched_at": "2026-07-17T11:00:00Z"}
    changed = {**refreshed, "word_count": 11}

    assert _analysis_hash(first) == _analysis_hash(refreshed)
    assert _analysis_hash(refreshed) != _analysis_hash(changed)
    assert _analysis_hash(first, "<p>cat dog</p>") != _analysis_hash(first, "<p>red fox</p>")
