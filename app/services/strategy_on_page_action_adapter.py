"""Platform adapters for controlled on-page SEO Strategy Actions."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.custom_connector_service import (
    execute_oemapps_seo_update,
    preview_oemapps_seo_update,
)
from app.services.oemapps_collection_service import (
    execute_collection_seo_update,
    preview_collection_seo_update,
)
from app.services.oemapps_home_seo_service import (
    execute_home_seo_update,
    preview_home_seo_update,
)
from app.services.shopify_product_service import (
    read_shopify_product_seo_remote,
    reconcile_shopify_product_seo_recovery,
    update_shopify_product_seo,
)


OEMAPPS_ON_PAGE_ACTIONS = frozenset(
    {"homepage_seo", "product_seo", "category_seo", "product_image_alt"}
)


class OEMAppsOnPageActionAdapter:
    """Bridge OEMApps guarded SEO writers into the unified Action lifecycle."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def preview(
        self, action: dict[str, Any], patch: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            context = await _load_target_context(self.session, action)
            proposed = _validated_patch(action, patch)
            remote = await _oemapps_preview(
                self.session, action=action, context=context, patch=proposed
            )
        except (TypeError, ValueError) as error:
            return {"result": "blocked", "block_reason": str(error)}
        return {
            "result": "updated",
            "before_snapshot": {
                **dict(
                    remote.get("current_public")
                    or remote.get("current")
                    or {}
                ),
                "_remote_snapshot_hash": remote["expected_snapshot_hash"],
                "_remote_object_id": context["remote_object_id"],
                "_connector_id": context["connector_id"],
            },
            "proposed_patch": proposed,
        }

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        context = await _load_target_context(self.session, action)
        patch = _validated_patch(
            action,
            dict(action.get("approved_patch") or action.get("proposed_patch") or {}),
        )
        before = dict(action.get("before_snapshot") or {})
        expected_hash = str(before.get("_remote_snapshot_hash") or "")
        if not expected_hash:
            return {"result": "blocked", "block_reason": "preview_snapshot_missing"}
        confirmations = dict(action.get("side_effect_confirmations") or {})
        action_type = str(action["action_type"])
        if action_type in {"product_seo", "product_image_alt"}:
            response = await execute_oemapps_seo_update(
                self.session,
                context["connector_id"],
                context["remote_object_id"],
                patch,
                expected_snapshot_hash=expected_hash,
                confirm_variant_recreation=bool(
                    confirmations.get("requires_variant_confirmation")
                ),
            )
        elif action_type == "category_seo":
            response = await execute_collection_seo_update(
                self.session,
                context["connector_id"],
                context["remote_object_id"],
                patch,
                expected_snapshot_hash=expected_hash,
                confirm_membership_top_reset=bool(
                    confirmations.get("requires_membership_confirmation")
                ),
            )
        elif action_type == "homepage_seo":
            response = await execute_home_seo_update(
                self.session,
                context["connector_id"],
                patch,
                expected_snapshot_hash=expected_hash,
                confirm=bool(
                    confirmations.get("requires_explicit_confirmation")
                ),
            )
        else:
            return {
                "result": "blocked",
                "block_reason": "action_adapter_not_configured_for_action_type",
            }
        readback = await _oemapps_preview(
            self.session, action=action, context=context, patch={}
        )
        semantic = "already_applied" if response.get("no_op") else "updated"
        if not response.get("ok", False):
            semantic = "readback_mismatch"
        return {
            "result": semantic,
            "submitted_patch": patch,
            "remote_response": response,
            "readback": {
                key: (
                    readback.get("current_public")
                    or readback.get("current")
                    or {}
                ).get(key)
                for key in patch
            },
            "target_url": context.get("target_url"),
        }

    async def recover(self, action: dict[str, Any]) -> dict[str, Any]:
        try:
            context = await _load_target_context(self.session, action)
            remote = await _oemapps_preview(
                self.session, action=action, context=context, patch={}
            )
        except Exception:
            return {"recovery_status": "unknown_remote_state"}
        return _recovery_result(
            action,
            dict(
                remote.get("current_public")
                or remote.get("current")
                or {}
            ),
        )


