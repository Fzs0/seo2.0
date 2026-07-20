from app.services.site_knowledge_service import _build_evidence, _build_prompt, _fallback_profile


def test_assigned_keywords_cannot_define_site_scope():
    site = {"content_scope": "vape guides, product comparisons", "knowledge_profile": {"index_scan": {"summary": {"pages": 3}}}}
    posts = [{"title": "Published buying guide", "primary_keyword": "vape buying guide"}]
    keywords = [{"keyword": "unrelated casino term", "topic_cluster": "casino", "page_type": "landing"}]

    prompt = _build_prompt(site, posts, keywords, [])
    evidence = _build_evidence(site, posts, keywords, [])
    fallback = _fallback_profile(site, posts, keywords)

    assert '"assigned_keywords"' not in prompt
    assert "unrelated casino term" not in prompt
    assert "不得用它扩展 in_scope_topics" in prompt
    assert all(item["source"] != "keyword_data" for item in evidence)
    assert fallback["in_scope_topics"] == ["vape guides", "product comparisons", "vape buying guide", "Published buying guide"]
    assert "casino" not in fallback["in_scope_topics"]
