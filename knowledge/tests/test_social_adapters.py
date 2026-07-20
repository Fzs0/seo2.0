from __future__ import annotations

from knowledge.backend.app.social_adapters import (
    RedditAdapter,
    YouTubeAdapter,
    _reddit_comment_content,
    adapter_for_platform,
)


def test_platform_registry_returns_concrete_adapters() -> None:
    assert isinstance(adapter_for_platform("reddit"), RedditAdapter)
    assert isinstance(adapter_for_platform("youtube"), YouTubeAdapter)
    assert adapter_for_platform("forum") is None


def test_reddit_comment_content_removes_display_metadata() -> None:
    raw = "author\n•\n2y ago\n\nThis is the actual customer comment.\n4 more replies"
    assert _reddit_comment_content(raw) == "This is the actual customer comment."
