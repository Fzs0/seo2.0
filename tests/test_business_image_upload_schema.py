from app.api.v1.business import SiteImageUploadBody


def test_site_image_upload_body_accepts_wordpress_media_metadata() -> None:
    body = SiteImageUploadBody(
        type="url",
        url="https://cdn.example.com/cover.png",
        filename="article-cover.png",
        alt_text="Replacement pod beside its charging dock",
        title="Article cover",
        caption="Product-referenced editorial illustration.",
        dry_run=False,
    )

    assert body.filename == "article-cover.png"
    assert body.alt_text == "Replacement pod beside its charging dock"
    assert body.title == "Article cover"
    assert body.caption == "Product-referenced editorial illustration."
