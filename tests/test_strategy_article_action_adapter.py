from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import strategy_article_action_adapter as service
from app.services.strategy_action_service import compare_readback_fields


def _body(*, image_url: str | None = None) -> str:
    image = f"\n\n![Product reference]({image_url})" if image_url else ""
    return (
        "# Titanium Cutting Board Buying Guide\n\n"
        "## What buyers need to know\n\n"
        + ("Titanium cutting board material, care, and buying guidance for home cooks. " * 24)
        + image
        + "\n\n## FAQ\n\n"
        "### Is a titanium cutting board easy to clean?\n\n"
        + ("Clean it after every use and follow the product care instructions. " * 12)
    )


def _patch(*, image_url: str | None = None) -> dict:
    images = (
        [{"src": image_url, "alt": "Titanium cutting board product reference"}]
        if image_url
        else []
    )
    return {
        "title": "Titanium Cutting Board Buying Guide",
        "body": _body(image_url=image_url),
        "meta_title": "Titanium Cutting Board Buying Guide",
        "meta_description": (
            "Compare titanium cutting board benefits, care needs, practical tradeoffs, "
            "and buying considerations before choosing one for your kitchen."
        ),
        "images": images,
    }


def test_article_patch_is_canonical_and_rejects_url_fields() -> None:
    url = "https://cdn.example.com/product.png"
    patch = service.canonical_article_patch(_patch(image_url=url))

    assert patch["images"] == [url]
    assert patch["image_alts"][url] == "Titanium cutting board product reference"

    with pytest.raises(ValueError, match="forbidden fields"):
        service.canonical_article_patch({**_patch(), "slug": "must-not-change"})


def test_article_patch_keeps_uploaded_cover_media_identity() -> None:
    cover_url = "https://site.example.com/wp-content/uploads/cover.png"

    patch = service.canonical_article_patch(
        {
            **_patch(),
            "cover_image": {
                "image_id": "501",
                "src": cover_url,
                "alt": "Replacement pod beside its charging dock",
            },
        }
    )

    assert patch["cover_image"] == {
        "image_id": "501",
        "src": cover_url,
        "alt": "Replacement pod beside its charging dock",
    }


def test_article_patch_omits_cover_when_cover_was_not_approved() -> None:
    patch = service.canonical_article_patch(_patch())

    assert "cover_image" not in patch


def test_readback_ignores_optional_null_field_not_sent_by_adapter() -> None:
    differences = compare_readback_fields(
        {"title": "Guide", "cover_image": None},
        {"title": "Guide"},
        {
            "title": "Guide",
            "cover_image": {
                "image_id": "501",
                "src": "https://example.com/existing-cover.png",
                "alt": "Existing cover",
            },
        },
    )

    assert differences == [
        {
            "field": "title",
            "expected": "Guide",
            "submitted": "Guide",
            "actual": "Guide",
            "match": True,
        }
    ]


