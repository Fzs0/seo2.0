from __future__ import annotations

import pytest

from knowledge.backend.app.knowledge_service import (
    content_fingerprint,
    extract_claim_candidates,
)
from knowledge.backend.app.schemas import ImportDocumentRequest


def test_claim_extraction_is_deterministic_deduplicated_and_limited() -> None:
    paragraphs = [f"## Recommendation {number}" for number in range(10)]
    content = "\n\n".join([paragraphs[0], paragraphs[0].lower(), *paragraphs[1:]])

    first = extract_claim_candidates(content)
    second = extract_claim_candidates(content)

    assert first == second
    assert len(first) == 8
    assert first[0] == {
        "statement": "Recommendation 0",
        "excerpt": "## Recommendation 0",
        "locator": "paragraph:1",
    }
    assert [candidate["statement"] for candidate in first].count("Recommendation 0") == 1


def test_content_fingerprint_is_stable_sha256() -> None:
    value = content_fingerprint("same body")
    assert value == content_fingerprint("same body")
    assert len(value) == 64
    assert value != content_fingerprint("same body ")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/article",
        "https://user:secret@example.com/article",
        "https://example.com:bad/article",
        "not a url",
    ],
)
def test_import_rejects_unsafe_or_invalid_url_metadata(url: str) -> None:
    with pytest.raises(ValueError):
        ImportDocumentRequest(
            source_name="Example",
            canonical_url=url,
            title="A title",
            raw_content="Licensed content",
            rights_confirmed=True,
        )


def test_import_normalizes_url_without_accessing_it() -> None:
    request = ImportDocumentRequest(
        source_name="Example",
        canonical_url="HTTPS://Example.COM/path?q=1#section",
        title="A title",
        raw_content="Licensed content",
        rights_confirmed=True,
    )
    assert request.canonical_url == "https://example.com/path?q=1"
