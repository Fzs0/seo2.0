from __future__ import annotations

from knowledge.backend.app.schemas import ImportSignalsRequest
from knowledge.backend.app.signal_service import signal_content_hash


def test_signal_content_hash_ignores_whitespace_only_changes() -> None:
    assert signal_content_hash("Need better pricing\n\nfor teams") == signal_content_hash(
        "Need   better pricing for teams"
    )


def test_signal_import_accepts_platform_neutral_content_kinds() -> None:
    request = ImportSignalsRequest(
        source_name="Community export",
        platform="reddit",
        channel="forum",
        rights_confirmed=True,
        signals=[
            {
                "content_kind": "forum_thread",
                "title": "Looking for an alternative",
                "content": "I need a simpler workflow.",
                "thread_key": "thread-1",
                "language_code": "en",
                "market": "US",
                "engagement": {"score": 12},
            },
            {
                "content_kind": "forum_reply",
                "content": "The setup is too difficult.",
                "parent_external_id": "comment-1",
            },
        ],
    )

    assert len(request.signals) == 2
    assert request.signals[0].content_kind == "forum_thread"
    assert request.signals[1].content_kind == "forum_reply"