@pytest.mark.asyncio
async def test_content_openapi_preview_blocks_mixed_markdown_and_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The self-hosted renderer must never receive a mixed-format article."""

    async def load_context(*_args, **_kwargs):
        return {
            "strategy_decision": {"query": "titanium cutting board"},
            "post_id": "post-1",
            "post_title": "Old title",
            "post_content_md": "# Old title\n\nOld body",
            "post_meta_title": "Old title",
            "post_meta_description": "Old description",
            "site": {
                "id": "site-1",
                "business_id": "exdivo",
                "site_type": "blog",
                "base_url": "https://vapestest.de",
                "api_base_url": "https://vapestest.de/api/open/v1",
                "api_config": {"connector_type": "custom_openapi"},
            },
        }

    monkeypatch.setattr(service, "_load_action_context", load_context)
    mixed = _body().replace(
        "Titanium cutting board material, care, and buying guidance for home cooks. "
        * 24,
        "<p>"
        + (
            "Titanium cutting board material, care, and buying guidance for home cooks. "
            * 24
        )
        + "</p>",
    )
    adapter = service.StrategyArticleActionAdapter(object())  # type: ignore[arg-type]
    result = await adapter.preview(
        {
            "action_type": "update_article",
            "site_id": "site-1",
            "business_id": "exdivo",
            "source_strategy_task_id": "strategy-1",
        },
        {**_patch(), "body": mixed},
    )

    assert result == {
        "result": "blocked",
        "block_reason": "content_openapi article body must be pure Markdown; HTML tags are not allowed",
    }


@pytest.mark.asyncio
async def test_content_openapi_preview_accepts_pure_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def load_context(*_args, **_kwargs):
        return {
            "strategy_decision": {"query": "titanium cutting board"},
            "post_id": "post-1",
            "post_title": "Old title",
            "post_content_md": "# Old title\n\nOld body",
            "post_meta_title": "Old title",
            "post_meta_description": "Old description",
            "site": {
                "id": "site-1",
                "business_id": "exdivo",
                "site_type": "blog",
                "base_url": "https://vapestest.de",
                "api_base_url": "https://vapestest.de/api/open/v1",
                "api_config": {"connector_type": "custom_openapi"},
            },
        }

    monkeypatch.setattr(service, "_load_action_context", load_context)
    adapter = service.StrategyArticleActionAdapter(object())  # type: ignore[arg-type]
    result = await adapter.preview(
        {
            "action_type": "update_article",
            "site_id": "site-1",
            "business_id": "exdivo",
            "source_strategy_task_id": "strategy-1",
        },
        _patch(),
    )

    assert result["result"] == "updated"


def test_custom_blog_article_ingest_accepts_cover_without_fabricated_media_id() -> None:
    cover_url = "https://media-source.example.com/generated-cover.png"
    action = {
        "capability_snapshot": {
            "supported_fields": {
                "articles": [
                    "title",
                    "body",
                    "meta_title",
                    "meta_description",
                    "images",
                    "image_alts",
                    "cover_image",
                ]
            },
            "connectors": {
                "images": {
                    "status": "available",
                    "write": True,
                    "upload": True,
                    "ingest": True,
                    "transport": (
                        "business_oemapps_upload_then_article_publish"
                    ),
                }
            },
        }
    }

    assert service._image_patch_supported(action) is True
    assert service._cover_patch_supported(action) is True
    assert service._cover_image_id_required(action) is False

    patch = service.canonical_article_patch(
        {
            **_patch(),
            "cover_image": {
                "src": cover_url,
                "alt": "Generated editorial comparison cover",
            },
        },
        cover_image_id_required=False,
    )

    assert patch["cover_image"] == {
        "src": cover_url,
        "alt": "Generated editorial comparison cover",
    }


def test_article_ingest_readback_accepts_same_site_localized_media_urls() -> None:
    source = "https://media-source.example.com/generated-inline.png"
    approved = {
        "images": [source],
        "image_alts": {source: "Generated inline comparison"},
        "cover_image": {
            "src": source,
            "alt": "Generated comparison cover",
        },
    }
    readback = {
        "images": ["/assets/media/images/2026/07/generated-inline.webp"],
        "image_alts": {
            "/assets/media/images/2026/07/generated-inline.webp": (
                "Generated inline comparison"
            )
        },
        "cover_image": {
            "src": "/assets/media/images/2026/07/generated-cover.webp",
            "image_id": "",
            "alt": "",
        },
    }

    differences = compare_readback_fields(
        approved,
        approved,
        readback,
        media_transport="business_oemapps_upload_then_article_publish",
        canonical_hosts={"topvapes.de"},
    )

    assert all(item["match"] for item in differences)


def test_article_ingest_readback_accepts_unchanged_source_media_urls() -> None:
    source = "https://cdn.example.com/generated-inline.png"
    approved = {
        "images": [source],
        "image_alts": {source: "Generated inline comparison"},
        "cover_image": {
            "src": source,
            "alt": "Generated comparison cover",
        },
    }
    readback = {
        "images": [source],
        "image_alts": {source: "Generated inline comparison"},
        "cover_image": {"src": source, "alt": ""},
    }

    differences = compare_readback_fields(
        approved,
        approved,
        readback,
        media_transport="business_oemapps_upload_then_article_publish",
        canonical_hosts={"topvapes.de"},
    )

    assert all(item["match"] for item in differences)


def test_content_openapi_readback_uses_article_title_as_meta_title() -> None:
    readback = service.normalize_article_readback(
        {
            "title": "Welches Liquid schmeckt am besten?",
            "content": "<p>Body</p>",
            "excerpt": "Description",
        },
        site={
            "site_type": "blog",
            "api_base_url": "https://vape2026.de/api/open/v1",
            "api_config": {"connector_type": "custom_openapi"},
        },
    )

    assert readback["meta_title"] == "Welches Liquid schmeckt am besten?"
    assert service._article_read_only_fields(
        {
            "site_type": "blog",
            "api_base_url": "https://vape2026.de/api/open/v1",
            "api_config": {"connector_type": "custom_openapi"},
        }
    ) == ["meta_title"]


@pytest.mark.asyncio
async def test_custom_blog_media_endpoint_uploads_to_same_business_oemapps_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = {
        "action_id": "action-1",
        "site_id": "site-1",
        "run_mode": "approval_execution",
        "plan_id": "plan-1",
        "source_strategy_task_id": "strategy-1",
        "preflight_token": "preflight-1",
        "preflight_expires_at": "2099-01-01T00:00:00+00:00",
        "preflight_plan_id": "plan-1",
        "preflight_strategy_task_id": "strategy-1",
        "preflight_capability_snapshot_hash": "snapshot-1",
        "expected_fields": [
            "title",
            "body",
            "meta_title",
            "meta_description",
            "images",
            "image_alts",
            "cover_image",
        ],
        "capability_snapshot": {
            "supported_fields": {
                "articles": [
                    "title",
                    "body",
                    "meta_title",
                    "meta_description",
                    "images",
                    "image_alts",
                    "cover_image",
                ]
            },
            "connectors": {
                "images": {
                    "status": "available",
                    "write": True,
                    "upload": True,
                    "ingest": True,
                    "transport": (
                        "business_oemapps_upload_then_article_publish"
                    ),
                    "media_host_site_id": "exdivo-main",
                    "media_host_business_id": "exdivo",
                }
            },
        },
    }

    class Store:
        async def get(self, _action_id, lock=False):
            return action

        async def validate_lineage(self, _action):
            return None

        async def save(self, _action):
            return None

    async def valid_capability(*_args, **_kwargs):
        return None

    class Rows:
        def mappings(self):
            return self

        def first(self):
            return {
                "id": "site-1",
                "business_id": "exdivo",
                "site_key": "topvapes.de",
                "name": "topvapes.de",
                "site_type": "blog",
                "domain": "topvapes.de",
                "base_url": "https://topvapes.de",
                "api_base_url": "https://topvapes.de/api/open/v1",
                "status": "active",
                "api_config": {"openApiKey": "configured"},
            }

    class Session:
        async def execute(self, _statement, _params):
            return Rows()

    uploaded: dict = {}

    class Publisher:
        async def upload_image(self, request):
            uploaded["request"] = request
            return SimpleNamespace(
                ok=True,
                dry_run=False,
                image_id="16756439",
                src="https://imgcdn.example.com/generated.png",
                raw={"code": 0},
            )

    async def media_uploader(*_args, **_kwargs):
        return SimpleNamespace(
            publisher=Publisher(),
            media_host_site={"id": "exdivo-main", "site_key": "exdivo"},
            transport="business_oemapps_upload_then_article_publish",
        )

    monkeypatch.setattr(service, "SQLActionStore", lambda _session: Store())
    monkeypatch.setattr(
        service,
        "_validate_current_preflight_capability",
        valid_capability,
    )
    monkeypatch.setattr(
        service,
        "resolve_site_media_uploader",
        media_uploader,
    )

    response = await service.upload_strategy_article_image(
        Session(),  # type: ignore[arg-type]
        action_id="action-1",
        preflight_token="preflight-1",
        idempotency_key="media-1",
        request=service.ImageUploadRequest(
            type="base64",
            base64="data:image/png;base64,AAAA",
        ),
        dry_run=False,
    )

    assert response["media_host_site_id"] == "exdivo-main"
    assert response["media_host_site_key"] == "exdivo"
    assert response["image_id"] == "16756439"
    assert response["src"] == "https://imgcdn.example.com/generated.png"
    assert response["raw"] == {"code": 0}
    assert uploaded["request"].type == "base64"
    assert action["media_upload_receipts"]["media-1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_no_media_plan_cannot_upload_orphan_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = {
        "action_id": "action-1",
        "site_id": "site-1",
        "run_mode": "approval_execution",
        "plan_id": "plan-1",
        "source_strategy_task_id": "strategy-1",
        "preflight_token": "preflight-1",
        "preflight_expires_at": "2099-01-01T00:00:00+00:00",
        "preflight_plan_id": "plan-1",
        "preflight_strategy_task_id": "strategy-1",
        "preflight_capability_snapshot_hash": "snapshot-1",
        "expected_fields": [
            "title",
            "body",
            "meta_title",
            "meta_description",
        ],
        "capability_snapshot": {
            "supported_fields": {
                "articles": [
                    "title",
                    "body",
                    "meta_title",
                    "meta_description",
                    "images",
                    "image_alts",
                    "cover_image",
                ]
            },
            "connectors": {
                "images": {
                    "status": "available",
                    "write": True,
                    "upload": True,
                }
            },
        },
    }

    class Store:
        async def get(self, _action_id, lock=False):
            return action

        async def validate_lineage(self, _action):
            return None

    async def valid_capability(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "SQLActionStore", lambda _session: Store())
    monkeypatch.setattr(
        service,
        "_validate_current_preflight_capability",
        valid_capability,
    )

    with pytest.raises(ValueError, match="not requested by the formal plan"):
        await service.upload_strategy_article_image(
            object(),  # type: ignore[arg-type]
            action_id="action-1",
            preflight_token="preflight-1",
            idempotency_key="media-1",
            request=service.ImageUploadRequest(
                type="base64",
                base64="data:image/png;base64,AAAA",
            ),
            dry_run=False,
        )


@pytest.mark.asyncio
async def test_no_media_article_plan_passes_preflight_without_image_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = {
        "action_id": "action-1",
        "action_type": "update_article",
        "status": "planned",
        "business_id": "business-1",
        "site_id": "site-1",
        "plan_id": "plan-1",
        "source_strategy_task_id": "strategy-1",
        "expected_fields": [
            "title",
            "body",
            "meta_title",
            "meta_description",
        ],
        "capability_snapshot": {"capability_snapshot_hash": "snapshot-1"},
    }

    class Store:
        async def get(self, _action_id, lock=False):
            return action

        async def validate_lineage(self, _action, lock=False):
            return None

        async def save(self, updated):
            action.update(updated)
            return action

    async def load_context(*_args, **_kwargs):
        return {
            "market": "US",
            "language_code": "en",
            "post_external_id": "remote-1",
            "site": {"domain": "example.com"},
        }

    async def capabilities(*_args, **_kwargs):
        return {
            "capability_snapshot_hash": "snapshot-1",
            "supported_actions": {"update_article": "approval_required"},
        }

    async def generation_context(*_args, **_kwargs):
        return {"optional_patch_fields": [], "image_upload_endpoint": None}

    monkeypatch.setattr(service, "SQLActionStore", lambda _session: Store())
    monkeypatch.setattr(service, "_load_action_context", load_context)
    monkeypatch.setattr(service, "get_site_capabilities", capabilities)
    monkeypatch.setattr(service, "_build_article_generation_context", generation_context)

    result = await service.preflight_article_action(
        object(),  # type: ignore[arg-type]
        action_id="action-1",
        idempotency_key="preflight-1",
    )

    assert result["ready"] is True
    assert result["generation_context"]["image_upload_endpoint"] is None


@pytest.mark.asyncio
async def test_generation_context_does_not_expose_unplanned_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = {
        "action_type": "update_article",
        "site_id": "site-1",
        "expected_fields": [
            "title",
            "body",
            "meta_title",
            "meta_description",
        ],
        "capability_snapshot": {
            "supported_fields": {
                "articles": [
                    "title",
                    "body",
                    "meta_title",
                    "meta_description",
                    "images",
                    "image_alts",
                    "cover_image",
                ]
            },
            "connectors": {
                "images": {
                    "status": "available",
                    "write": True,
                    "upload": True,
                    "transport": "wordpress_media",
                }
            },
        },
    }

    class Rows:
        def mappings(self):
            return self

        def all(self):
            return []

    class Session:
        async def execute(self, *_args, **_kwargs):
            return Rows()

    async def load_context(*_args, **_kwargs):
        return {
            "site": {"domain": "example.com"},
            "strategy_decision": {},
        }

    monkeypatch.setattr(service, "_load_action_context", load_context)

    result = await service._build_article_generation_context(
        Session(),  # type: ignore[arg-type]
        action_id="action-1",
        action=action,
    )

    assert result["optional_patch_fields"] == []
    assert result["image_upload_endpoint"] is None
    assert result["media_transport"] is None
    assert result["accepted_media_inputs"] == []


def test_article_patch_is_filtered_to_formal_plan_fields() -> None:
    image_url = "https://cdn.example.com/editorial.png"
    action = {
        "expected_fields": [
            "title",
            "body",
            "meta_title",
            "meta_description",
        ],
        "capability_snapshot": {
            "supported_fields": {
                "articles": [
                    "title",
                    "body",
                    "meta_title",
                    "meta_description",
                    "images",
                    "image_alts",
                    "cover_image",
                ]
            },
            "connectors": {
                "images": {
                    "status": "available",
                    "write": True,
                    "upload": True,
                }
            },
        },
    }
    canonical = service.canonical_article_patch(_patch(image_url=image_url))

    filtered = service._capability_filtered_article_patch(action, canonical)

    assert set(filtered) == {
        "title",
        "body",
        "meta_title",
        "meta_description",
    }


@pytest.mark.asyncio
async def test_save_article_accepts_formal_plan_without_media_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def save(_session, article):
        captured.update(article)
        return {"id": "article-1"}

    monkeypatch.setattr(service, "save_article", save)
    patch = service._capability_filtered_article_patch(
        {
            "expected_fields": [
                "title",
                "body",
                "meta_title",
                "meta_description",
            ]
        },
        service.canonical_article_patch(_patch()),
    )

    result = await service._save_action_article(
        object(),  # type: ignore[arg-type]
        action={
            "action_id": "action-1",
            "action_type": "update_article",
            "site_id": "site-1",
            "topic": "titanium cutting board",
        },
        context={
            "post_slug": "titanium-cutting-board",
            "strategy_decision": {
                "query": "titanium cutting board",
                "internal_link_plan": [],
            },
        },
        execution_id="execution-1",
        patch=patch,
    )

    assert result == {"id": "article-1"}
    assert captured["image_plan"] == []


@pytest.mark.asyncio
async def test_recovery_proves_prewrite_failure_when_no_article_exists() -> None:
    class Rows:
        def mappings(self):
            return self

        def first(self):
            return {"raw_error": "'images'", "article_absent": True}

    class Session:
        async def execute(self, *_args, **_kwargs):
            return Rows()

    assert await service._has_confirmed_prewrite_failure(
        Session(),  # type: ignore[arg-type]
        {"action_id": "action-1"},
    ) is True


def test_article_readback_compares_markdown_with_remote_html_semantically() -> None:
    body = "# Guide\n\n## Details\n\nA clear buyer answer."
    differences = compare_readback_fields(
        {"body": body},
        {"body": body},
        {"body": "<h1>Guide</h1><h2>Details</h2><p>A clear buyer answer.</p>"},
    )

    assert differences == [
        {
            "field": "body",
            "expected": body,
            "submitted": body,
            "actual": "<h1>Guide</h1><h2>Details</h2><p>A clear buyer answer.</p>",
            "match": True,
        }
    ]


def test_article_readback_ignores_separately_verified_image_and_list_markup() -> None:
    markdown = (
        "# Guide\n\n"
        "![Product reference](https://cdn.example.com/product.png)\n\n"
        "## Steps\n\n"
        "1. Read the instructions.\n"
        "2. Let the product cool."
    )
    html = (
        "<h1>Guide</h1>"
        '<p><img src="https://cdn.example.com/product.png" alt="Product reference"></p>'
        "<h2>Steps</h2>"
        "<ol><li>Read the instructions.</li><li>Let the product cool.</li></ol>"
    )

    differences = compare_readback_fields(
        {"body": markdown},
        {"body": markdown},
        {"body": html},
    )

    assert differences[0]["match"] is True


def test_article_readback_accepts_wordpress_body_without_title_h1_and_with_table_blocks() -> None:
    markdown = (
        "# Foger Vape Refill Guide\n\n"
        "A sealed replacement pod is replaced rather than opened.\n\n"
        "## Compatibility\n\n"
        "| Component | Action |\n"
        "| --- | --- |\n"
        "| Pod | Replace |\n"
        "| Dock | Keep |\n\n"
        "1. Check the package.\n"
        "2. Confirm the dock."
    )
    wordpress = (
        "<!-- wp:paragraph --><p>A sealed replacement pod is replaced rather than opened.</p>"
        "<!-- /wp:paragraph -->"
        '<!-- wp:heading {"level":2} --><h2>Compatibility</h2><!-- /wp:heading -->'
        "<!-- wp:table --><figure><table><thead><tr>"
        "<th>Component</th><th>Action</th></tr></thead><tbody>"
        "<tr><td>Pod</td><td>Replace</td></tr>"
        "<tr><td>Dock</td><td>Keep</td></tr>"
        "</tbody></table></figure><!-- /wp:table -->"
        "<!-- wp:list --><ol><li>Check the package.</li>"
        "<li>Confirm the dock.</li></ol><!-- /wp:list -->"
    )

    differences = compare_readback_fields(
        {"body": markdown},
        {"body": markdown},
        {"body": wordpress},
    )

    assert differences[0]["match"] is True


def test_article_readback_ignores_markdown_table_alignment_markers() -> None:
    markdown = (
        "# Mixing Guide\n\n"
        "## Example\n\n"
        "| Goal | Shot | Aroma | Base |\n"
        "|---|---:|---:|---:|\n"
        "| 3 mg/ml | 15 ml | 8 ml | 77 ml |"
    )
    html = (
        "<h2>Example</h2><table><thead><tr>"
        "<th>Goal</th><th>Shot</th><th>Aroma</th><th>Base</th>"
        "</tr></thead><tbody><tr>"
        "<td>3 mg/ml</td><td>15 ml</td><td>8 ml</td><td>77 ml</td>"
        "</tr></tbody></table>"
    )

    differences = compare_readback_fields(
        {"body": markdown},
        {"body": markdown},
        {"body": html},
    )

    assert differences[0]["match"] is True


def test_oemapps_meta_description_alias_is_normalized() -> None:
    readback = service.normalize_remote_article(
        {
            "title": "Guide",
            "content": "<h1>Guide</h1>",
            "meta_title": "Guide Meta",
            "meta_descript": "A complete description returned by OEMApps.",
        }
    )

    assert readback["meta_description"] == "A complete description returned by OEMApps."


def test_wordpress_featured_media_is_normalized_and_compared() -> None:
    cover = {
        "image_id": "501",
        "src": "https://site.example.com/wp-content/uploads/cover.png",
        "alt": "Replacement pod beside its charging dock",
    }
    readback = service.normalize_remote_article(
        {
            "title": {"rendered": "Guide"},
            "content": {"rendered": "<p>Body</p>"},
            "featured_media": 501,
            "_embedded": {
                "wp:featuredmedia": [
                    {
                        "id": 501,
                        "source_url": cover["src"],
                        "alt_text": cover["alt"],
                    }
                ]
            },
        }
    )

    assert readback["cover_image"] == cover
    differences = compare_readback_fields(
        {"cover_image": cover},
        {"cover_image": cover},
        readback,
    )
    assert differences[0]["match"] is True


@pytest.mark.asyncio
async def test_article_adapter_preview_reads_the_bound_update_target() -> None:
    class Rows:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class Session:
        async def execute(self, _statement, _params=None):
            return Rows(
                {
                    "strategy_task_id": "strategy-1",
                    "strategy_status": "queued",
                    "strategy_decision": {
                        "query": "titanium cutting board",
                        "internal_link_plan": [],
                    },
                    "keyword_id": None,
                    "post_id": "post-1",
                    "source_article_id": None,
                    "site_id": "site-1",
                    "business_id": "avinoti",
                    "site_key": "avinoti",
                    "name": "Avinoti",
                    "site_type": "shopify",
                    "domain": "avinoti.shop",
                    "base_url": "https://avinoti.shop",
                    "api_base_url": None,
                    "api_config": {},
                    "site_status": "active",
                    "strategy_enabled": True,
                    "market": "US",
                    "language_code": "en",
                    "content_role": "main",
                    "post_external_id": "gid://shopify/Article/1",
                    "post_title": "Old title",
                    "post_slug": "old-title",
                    "post_url": "https://avinoti.shop/blogs/detail/old-title",
                    "post_content_md": "# Old title\n\nOld body",
                    "post_content_html": None,
                    "post_meta_title": "Old meta title",
                    "post_meta_description": "Old description",
                }
            )

    adapter = service.StrategyArticleActionAdapter(Session())  # type: ignore[arg-type]
    result = await adapter.preview(
        {
            "action_type": "update_article",
            "site_id": "site-1",
            "business_id": "avinoti",
            "source_strategy_task_id": "strategy-1",
        },
        _patch(),
    )

    assert result["result"] == "updated"
    assert result["before_snapshot"]["title"] == "Old title"
    assert result["proposed_patch"]["title"] == "Titanium Cutting Board Buying Guide"


@pytest.mark.asyncio
async def test_article_context_uses_action_target_asset_when_strategy_post_id_is_empty() -> None:
    """Formal AI plans keep the local post identity on the Action itself."""

    target_post_id = "2280f3f6-a31c-486e-bb57-fbe235301f70"

    class Rows:
        def mappings(self):
            return self

        def first(self):
            return {
                "strategy_task_id": "11111111-1111-1111-1111-111111111111",
                "strategy_status": "queued",
                "strategy_decision": {"query": "bestes tabak aroma"},
                "keyword_id": None,
                "post_id": target_post_id,
                "source_article_id": None,
                "site_id": "560f0dd6-73e9-482a-85ba-ae926bbd4eeb",
                "business_id": "exdivo",
                "site_key": "topvapes.de",
                "name": "topvapes.de",
                "site_type": "blog",
                "domain": "topvapes.de",
                "base_url": "https://topvapes.de",
                "api_base_url": "https://topvapes.de/api/open/v1",
                "api_config": {},
                "site_status": "active",
                "strategy_enabled": True,
                "market": "DE",
                "language_code": "de",
                "content_role": "brand_blog",
                "post_external_id": "9",
                "post_title": "Bestes Tabak Aroma",
                "post_slug": "bestes-tabak-aroma",
                "post_url": "https://topvapes.de/blog/bestes-tabak-aroma",
                "post_content_md": "## Auswahl\n\nBestehender Inhalt.",
                "post_content_html": None,
                "post_meta_title": "Bestes Tabak Aroma",
                "post_meta_description": "Bestehende Beschreibung.",
                "post_cover_url": None,
                "post_raw": {},
            }

    class Session:
        async def execute(self, statement, params):
            assert "post.id::text AS post_id" in str(statement)
            assert params["strategy_task_id"] == (
                "11111111-1111-1111-1111-111111111111"
            )
            assert params["target_post_id"] == target_post_id
            return Rows()

    context = await service._load_action_context(
        Session(),  # type: ignore[arg-type]
        {
            "source_strategy_task_id": (
                "11111111-1111-1111-1111-111111111111"
            ),
            "target_asset_id": target_post_id,
            "site_id": "560f0dd6-73e9-482a-85ba-ae926bbd4eeb",
            "business_id": "exdivo",
        },
    )

    assert context["post_id"] == target_post_id
    assert context["post_external_id"] == "9"
    assert context["post_slug"] == "bestes-tabak-aroma"


@pytest.mark.asyncio
async def test_generation_context_is_blocked_before_preflight() -> None:
    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def first(self):
            return self.rows[0] if self.rows else None

        def all(self):
            return self.rows

    class Session:
        async def execute(self, statement, _params=None):
            sql = str(statement)
            if "payload->>'kind'='strategy_action'" in sql:
                return Rows(
                    [
                        {
                            "payload": {
                                "action_id": "action-1",
                                "run_id": "run-1",
                                "business_id": "exdivo",
                                "site_id": "site-1",
                                "source_strategy_task_id": "strategy-1",
                                "action_type": "new_article",
                                "capability_snapshot": {
                                    "supported_fields": {
                                        "articles": [
                                            "title",
                                            "body",
                                            "meta_title",
                                            "meta_description",
                                            "images",
                                            "image_alts",
                                        ]
                                    },
                                    "connectors": {
                                        "images": {
                                            "status": "unavailable",
                                            "upload": False,
                                        }
                                    },
                                },
                            }
                        }
                    ]
                )
            if "FROM seo_agent.tasks strategy" in sql:
                return Rows(
                    [
                        {
                            "strategy_task_id": "strategy-1",
                            "strategy_status": "queued",
                            "strategy_decision": {"query": "vape guide"},
                            "keyword_id": None,
                            "post_id": None,
                            "source_article_id": None,
                            "site_id": "site-1",
                            "business_id": "exdivo",
                            "site_key": "vape2026",
                            "name": "Vape 2026",
                            "site_type": "blog",
                            "domain": "vape2026.com",
                            "base_url": "https://vape2026.com",
                            "api_base_url": "https://api.vape2026.com",
                            "api_config": {},
                            "site_status": "active",
                            "strategy_enabled": True,
                            "market": "US",
                            "language_code": "en",
                            "content_role": "traffic",
                            "post_external_id": None,
                            "post_title": None,
                            "post_slug": None,
                            "post_url": None,
                            "post_content_md": None,
                            "post_content_html": None,
                            "post_meta_title": None,
                            "post_meta_description": None,
                        }
                    ]
                )
            if "FROM seo_agent.products" in sql:
                return Rows([])
            raise AssertionError(f"unexpected SQL: {sql}")

    with pytest.raises(ValueError, match="preflight token"):
        await service.get_article_generation_context(
            Session(),  # type: ignore[arg-type]
            action_id="action-1",
        )


@pytest.mark.asyncio
async def test_article_adapter_execute_bridges_approval_publish_readback_and_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Session:
        async def commit(self):
            calls.append("commit")

    context = {
        "strategy_status": "queued",
        "strategy_decision": {
            "query": "titanium cutting board",
            "internal_link_plan": [],
        },
        "post_external_id": None,
        "site": {"id": "site-1", "business_id": "avinoti"},
    }

    async def load(*_args, **_kwargs):
        calls.append("load")
        return context

    async def approve(*_args, **_kwargs):
        calls.append("approve")
        return "execution-1"

    async def claim(*_args, **_kwargs):
        calls.append("claim")

    async def save(*_args, **_kwargs):
        calls.append("save")
        return {"id": "article-1"}

    async def effect(*_args, **_kwargs):
        calls.append("effect")
        return {"id": "effect-1"}

    async def publish(*_args, **_kwargs):
        calls.append("publish")
        return {
            "ok": True,
            "post_id": "remote-1",
            "task_id": "publish-1",
            "url": "https://avinoti.shop/blogs/detail/guide",
            "idempotent": False,
            "reused": False,
        }

    async def mark(*_args, **_kwargs):
        calls.append("mark")

    async def finish(*_args, **_kwargs):
        calls.append("finish")

    async def read(*_args, **_kwargs):
        calls.append("readback")
        patch = service.canonical_article_patch(_patch())
        return {
            "title": patch["title"],
            "body": patch["body"],
            "summary": patch["meta_description"],
            "meta_title": patch["meta_title"],
        }

    monkeypatch.setattr(service, "_load_action_context", load)
    monkeypatch.setattr(service, "_ensure_approved_execution", approve)
    monkeypatch.setattr(service, "_claim_execution", claim)
    monkeypatch.setattr(service, "_save_action_article", save)
    monkeypatch.setattr(service, "ensure_effect", effect)
    monkeypatch.setattr(service, "publish_article", publish)
    monkeypatch.setattr(service, "mark_effect_published", mark)
    monkeypatch.setattr(service, "_finish_execution", finish)
    monkeypatch.setattr(service, "_read_remote_article", read)

    adapter = service.StrategyArticleActionAdapter(Session())  # type: ignore[arg-type]
    result = await adapter.execute(
        {
                "action_id": "action-1",
                "run_id": "run-1",
                "run_mode": "approval_execution",
                "business_id": "avinoti",
            "site_id": "site-1",
            "source_strategy_task_id": "strategy-1",
            "action_type": "new_article",
            "approved_patch": _patch(),
            "generation_mode": "model",
            "generation_provider": "openai",
            "generation_model": "gpt-5.6-sol",
        }
    )

    assert result["result"] == "created"
    assert result["article_id"] == "article-1"
    assert result["publish_task_id"] == "publish-1"
    assert result["effect_id"] == "effect-1"
    assert calls.index("approve") < calls.index("publish") < calls.index("readback")


@pytest.mark.asyncio
async def test_article_adapter_recovery_reconciles_local_lineage_after_exact_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    reconciled: dict = {}
    patch = service.canonical_article_patch(_patch())
    context = {
        "strategy_decision": {
            "query": "titanium cutting board",
            "strategy_type": "update_article",
        },
        "post_external_id": "2588676",
        "post_url": "https://avinoti.shop/blogs/titanium-cutting-board",
        "site": {"id": "site-1", "business_id": "avinoti"},
    }

    async def load(*_args, **_kwargs):
        calls.append("load")
        return context

    async def read(*_args, **_kwargs):
        calls.append("readback")
        return {
            "id": "2588676",
            "title": patch["title"],
            "content": patch["body"],
            "meta_title": patch["meta_title"],
            "meta_description": patch["meta_description"],
        }

    async def reconcile(*_args, **kwargs):
        calls.append("reconcile")
        reconciled.update(kwargs)
        return {
            "article_id": "article-1",
            "execution_task_id": "execution-1",
            "publish_task_id": "publish-1",
            "effect_id": "effect-1",
            "target_url": "https://avinoti.shop/blogs/titanium-cutting-board",
        }

    monkeypatch.setattr(service, "_load_action_context", load)
    monkeypatch.setattr(service, "_read_remote_article", read)
    monkeypatch.setattr(service, "_reconcile_confirmed_article_execution", reconcile)

    adapter = service.StrategyArticleActionAdapter(object())  # type: ignore[arg-type]
    result = await adapter.recover(
        {
            "action_id": "action-1",
            "run_id": "run-1",
            "business_id": "avinoti",
            "site_id": "site-1",
            "source_strategy_task_id": "strategy-1",
            "action_type": "update_article",
            "approved_patch": patch,
            "article_id": "article-1",
            "execution_task_id": "execution-1",
            "publish_task_id": "publish-1",
            "effect_id": "effect-1",
            "target_url": "https://avinoti.shop/blogs/titanium-cutting-board",
        }
    )

    assert result["recovery_status"] == "confirmed_applied"
    assert result["article_id"] == "article-1"
    assert result["publish_task_id"] == "publish-1"
    assert result["effect_id"] == "effect-1"
    assert reconciled["remote_id"] == "2588676"
    assert reconciled["readback"] == patch
    assert calls == ["load", "readback", "reconcile"]


@pytest.mark.asyncio
async def test_confirmed_recovery_restores_lineage_from_unique_strategy_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-write failure must not strand an Action before IDs are copied back."""

    class Result:
        def __init__(self, *, rows=None, first=None):
            self._rows = rows or []
            self._first = first

        def mappings(self):
            return self

        def all(self):
            return self._rows

        def first(self):
            return self._first

    class Session:
        def __init__(self):
            self.results = [
                Result(
                    rows=[
                        {
                            "execution_task_id": "execution-1",
                            "article_id": "article-1",
                            "publish_task_id": "publish-1",
                        }
                    ]
                ),
                Result(first={"id": "article-1"}),
                Result(first={"id": "publish-1"}),
            ]
            self.commits = 0

        async def execute(self, *_args, **_kwargs):
            return self.results.pop(0)

        async def commit(self):
            self.commits += 1

    calls: list[tuple[str, dict]] = []

    async def finish(*_args, **kwargs):
        calls.append(("finish", kwargs))

    async def effect(*_args, **kwargs):
        calls.append(("effect", kwargs))
        return {"id": "effect-1"}

    async def mark(*_args, **kwargs):
        calls.append(("mark", kwargs))

    monkeypatch.setattr(service, "_finish_execution", finish)
    monkeypatch.setattr(service, "ensure_effect", effect)
    monkeypatch.setattr(service, "mark_effect_published", mark)

    session = Session()
    result = await service._reconcile_confirmed_article_execution(
        session,  # type: ignore[arg-type]
        action={
            "action_id": "action-1",
            "site_id": "site-1",
            "source_strategy_task_id": "strategy-1",
            "action_type": "update_article",
            "target_url": "https://example.com/blogs/guide",
        },
        context={
            "post_url": "https://example.com/blogs/guide",
            "strategy_decision": {"query": "guide"},
        },
        remote_id="remote-1",
        readback={"title": "Guide"},
    )

    assert result["article_id"] == "article-1"
    assert result["execution_task_id"] == "execution-1"
    assert result["publish_task_id"] == "publish-1"
    assert session.commits == 1
    assert calls[0][0] == "finish"


