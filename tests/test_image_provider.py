from app.clients.image_provider import _normalize_items


def test_pexels_images_use_the_image_source_url() -> None:
    items = _normalize_items(
        "pexels",
        {"photos": [{"id": 1, "url": "https://www.pexels.com/photo/1", "src": {"large": "https://images.pexels.com/photo-1.jpeg", "medium": "https://images.pexels.com/photo-1-small.jpeg"}}]},
    )

    assert items[0]["url"] == "https://images.pexels.com/photo-1.jpeg"
