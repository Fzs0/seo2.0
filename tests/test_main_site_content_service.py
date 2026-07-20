from app.services.main_site_content_service import PAGE_ROLES


def test_main_site_page_roles_keep_commercial_pages_above_articles():
    assert PAGE_ROLES["product"][0] == "产品页"
    assert PAGE_ROLES["category"][0] == "分类页"
    assert "链接到产品页或分类页" in PAGE_ROLES["article"][1]
