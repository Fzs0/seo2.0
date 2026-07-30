from __future__ import annotations

from typing import Any

import pytest

from app.services import strategy_on_page_action_adapter as module
from app.services.strategy_on_page_action_adapter import (
    OEMAppsOnPageActionAdapter,
    ShopifyProductSeoActionAdapter,
)


class _Mappings:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def first(self):
        return self.row


class _Result:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def mappings(self):
        return _Mappings(self.row)


class _Session:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    async def execute(self, _statement, _params):
        return _Result(self.row)


def _oem_action() -> dict[str, Any]:
    return {
        "action_id": "11111111-1111-1111-1111-111111111111",
        "site_id": "22222222-2222-2222-2222-222222222222",
        "action_type": "product_seo",
        "page_type": "product",
        "target_asset_id": "42",
        "remote_object_id": "product-42",
        "connector_id": "33333333-3333-3333-3333-333333333333",
        "connector_type": "oemapps",
        "target_url": "https://example.com/products/example",
        "expected_fields": ["meta_title", "meta_description"],
    }


@pytest.mark.asyncio
async def test_oemapps_product_adapter_previews_executes_and_reads_back(monkeypatch):
    row = {
        "target_asset_id": "42",
        "site_id": "22222222-2222-2222-2222-222222222222",
        "remote_object_id": "product-42",
        "connector_id": "33333333-3333-3333-3333-333333333333",
        "target_url": "https://example.com/products/example",
        "meta_title": "Before",
        "meta_description": "Before description",
    }
    states = [
        {
            "ok": True,
            "expected_snapshot_hash": "snapshot-1",
            "current": {
                "meta_title": "Before",
                "meta_description": "Before description",
            },
        },
        {
            "ok": True,
            "expected_snapshot_hash": "snapshot-2",
            "current": {
                "meta_title": "After",
                "meta_description": "After description",
            },
        },
    ]
    writes = []

    async def fake_preview(*_args, **_kwargs):
        return states.pop(0)

    async def fake_execute(*args, **kwargs):
        writes.append((args, kwargs))
        return {"ok": True, "no_op": False}

    monkeypatch.setattr(module, "preview_oemapps_seo_update", fake_preview)
    monkeypatch.setattr(module, "execute_oemapps_seo_update", fake_execute)
    adapter = OEMAppsOnPageActionAdapter(_Session(row))
    action = _oem_action()
    patch = {
        "meta_title": "After",
        "meta_description": "After description",
    }

    preview = await adapter.preview(action, patch)
    action.update(
        {
            "before_snapshot": preview["before_snapshot"],
            "proposed_patch": preview["proposed_patch"],
            "approved_patch": preview["proposed_patch"],
            "side_effect_confirmations": {
                "requires_variant_confirmation": True
            },
        }
    )
    result = await adapter.execute(action)

    assert result["result"] == "updated"
    assert result["submitted_patch"] == patch
    assert result["readback"] == patch
    assert writes[0][1]["confirm_variant_recreation"] is True