class ShopifyProductSeoActionAdapter:
    """Bridge the Shopify product SEO writer into the same Action lifecycle."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def preview(
        self, action: dict[str, Any], patch: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            context = await _load_target_context(self.session, action)
            proposed = _validated_patch(action, patch)
            complete = {
                "meta_title": proposed.get(
                    "meta_title", context.get("meta_title") or ""
                ),
                "meta_description": proposed.get(
                    "meta_description", context.get("meta_description") or ""
                ),
            }
            result = await update_shopify_product_seo(
                self.session,
                site_id=str(action["site_id"]),
                product_id=int(context["target_asset_id"]),
                expected_updated_at=str(context.get("source_updated_at") or ""),
                meta_title=complete["meta_title"],
                meta_description=complete["meta_description"],
                actor="strategy_action",
                dry_run=True,
            )
        except (TypeError, ValueError) as error:
            return {"result": "blocked", "block_reason": str(error)}
        return {
            "result": "updated",
            "before_snapshot": {
                **dict(result["before"]),
                "_preview_token": result["preview_token"],
                "_remote_object_id": context["remote_object_id"],
            },
            "proposed_patch": proposed,
        }

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        context = await _load_target_context(self.session, action)
        patch = _validated_patch(
            action,
            dict(action.get("approved_patch") or action.get("proposed_patch") or {}),
        )
        before = dict(action.get("before_snapshot") or {})
        complete = {
            "meta_title": patch.get("meta_title", before.get("meta_title") or ""),
            "meta_description": patch.get(
                "meta_description", before.get("meta_description") or ""
            ),
        }
        response = await update_shopify_product_seo(
            self.session,
            site_id=str(action["site_id"]),
            product_id=int(context["target_asset_id"]),
            expected_updated_at=str(before.get("source_updated_at") or ""),
            meta_title=complete["meta_title"],
            meta_description=complete["meta_description"],
            actor="strategy_action",
            dry_run=False,
            preview_token=str(before.get("_preview_token") or ""),
            request_id=str(action["action_id"]),
        )
        readback = await read_shopify_product_seo_remote(
            self.session,
            site_id=str(action["site_id"]),
            product_id=int(context["target_asset_id"]),
            expected_remote_object_id=context["remote_object_id"],
        )
        return {
            "result": "already_applied" if response.get("no_op") else "updated",
            "submitted_patch": patch,
            "remote_response": response,
            "readback": {key: readback.get(key) for key in patch},
            "target_url": context.get("target_url"),
        }

    async def recover(self, action: dict[str, Any]) -> dict[str, Any]:
        try:
            context = await _load_target_context(self.session, action)
            readback = await read_shopify_product_seo_remote(
                self.session,
                site_id=str(action["site_id"]),
                product_id=int(context["target_asset_id"]),
                expected_remote_object_id=context["remote_object_id"],
            )
        except Exception:
            return {"recovery_status": "unknown_remote_state"}
        result = _recovery_result(action, readback)
        if result.get("recovery_status") == "confirmed_applied":
            await reconcile_shopify_product_seo_recovery(
                self.session,
                site_id=str(action["site_id"]),
                product_id=int(context["target_asset_id"]),
                expected_remote_object_id=str(context["remote_object_id"]),
                request_id=str(action["action_id"]),
                readback=readback,
            )
        return result


async def _load_target_context(
    session: AsyncSession, action: dict[str, Any]
) -> dict[str, Any]:
    site_id = str(action.get("site_id") or "")
    target_asset_id = str(action.get("target_asset_id") or "")
    action_type = str(action.get("action_type") or "")
    connector_type = str(action.get("connector_type") or "").casefold()
    if not site_id or not target_asset_id:
        raise ValueError("on-page action target identity is incomplete")
    if connector_type == "oemapps":
        if action_type in {"product_seo", "product_image_alt"}:
            query = """
                SELECT id::text AS target_asset_id, external_id AS remote_object_id,
                       site_id::text, source_connector_id::text AS connector_id, url AS target_url,
                       meta_title, meta_description, meta_keywords, images, updated_at AS source_updated_at
                  FROM seo_agent.products
                 WHERE id=:target_asset_id AND site_id=CAST(:site_id AS uuid)
                   AND source_connector_id IS NOT NULL
            """
        elif action_type == "category_seo":
            query = """
                SELECT id::text AS target_asset_id, external_id AS remote_object_id,
                       site_id::text, source_connector_id::text AS connector_id, url AS target_url,
                       meta_title, meta_description, meta_keywords, updated_at AS source_updated_at
                  FROM seo_agent.product_collections
                 WHERE id=:target_asset_id AND site_id=CAST(:site_id AS uuid)
            """
        elif action_type == "homepage_seo":
            query = """
                SELECT h.id::text AS target_asset_id, h.site_id::text,
                       h.source_connector_id::text AS connector_id,
                       h.site_id::text AS remote_object_id,
                       COALESCE(s.base_url, s.domain) AS target_url,
                       h.meta_title, h.meta_description, h.meta_keywords,
                       h.updated_at AS source_updated_at
                  FROM seo_agent.site_home_seo h
                  JOIN seo_agent.sites s ON s.id=h.site_id
                 WHERE h.id=:target_asset_id
                   AND h.site_id=CAST(:site_id AS uuid)
            """
        else:
            raise ValueError("OEMApps action type is not registered")
    elif connector_type in {"shopify", "shopify_admin"} and action_type == "product_seo":
        query = """
            SELECT id::text AS target_asset_id, external_id AS remote_object_id,
                   site_id::text, NULL::text AS connector_id, url AS target_url,
                   meta_title, meta_description, meta_keywords, source_updated_at
              FROM seo_agent.products
             WHERE id=:target_asset_id AND site_id=CAST(:site_id AS uuid)
               AND source='shopify_admin_graphql'
        """
    else:
        raise ValueError("platform/action adapter is not registered")
    row = (
        await session.execute(
            text(query),
            {"target_asset_id": int(target_asset_id), "site_id": site_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("on-page target is missing or belongs to another site")
    context = {key: value for key, value in dict(row).items()}
    for field in ("remote_object_id", "connector_id", "target_url"):
        approved = action.get(field)
        actual = context.get(field)
        if approved and str(approved) != str(actual):
            raise ValueError(f"on-page target {field} does not match current data")
    return context


def _validated_patch(
    action: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    if not patch:
        raise ValueError("on-page action patch cannot be empty")
    expected = set(action.get("expected_fields") or ())
    if not expected or any(key not in expected for key in patch):
        raise ValueError("on-page patch exceeds the run-local expected fields")
    return dict(patch)


async def _oemapps_preview(
    session: AsyncSession,
    *,
    action: dict[str, Any],
    context: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    action_type = str(action["action_type"])
    if action_type in {"product_seo", "product_image_alt"}:
        return await preview_oemapps_seo_update(
            session, context["connector_id"], context["remote_object_id"], patch
        )
    if action_type == "category_seo":
        return await preview_collection_seo_update(
            session, context["connector_id"], context["remote_object_id"], patch
        )
    if action_type == "homepage_seo":
        return await preview_home_seo_update(
            session, context["connector_id"], patch
        )
    raise ValueError("OEMApps action type is not registered")


def _recovery_result(
    action: dict[str, Any], readback: dict[str, Any]
) -> dict[str, Any]:
    patch = dict(action.get("approved_patch") or action.get("proposed_patch") or {})
    before = dict(action.get("before_snapshot") or {})
    if patch and all(readback.get(key) == value for key, value in patch.items()):
        return {"recovery_status": "confirmed_applied", "readback": readback}
    if patch and all(readback.get(key) == before.get(key) for key in patch):
        return {"recovery_status": "confirmed_not_applied", "readback": readback}
    return {"recovery_status": "partially_applied", "readback": readback}


__all__ = [
    "OEMAPPS_ON_PAGE_ACTIONS",
    "OEMAppsOnPageActionAdapter",
    "ShopifyProductSeoActionAdapter",
]
