from __future__ import annotations

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
    assert service._semantic_value("body", markdown) == service._semantic_value(
        "body", wordpress
    )


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