@pytest.mark.asyncio
async def test_confirmed_recovery_refuses_ambiguous_strategy_execution_lineage() -> None:
    class Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "execution_task_id": "execution-2",
                    "article_id": "article-2",
                    "publish_task_id": "publish-2",
                },
                {
                    "execution_task_id": "execution-1",
                    "article_id": "article-1",
                    "publish_task_id": "publish-1",
                },
            ]

    class Session:
        async def execute(self, *_args, **_kwargs):
            return Result()

    with pytest.raises(ValueError, match="missing or ambiguous"):
        await service._reconcile_confirmed_article_execution(
            Session(),  # type: ignore[arg-type]
            action={
                "action_id": "action-1",
                "site_id": "site-1",
                "source_strategy_task_id": "strategy-1",
                "action_type": "update_article",
                "target_url": "https://example.com/blogs/guide",
            },
            context={
                "post_url": "https://example.com/blogs/guide",
                "strategy_decision": {"query": "guide"},
            },
            remote_id="remote-1",
            readback={"title": "Guide"},
        )


@pytest.mark.asyncio
async def test_article_adapter_recovery_does_not_hide_local_reconciliation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = service.canonical_article_patch(_patch())

    async def load(*_args, **_kwargs):
        return {
            "post_external_id": "2588676",
            "site": {"id": "site-1", "business_id": "avinoti"},
        }

    async def read(*_args, **_kwargs):
        return {
            "id": "2588676",
            "title": patch["title"],
            "content": patch["body"],
            "meta_title": patch["meta_title"],
            "meta_description": patch["meta_description"],
        }

    async def fail_reconciliation(*_args, **_kwargs):
        raise RuntimeError("local lineage transaction failed")

    monkeypatch.setattr(service, "_load_action_context", load)
    monkeypatch.setattr(service, "_read_remote_article", read)
    monkeypatch.setattr(
        service,
        "_reconcile_confirmed_article_execution",
        fail_reconciliation,
    )

    adapter = service.StrategyArticleActionAdapter(object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="local lineage transaction failed"):
        await adapter.recover(
            {
                "action_id": "action-1",
                "business_id": "avinoti",
                "site_id": "site-1",
                "source_strategy_task_id": "strategy-1",
                "action_type": "update_article",
                "approved_patch": patch,
            }
        )