@pytest.mark.asyncio
async def test_on_page_adapter_rejects_cross_site_or_missing_target_before_remote(monkeypatch):
    called = False

    async def fake_preview(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(module, "preview_oemapps_seo_update", fake_preview)
    result = await OEMAppsOnPageActionAdapter(_Session(None)).preview(
        _oem_action(), {"meta_title": "After"}
    )

    assert result["result"] == "blocked"
    assert "another site" in result["block_reason"]
    assert called is False


@pytest.mark.asyncio
async def test_shopify_adapter_uses_guarded_writer_and_independent_remote_readback(
    monkeypatch,
):
    row = {
        "target_asset_id": "42",
        "site_id": "22222222-2222-2222-2222-222222222222",
        "remote_object_id": "gid://shopify/Product/42",
        "connector_id": None,
        "target_url": "https://example.com/products/example",
        "meta_title": "Before",
        "meta_description": "Before description",
        "source_updated_at": "2026-07-30T00:00:00+00:00",
    }
    calls = []

    async def fake_update(*_args, **kwargs):
        calls.append(kwargs)
        if kwargs["dry_run"]:
            return {
                "before": {
                    "meta_title": "Before",
                    "meta_description": "Before description",
                    "source_updated_at": "2026-07-30T00:00:00+00:00",
                },
                "after": {
                    "meta_title": kwargs["meta_title"],
                    "meta_description": kwargs["meta_description"],
                },
                "preview_token": "preview-token",
            }
        return {"ok": True, "dry_run": False, "no_op": False}

    async def fake_read(*_args, **_kwargs):
        return {
            "meta_title": "After",
            "meta_description": "After description",
        }

    monkeypatch.setattr(module, "update_shopify_product_seo", fake_update)
    monkeypatch.setattr(module, "read_shopify_product_seo_remote", fake_read)
    action = {
        **_oem_action(),
        "connector_type": "shopify",
        "connector_id": None,
        "remote_object_id": "gid://shopify/Product/42",
    }
    adapter = ShopifyProductSeoActionAdapter(_Session(row))
    patch = {
        "meta_title": "After",
        "meta_description": "After description",
    }

    preview = await adapter.preview(action, patch)
    action.update(
        {
            "before_snapshot": preview["before_snapshot"],
            "proposed_patch": patch,
            "approved_patch": patch,
        }
    )
    result = await adapter.execute(action)

    assert [call["dry_run"] for call in calls] == [True, False]
    assert result["readback"] == patch
    assert calls[1]["request_id"] == action["action_id"]


@pytest.mark.asyncio
async def test_shopify_recovery_persists_exact_readback_without_repeating_write(
    monkeypatch,
):
    row = {
        "target_asset_id": "42",
        "site_id": "22222222-2222-2222-2222-222222222222",
        "remote_object_id": "gid://shopify/Product/42",
        "connector_id": None,
        "target_url": "https://example.com/products/example",
        "meta_title": "Before",
        "meta_description": "Before description",
        "source_updated_at": "2026-07-30T00:00:00Z",
    }
    readback = {
        "meta_title": "After",
        "meta_description": "After description",
        "source_updated_at": "2026-07-30T13:38:57Z",
    }
    reconciliations = []

    async def fake_read(*_args, **_kwargs):
        return dict(readback)

    async def fake_reconcile(*_args, **kwargs):
        reconciliations.append(kwargs)
        return dict(readback)

    monkeypatch.setattr(module, "read_shopify_product_seo_remote", fake_read)
    monkeypatch.setattr(
        module,
        "reconcile_shopify_product_seo_recovery",
        fake_reconcile,
    )
    action = {
        **_oem_action(),
        "connector_type": "shopify",
        "connector_id": None,
        "remote_object_id": "gid://shopify/Product/42",
        "approved_patch": {
            "meta_title": "After",
            "meta_description": "After description",
        },
    }

    result = await ShopifyProductSeoActionAdapter(_Session(row)).recover(action)

    assert result["recovery_status"] == "confirmed_applied"
    assert reconciliations == [{
        "site_id": action["site_id"],
        "product_id": 42,
        "expected_remote_object_id": "gid://shopify/Product/42",
        "request_id": action["action_id"],
        "readback": readback,
    }]


@pytest.mark.asyncio
async def test_oemapps_category_adapter_preserves_membership_confirmation(
    monkeypatch,
):
    row = {
        "target_asset_id": "7",
        "site_id": "22222222-2222-2222-2222-222222222222",
        "remote_object_id": "collection-7",
        "connector_id": "33333333-3333-3333-3333-333333333333",
        "target_url": "https://example.com/collections/example",
    }
    states = [
        {
            "ok": True,
            "expected_snapshot_hash": "snapshot-1",
            "current": {"meta_title": "Before"},
        },
        {
            "ok": True,
            "expected_snapshot_hash": "snapshot-2",
            "current": {"meta_title": "After"},
        },
    ]
    writes = []

    async def fake_preview(*_args, **_kwargs):
        return states.pop(0)

    async def fake_execute(*args, **kwargs):
        writes.append((args, kwargs))
        return {"ok": True, "no_op": False}

    monkeypatch.setattr(module, "preview_collection_seo_update", fake_preview)
    monkeypatch.setattr(module, "execute_collection_seo_update", fake_execute)
    action = {
        **_oem_action(),
        "action_type": "category_seo",
        "page_type": "category",
        "target_asset_id": "7",
        "remote_object_id": "collection-7",
        "target_url": "https://example.com/collections/example",
        "expected_fields": ["meta_title"],
    }
    adapter = OEMAppsOnPageActionAdapter(_Session(row))
    preview = await adapter.preview(action, {"meta_title": "After"})
    action.update(
        {
            "before_snapshot": preview["before_snapshot"],
            "approved_patch": preview["proposed_patch"],
            "side_effect_confirmations": {
                "requires_membership_confirmation": True
            },
        }
    )

    result = await adapter.execute(action)

    assert result["readback"] == {"meta_title": "After"}
    assert writes[0][1]["confirm_membership_top_reset"] is True


@pytest.mark.asyncio
async def test_oemapps_homepage_adapter_requires_explicit_confirmation(
    monkeypatch,
):
    site_id = "22222222-2222-2222-2222-222222222222"
    row = {
        "target_asset_id": "9",
        "site_id": site_id,
        "remote_object_id": site_id,
        "connector_id": "33333333-3333-3333-3333-333333333333",
        "target_url": "https://example.com",
    }
    states = [
        {
            "ok": True,
            "expected_snapshot_hash": "snapshot-1",
            "current_public": {"meta_description": "Before"},
        },
        {
            "ok": True,
            "expected_snapshot_hash": "snapshot-2",
            "current_public": {"meta_description": "After"},
        },
    ]
    writes = []

    async def fake_preview(*_args, **_kwargs):
        return states.pop(0)

    async def fake_execute(*args, **kwargs):
        writes.append((args, kwargs))
        return {"ok": True, "no_op": False}

    monkeypatch.setattr(module, "preview_home_seo_update", fake_preview)
    monkeypatch.setattr(module, "execute_home_seo_update", fake_execute)
    action = {
        **_oem_action(),
        "action_type": "homepage_seo",
        "page_type": "homepage",
        "target_asset_id": "9",
        "remote_object_id": site_id,
        "target_url": "https://example.com",
        "expected_fields": ["meta_description"],
    }
    adapter = OEMAppsOnPageActionAdapter(_Session(row))
    preview = await adapter.preview(action, {"meta_description": "After"})
    action.update(
        {
            "before_snapshot": preview["before_snapshot"],
            "approved_patch": preview["proposed_patch"],
            "side_effect_confirmations": {
                "requires_explicit_confirmation": True
            },
        }
    )

    result = await adapter.execute(action)

    assert result["readback"] == {"meta_description": "After"}
    assert writes[0][1]["confirm"] is True