@pytest.mark.asyncio
async def test_article_adapter_recovery_classifies_exact_old_snapshot_as_not_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_patch = service.canonical_article_patch(_patch())
    new_patch = service.canonical_article_patch(
        {
            **_patch(),
            "title": "Updated Titanium Cutting Board Buying Guide",
            "meta_title": "Updated Titanium Cutting Board Buying Guide",
        }
    )
    context = {
        "post_external_id": "2588676",
        "site": {"id": "site-1", "business_id": "avinoti"},
    }

    async def load(*_args, **_kwargs):
        return context

    async def read(*_args, **_kwargs):
        return {
            "id": "2588676",
            "title": old_patch["title"],
            "content": old_patch["body"],
            "meta_title": old_patch["meta_title"],
            "meta_description": old_patch["meta_description"],
        }

    monkeypatch.setattr(service, "_load_action_context", load)
    monkeypatch.setattr(service, "_read_remote_article", read)

    adapter = service.StrategyArticleActionAdapter(object())  # type: ignore[arg-type]
    result = await adapter.recover(
        {
            "action_id": "action-1",
            "run_id": "run-1",
            "business_id": "avinoti",
            "site_id": "site-1",
            "source_strategy_task_id": "strategy-1",
            "action_type": "update_article",
            "before_snapshot": old_patch,
            "approved_patch": new_patch,
        }
    )

    assert result["recovery_status"] == "confirmed_not_applied"
    assert result["remote_response"]["remote_outcome"] == "confirmed_not_applied"


@pytest.mark.asyncio
async def test_approved_update_execution_persists_action_target_post_id() -> None:
    captured: dict = {}

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def first(self):
            return self.rows[0] if self.rows else None

        def mappings(self):
            return self

    class Session:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "pg_advisory_xact_lock" in sql:
                return Rows([])
            if "payload->>'strategy_action_id'=:action_id" in sql:
                return Rows([])
            if "payload->>'kind'='seo_strategy'" in sql:
                return Rows(
                    [
                        {
                            "id": "strategy-1",
                            "status": "queued",
                            "priority": "P1",
                            "score": 80,
                            "site_id": "site-1",
                            "keyword_id": None,
                            "post_id": None,
                            "article_id": None,
                            "title": "Update approved article",
                            "decision": {},
                            "business_id": "exdivo",
                            "plan_id": "plan-1",
                            "strategy_run_id": "run-1",
                            "schedule_class": "execute_now",
                        }
                    ]
                )
            if "INSERT INTO seo_agent.tasks" in sql:
                captured.update(params or {})
                return Rows([])
            if "UPDATE seo_agent.tasks" in sql:
                return Rows([])
            raise AssertionError(f"unexpected SQL: {sql}")

        async def commit(self):
            return None

    post_id = "2280f3f6-a31c-486e-bb57-fbe235301f70"
    await service._ensure_approved_execution(
        Session(),  # type: ignore[arg-type]
        action={
            "action_id": "action-1",
            "run_id": "run-1",
            "run_mode": "approval_execution",
            "business_id": "exdivo",
            "site_id": "site-1",
            "plan_id": "plan-1",
            "source_strategy_task_id": "strategy-1",
            "action_type": "update_article",
            "target_url": "https://topvapes.de/blog/bestes-tabak-aroma",
        },
        context={"post_id": post_id},
    )

    assert captured["post_id"] == post_id


@pytest.mark.asyncio
async def test_existing_update_execution_repairs_missing_target_post_id() -> None:
    repaired: dict = {}

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def first(self):
            return self.rows[0] if self.rows else None

        def mappings(self):
            return self

    class Session:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "pg_advisory_xact_lock" in sql:
                return Rows([])
            if "payload->>'strategy_action_id'=:action_id" in sql:
                return Rows([{"id": "execution-1"}])
            if "SET post_id=COALESCE" in sql:
                repaired.update(params or {})
                return Rows([])
            raise AssertionError(f"unexpected SQL: {sql}")

        async def commit(self):
            return None

    post_id = "2280f3f6-a31c-486e-bb57-fbe235301f70"
    execution_id = await service._ensure_approved_execution(
        Session(),  # type: ignore[arg-type]
        action={
            "action_id": "action-1",
            "run_id": "run-1",
            "run_mode": "approval_execution",
            "business_id": "exdivo",
            "site_id": "site-1",
            "plan_id": "plan-1",
            "source_strategy_task_id": "strategy-1",
            "action_type": "update_article",
        },
        context={"post_id": post_id},
    )

    assert execution_id == "execution-1"
    assert repaired == {"id": "execution-1", "post_id": post_id}
