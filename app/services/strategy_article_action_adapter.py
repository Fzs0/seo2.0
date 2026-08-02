"""Production article adapter for the unified Strategy Action lifecycle.

The adapter bridges the new Action state machine to the existing, guarded
``seo_strategy`` approval, article storage, publisher, and effect-observation
services.  It never generates content itself: Codex (or a human) submits the
complete, reviewable article patch to the Action preview endpoint.
"""
from __future__ import annotations

import json
import re
import hashlib
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.article_urls import is_content_openapi_site
from app.clients.publishers import (
    ImageUploadRequest,
    validate_content_openapi_markdown,
)
from app.services.article_generation_service import _qa, _slug
from app.services.article_service import save_article
from app.services.publish_service import publish_article
from app.services.shopify_connection_service import publisher_for_site_runtime
from app.services.strategy_effect_service import (
    cancel_unpublished_effect,
    ensure_effect,
    mark_effect_published,
)
from app.services.site_capability_service import get_site_capabilities
from app.services.strategy_action_service import SQLActionStore, compare_readback_fields
from app.services.site_media_service import resolve_site_media_uploader


ARTICLE_ACTIONS = frozenset({"new_article", "update_article"})
ARTICLE_FIELDS = frozenset(
    {
        "title",
        "body",
        "meta_title",
        "meta_description",
        "images",
        "image_alts",
        "cover_image",
    }
)


class StrategyArticleActionAdapter:
    """Execute approved article Actions through the existing safe publishers."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def preview(
        self,
        action: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        if action.get("action_type") not in ARTICLE_ACTIONS:
            return {
                "result": "blocked",
                "block_reason": "action_adapter_not_configured_for_action_type",
            }
        try:
            context = await _load_action_context(self.session, action)
            cover_image_id_required = _cover_image_id_required(action)
            proposed = canonical_article_patch(
                patch,
                cover_image_id_required=cover_image_id_required,
            )
            _validate_article_patch(
                proposed,
                keyword=str(
                    (context.get("strategy_decision") or {}).get("query")
                    or action.get("topic")
                    or ""
                ),
                internal_link_plan=(
                    (context.get("strategy_decision") or {}).get("internal_link_plan")
                    or []
                ),
                cover_image_id_required=cover_image_id_required,
                site=context.get("site") or {},
            )
            proposed = _capability_filtered_article_patch(action, proposed)
        except ValueError as error:
            return {"result": "blocked", "block_reason": str(error)}
        return {
            "result": "updated",
            "before_snapshot": _before_snapshot(context),
            "proposed_patch": proposed,
        }

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        if action.get("action_type") not in ARTICLE_ACTIONS:
            return {
                "result": "blocked",
                "block_reason": "action_adapter_not_configured_for_action_type",
            }
        if action.get("run_mode") != "approval_execution":
            return {
                "result": "blocked",
                "block_reason": "dry_run_actions_cannot_execute",
            }
        context = await _load_action_context(self.session, action)
        cover_image_id_required = _cover_image_id_required(action)
        patch = canonical_article_patch(
            dict(action.get("approved_patch") or action.get("proposed_patch") or {}),
            cover_image_id_required=cover_image_id_required,
        )
        _validate_article_patch(
            patch,
            keyword=str(
                (context.get("strategy_decision") or {}).get("query")
                or action.get("topic")
                or ""
            ),
            internal_link_plan=(
                (context.get("strategy_decision") or {}).get("internal_link_plan")
                or []
            ),
            cover_image_id_required=cover_image_id_required,
            site=context.get("site") or {},
        )

        execution_id = await _ensure_approved_execution(
            self.session,
            action=action,
            context=context,
        )
        await _claim_execution(
            self.session,
            execution_id=execution_id,
            action=action,
        )
        article = await _save_action_article(
            self.session,
            action=action,
            context=context,
            execution_id=execution_id,
            patch=patch,
        )
        article_id = str(article["id"])
        strategy = dict(context.get("strategy_decision") or {})
        effect = await ensure_effect(
            self.session,
            execution_task_id=execution_id,
            strategy_task_id=str(action["source_strategy_task_id"]),
            site_id=str(action["site_id"]),
            article_id=article_id,
            strategy=strategy,
        )
        await self.session.commit()

        update_remote_id = (
            str(context.get("post_external_id") or "")
            if action.get("action_type") == "update_article"
            else None
        )
        if action.get("action_type") == "update_article" and not update_remote_id:
            await cancel_unpublished_effect(
                self.session,
                execution_task_id=execution_id,
                reason="approved update target has no remote article ID",
            )
            raise ValueError("approved update target has no remote article ID")

        published = await publish_article(
            self.session,
            article_id=article_id,
            site_id=str(action["site_id"]),
            dry_run=False,
            actor="strategy_action",
            update_post_id=update_remote_id,
        )
        if not published.get("ok"):
            await cancel_unpublished_effect(
                self.session,
                execution_task_id=execution_id,
                reason=published.get("error") or "article publish failed",
            )
            await _finish_execution(
                self.session,
                execution_id=execution_id,
                status="blocked"
                if published.get("remote_outcome")
                in {"unknown_remote_state", "partially_applied", "identity_conflict"}
                else "failed",
                article_id=article_id,
                target_url=published.get("url"),
                error=published.get("error"),
            )
            await self.session.commit()
            return {
                "result": "blocked"
                if published.get("remote_outcome")
                in {"unknown_remote_state", "partially_applied", "identity_conflict"}
                else "failed",
                "remote_outcome": published.get("remote_outcome"),
                "submitted_patch": patch,
                "remote_response": published,
                "readback": {},
                "article_id": article_id,
                "execution_task_id": execution_id,
                "publish_task_id": published.get("task_id"),
                "effect_id": effect.get("id"),
            }

        target_url = str(published.get("url") or "").strip()
        await mark_effect_published(
            self.session,
            execution_task_id=execution_id,
            article_id=article_id,
            target_url=target_url,
            action=str(action["action_type"]),
            topic_cooldown_days=get_settings().strategy_topic_cooldown_days,
        )
        await _finish_execution(
            self.session,
            execution_id=execution_id,
            status="done",
            article_id=article_id,
            target_url=target_url,
            error=None,
        )
        remote = await _read_remote_article(
            self.session,
            site=context["site"],
            remote_id=str(published.get("post_id") or update_remote_id or ""),
        )
        readback = normalize_article_readback(remote or {}, site=context["site"])
        await self.session.commit()
        return {
            "result": (
                "already_applied"
                if published.get("idempotent") or published.get("reused")
                else "updated"
                if action.get("action_type") == "update_article"
                else "created"
            ),
            "submitted_patch": patch,
            "remote_response": published,
            "readback": readback,
            "read_only_fields": _article_read_only_fields(context["site"]),
            "article_id": article_id,
            "execution_task_id": execution_id,
            "publish_task_id": published.get("task_id"),
            "effect_id": effect.get("id"),
            "target_url": target_url,
        }

    async def recover(self, action: dict[str, Any]) -> dict[str, Any]:
        exact_remote_readback = False
        try:
            if (
                action.get("recovery_status")
                in {
                    "unknown_remote_state",
                    "partially_applied",
                    "confirmed_not_applied",
                }
                and await _has_confirmed_prewrite_failure(self.session, action)
            ):
                await _close_confirmed_prewrite_lineage(self.session, action)
                return {
                    "recovery_status": "confirmed_not_applied",
                    "submitted_patch": dict(
                        action.get("approved_patch")
                        or action.get("proposed_patch")
                        or {}
                    ),
                    "remote_response": {
                        "remote_outcome": "confirmed_not_applied",
                        "reconciled_without_remote_write": True,
                        "evidence": "persisted_local_prewrite_guard",
                    },
                    "readback": {},
                }
            context = await _load_action_context(self.session, action)
            execution_id = str(action.get("execution_task_id") or "")
            remote_id = (
                str(context.get("post_external_id") or "")
                if action.get("action_type") == "update_article"
                else ""
            )
            article_id = str(action.get("article_id") or "")
            if not remote_id and article_id:
                article = (
                    await self.session.execute(
                        text(
                            "SELECT published_post_id FROM seo_agent.articles "
                            "WHERE id=CAST(:id AS uuid)"
                        ),
                        {"id": article_id},
                    )
                ).mappings().first()
                remote_id = str((article or {}).get("published_post_id") or "")
            if not remote_id and execution_id:
                row = (
                    await self.session.execute(
                        text(
                            "SELECT article_id::text AS article_id "
                            "FROM seo_agent.tasks WHERE id=CAST(:id AS uuid)"
                        ),
                        {"id": execution_id},
                    )
                ).mappings().first()
                if row and row.get("article_id"):
                    article = (
                        await self.session.execute(
                            text(
                                "SELECT published_post_id FROM seo_agent.articles "
                                "WHERE id=CAST(:id AS uuid)"
                            ),
                            {"id": row["article_id"]},
                        )
                    ).mappings().first()
                    remote_id = str((article or {}).get("published_post_id") or "")
            if not remote_id:
                return {"recovery_status": "confirmed_absent"}
            remote = await _read_remote_article(
                self.session,
                site=context["site"],
                remote_id=remote_id,
            )
            if not remote:
                return {"recovery_status": "confirmed_absent"}
            readback = normalize_article_readback(remote, site=context["site"])
            proposed = canonical_article_patch(
                dict(action.get("approved_patch") or action.get("proposed_patch") or {}),
                cover_image_id_required=_cover_image_id_required(action),
            )
            read_only_fields = _article_read_only_fields(context["site"])
            differences = compare_readback_fields(
                proposed,
                proposed,
                readback,
                read_only_fields=set(read_only_fields),
                media_transport=_article_media_transport(action),
                canonical_hosts=set(
                    (action.get("capability_snapshot") or {}).get(
                        "canonical_hosts"
                    )
                    or ()
                ),
            )
            matches = bool(differences) and all(
                item.get("match") is True for item in differences
            )
            exact_remote_readback = matches
            before_snapshot = dict(action.get("before_snapshot") or {})
            before_differences = compare_readback_fields(
                before_snapshot,
                before_snapshot,
                readback,
                read_only_fields=set(read_only_fields),
                media_transport=_article_media_transport(action),
                canonical_hosts=set(
                    (action.get("capability_snapshot") or {}).get(
                        "canonical_hosts"
                    )
                    or ()
                ),
            )
            unchanged = bool(before_differences) and all(
                item.get("match") is True for item in before_differences
            )
            reconciliation: dict[str, Any] = {}
            if matches:
                approved_readback = {
                    field: readback.get(field) for field in proposed
                }
                reconciliation = await _reconcile_confirmed_article_execution(
                    self.session,
                    action=action,
                    context=context,
                    remote_id=remote_id,
                    readback=approved_readback,
                )
            return {
                "recovery_status": (
                    "confirmed_applied"
                    if matches
                    else "confirmed_not_applied"
                    if unchanged
                    else "partially_applied"
                ),
                "result": "updated"
                if action.get("action_type") == "update_article"
                else "created",
                "submitted_patch": proposed,
                "readback": readback,
                "read_only_fields": read_only_fields,
                "remote_response": {
                    "remote_id": remote_id,
                    "remote_outcome": (
                        "confirmed_applied"
                        if matches
                        else "confirmed_not_applied"
                        if unchanged
                        else "partially_applied"
                    ),
                    "reconciled_without_remote_write": matches,
                },
                **reconciliation,
            }
        except Exception as error:
            rollback = getattr(self.session, "rollback", None)
            if rollback is not None:
                await rollback()
            # Once the approved fields have matched exactly, any subsequent
            # failure is local reconciliation, not an unknown remote outcome.
            # Propagate it so the API and logs preserve the true root cause.
            if exact_remote_readback:
                raise
            return {
                "recovery_status": "unknown_remote_state",
                "recovery_error_code": "ARTICLE_RECOVERY_READBACK_FAILED",
                "recovery_error_type": type(error).__name__,
            }


async def get_article_generation_context(
    session: AsyncSession,
    *,
    action_id: str,
    preflight_token: str | None = None,
) -> dict[str, Any]:
    """Return evidence only after the formal-plan preflight has passed."""
    action_row = (
        await session.execute(
            text(
                "SELECT payload FROM seo_agent.tasks "
                "WHERE id=CAST(:id AS uuid) AND payload->>'kind'='strategy_action'"
            ),
            {"id": action_id},
        )
    ).mappings().first()
    if not action_row:
        raise ValueError("strategy action not found")
    action = dict(action_row["payload"] or {})
    _validate_preflight_token(action, preflight_token)
    await SQLActionStore(session).validate_lineage(action)
    await _validate_current_preflight_capability(session, action)
    return await _build_article_generation_context(
        session, action_id=action_id, action=action
    )


async def preflight_article_action(
    session: AsyncSession,
    *,
    action_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Validate lineage, capabilities, identity, readback, and media before generation."""
    store = SQLActionStore(session)
    action = await store.get(action_id, lock=True)
    if action.get("action_type") not in ARTICLE_ACTIONS:
        raise ValueError("preflight is available only for article actions")
    await store.validate_lineage(action, lock=True)
    if action.get("status") not in {"planned", "previewed"}:
        raise ValueError("article generation preflight requires a planned Action")
    receipts = dict(action.get("preflight_receipts") or {})
    prior = receipts.get(idempotency_key)
    if prior and prior == action.get("preflight_token"):
        _validate_preflight_token(action, prior)
        await _validate_current_preflight_capability(session, action)
        context = await _build_article_generation_context(
            session, action_id=action_id, action=action
        )
        return {
            "ready": True,
            "preflight_token": prior,
            "expires_at": action.get("preflight_expires_at"),
            "idempotency_replayed": True,
            "generation_context": context,
        }
    context = await _load_action_context(session, action)
    current_capability = await get_site_capabilities(
        session,
        str(action["site_id"]),
        expected_business_id=str(action["business_id"]),
    )
    if not current_capability:
        raise ValueError("current site capability snapshot is unavailable")
    stored_capability = action.get("capability_snapshot") or {}
    if (
        current_capability.get("capability_snapshot_hash")
        != stored_capability.get("capability_snapshot_hash")
    ):
        raise ValueError("site capability changed; re-plan before generation")
    permission = (current_capability.get("supported_actions") or {}).get(
        action["action_type"]
    )
    if permission not in {"approval_required", "allowed", "execute"}:
        raise ValueError("article action capability is no longer executable")
    if not context.get("market") or not context.get("language_code"):
        raise ValueError("target market and language must be configured")
    public_site = _public_site(context["site"])
    if not public_site.get("domain") and not public_site.get("base_url"):
        raise ValueError("public site identity is unavailable")
    if action.get("action_type") == "update_article" and not context.get(
        "post_external_id"
    ):
        raise ValueError("approved update target has no remote article ID")
    if not _image_patch_supported(action) or not _cover_patch_supported(action):
        raise ValueError(
            "strategy article requires both content-image upload and cover-image capability"
        )
    token = str(uuid4())
    expires_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    receipts[idempotency_key] = token
    action.update(
        {
            "preflight_status": "ready",
            "preflight_token": token,
            "preflight_expires_at": expires_at,
            "preflight_capability_snapshot_hash": current_capability.get(
                "capability_snapshot_hash"
            ),
            "preflight_plan_id": action.get("plan_id"),
            "preflight_strategy_task_id": action.get(
                "source_strategy_task_id"
            ),
            "preflight_receipts": receipts,
        }
    )
    await store.save(action)
    generation_context = await _build_article_generation_context(
        session, action_id=action_id, action=action
    )
    return {
        "ready": True,
        "preflight_token": token,
        "expires_at": expires_at,
        "generation_context": generation_context,
    }


async def upload_strategy_article_image(
    session: AsyncSession,
    *,
    action_id: str,
    preflight_token: str,
    idempotency_key: str,
    request: ImageUploadRequest,
    dry_run: bool,
) -> dict[str, Any]:
    """Upload media only for a currently valid, preflighted Strategy Action."""
    store = SQLActionStore(session)
    action = await store.get(action_id, lock=True)
    _validate_preflight_token(action, preflight_token)
    await store.validate_lineage(action)
    await _validate_current_preflight_capability(session, action)
    if action.get("run_mode") == "dry_run" and not dry_run:
        raise ValueError("dry-run Strategy Actions cannot upload remote media")
    if not _image_patch_supported(action) or not _cover_patch_supported(action):
        raise ValueError("strategy media upload capability is unavailable")
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "payload": request.payload(),
                "filename": request.filename,
                "alt_text": request.alt_text,
                "title": request.title,
                "caption": request.caption,
                "dry_run": dry_run,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    receipts = dict(action.get("media_upload_receipts") or {})
    receipt = receipts.get(idempotency_key)
    if receipt:
        if receipt.get("request_hash") != request_hash:
            raise ValueError("media upload idempotency key is bound to another request")
        if receipt.get("status") == "completed":
            return {**dict(receipt.get("response") or {}), "idempotency_replayed": True}
        raise ValueError(
            "media upload outcome is unresolved; verify remotely before retrying"
        )
    receipts[idempotency_key] = {
        "request_hash": request_hash,
        "status": "in_progress",
    }
    action["media_upload_receipts"] = receipts
    await store.save(action)
    row = (
        await session.execute(
            text(
                """
                SELECT id, business_id, site_key, name, site_type, domain, base_url,
                       api_base_url, status, api_config
                  FROM seo_agent.sites
                 WHERE id=CAST(:id AS uuid)
                """
            ),
            {"id": action["site_id"]},
        )
    ).mappings().first()
    try:
        if not row or row["status"] != "active":
            raise ValueError("strategy media target site is not active")
        media = await resolve_site_media_uploader(
            session,
            dict(row),
            dry_run=dry_run,
            require_active=not dry_run,
        )
    except Exception as error:
        action = await store.get(action_id, lock=True)
        receipts = dict(action.get("media_upload_receipts") or {})
        receipts[idempotency_key] = {
            "request_hash": request_hash,
            "status": "failed",
            "error_type": type(error).__name__,
        }
        action["media_upload_receipts"] = receipts
        await store.save(action)
        raise ValueError(str(error)) from error
    try:
        result = await media.publisher.upload_image(request)
    except Exception as error:
        action = await store.get(action_id, lock=True)
        receipts = dict(action.get("media_upload_receipts") or {})
        receipts[idempotency_key] = {
            "request_hash": request_hash,
            "status": "unknown_remote_state",
            "error_type": type(error).__name__,
        }
        action["media_upload_receipts"] = receipts
        await store.save(action)
        raise ValueError(
            "strategy image upload outcome is unknown; verify remotely before retrying"
        ) from error
    if not result.ok:
        action = await store.get(action_id, lock=True)
        receipts = dict(action.get("media_upload_receipts") or {})
        receipts[idempotency_key] = {
            "request_hash": request_hash,
            "status": "failed",
        }
        action["media_upload_receipts"] = receipts
        await store.save(action)
        raise ValueError(result.error or "strategy image upload failed")
    response = {
        "ok": True,
        "dry_run": result.dry_run,
        "site_id": str(action["site_id"]),
        "action_id": action_id,
        "media_host_site_id": str(media.media_host_site["id"]),
        "media_host_site_key": media.media_host_site.get("site_key"),
        "media_transport": media.transport,
        "image_id": result.image_id,
        "src": result.src,
        "raw": result.raw,
    }
    action = await store.get(action_id, lock=True)
    receipts = dict(action.get("media_upload_receipts") or {})
    receipts[idempotency_key] = {
        "request_hash": request_hash,
        "status": "completed",
        "response": response,
    }
    action["media_upload_receipts"] = receipts
    await store.save(action)
    return response


async def _build_article_generation_context(
    session: AsyncSession,
    *,
    action_id: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    if action.get("action_type") not in ARTICLE_ACTIONS:
        raise ValueError("generation context is available only for article actions")
    context = await _load_action_context(session, action)
    products = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, external_id, title, url, description,
                       image, images, meta_title, meta_description
                  FROM seo_agent.products
                 WHERE site_id=CAST(:site_id AS uuid) AND status='active'
                 ORDER BY updated_at DESC
                 LIMIT 20
                """
            ),
            {"site_id": action["site_id"]},
        )
    ).mappings().all()
    image_patch_supported = _image_patch_supported(action)
    cover_patch_supported = _cover_patch_supported(action)
    optional_patch_fields = (
        ["images", "image_alts"] if image_patch_supported else []
    )
    if cover_patch_supported:
        optional_patch_fields.append("cover_image")
    return {
        "action_id": action_id,
        "run_id": action.get("run_id"),
        "business_id": action.get("business_id"),
        "site_id": action.get("site_id"),
        "action_type": action.get("action_type"),
        "source_strategy_task_id": action.get("source_strategy_task_id"),
        "site": _public_site(context["site"]),
        "strategy_decision": context.get("strategy_decision") or {},
        "before_snapshot": _before_snapshot(context),
        "product_references": [dict(row) for row in products],
        "required_patch_fields": [
            "title",
            "body",
            "meta_title",
            "meta_description",
        ],
        "optional_patch_fields": optional_patch_fields,
        "forbidden_patch_fields": [
            "slug",
            "url",
            "canonical",
            "redirect",
            "status",
        ],
        "image_upload_endpoint": (
            f"/api/v1/strategy-actions/{action_id}/images/upload"
            if image_patch_supported or cover_patch_supported
            else None
        ),
        "media_transport": _article_media_transport(action),
        "accepted_media_inputs": (
            ["url", "file", "base64"]
            if image_patch_supported or cover_patch_supported
            else []
        ),
        "next_step": f"POST /api/v1/strategy-actions/{action_id}/preview",
    }


def _validate_preflight_token(
    action: dict[str, Any], preflight_token: str | None
) -> None:
    if not preflight_token or preflight_token != action.get("preflight_token"):
        raise ValueError("a valid strategy Action preflight token is required")
    expires_at = action.get("preflight_expires_at")
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError("strategy Action preflight token is invalid") from error
    if not expiry.tzinfo:
        expiry = expiry.replace(tzinfo=UTC)
    if expiry <= datetime.now(UTC):
        raise ValueError("strategy Action preflight token has expired")
    if action.get("preflight_plan_id") != action.get("plan_id"):
        raise ValueError("strategy plan changed after preflight")
    if action.get("preflight_strategy_task_id") != action.get(
        "source_strategy_task_id"
    ):
        raise ValueError("formal strategy changed after preflight")


async def _validate_current_preflight_capability(
    session: AsyncSession, action: dict[str, Any]
) -> None:
    current = await get_site_capabilities(
        session,
        str(action["site_id"]),
        expected_business_id=str(action["business_id"]),
    )
    if not current:
        raise ValueError("current site capability snapshot is unavailable")
    if current.get("capability_snapshot_hash") != action.get(
        "preflight_capability_snapshot_hash"
    ):
        raise ValueError("site capability changed after preflight")


async def _load_action_context(
    session: AsyncSession,
    action: dict[str, Any],
) -> dict[str, Any]:
    strategy_task_id = str(action.get("source_strategy_task_id") or "")
    if not strategy_task_id:
        raise ValueError("strategy action is not bound to a persisted seo_strategy")
    target_post_id: str | None = None
    try:
        target_post_id = str(UUID(str(action.get("target_asset_id") or "")))
    except (AttributeError, TypeError, ValueError):
        target_post_id = None
    row = (
        await session.execute(
            text(
                """
                SELECT strategy.id::text AS strategy_task_id,
                       strategy.status AS strategy_status,
                       strategy.decision AS strategy_decision,
                       strategy.keyword_id::text AS keyword_id,
                       post.id::text AS post_id,
                       strategy.article_id::text AS source_article_id,
                       site.id::text AS site_id, site.business_id, site.site_key,
                       site.name, site.site_type, site.domain, site.base_url,
                       site.api_base_url, site.api_config, site.status AS site_status,
                       site.strategy_enabled, site.market, site.language_code,
                       site.content_role,
                       post.external_id AS post_external_id, post.title AS post_title,
                       post.slug AS post_slug, post.url AS post_url,
                       post.content_md AS post_content_md,
                       post.content_html AS post_content_html,
                       post.meta_title AS post_meta_title,
                       post.meta_description AS post_meta_description,
                       post.cover_url AS post_cover_url,
                       post.raw AS post_raw
                  FROM seo_agent.tasks strategy
                  JOIN seo_agent.sites site ON site.id=strategy.site_id
                   LEFT JOIN seo_agent.posts post
                     ON post.id=COALESCE(
                          strategy.post_id, CAST(:target_post_id AS uuid)
                        )
                    AND post.site_id=strategy.site_id
                 WHERE strategy.id=CAST(:strategy_task_id AS uuid)
                   AND strategy.task_type='review'
                   AND strategy.payload->>'kind'='seo_strategy'
                """
            ),
            {
                "strategy_task_id": strategy_task_id,
                "target_post_id": target_post_id,
            },
        )
    ).mappings().first()
    if not row:
        raise ValueError("bound seo_strategy task not found")
    context = dict(row)
    if (
        action.get("action_type") == "update_article"
        and not context.get("post_external_id")
    ):
        context["post_external_id"] = (
            str(action.get("remote_object_id") or "").strip() or None
        )
    if (
        str(context["site_id"]) != str(action.get("site_id"))
        or str(context["business_id"]) != str(action.get("business_id"))
    ):
        raise ValueError("strategy action business/site scope does not match its source strategy")
    if context.get("site_status") != "active" or not context.get("strategy_enabled"):
        raise ValueError("target site is no longer active in strategy scope")
    context["site"] = {
        key: context.get(key)
        for key in (
            "site_id",
            "business_id",
            "site_key",
            "name",
            "site_type",
            "domain",
            "base_url",
            "api_base_url",
            "api_config",
            "site_status",
            "strategy_enabled",
            "market",
            "language_code",
            "content_role",
        )
    }
    context["site"]["id"] = context["site"].pop("site_id")
    context["site"]["status"] = context["site"].pop("site_status")
    return context


def canonical_article_patch(
    patch: dict[str, Any],
    *,
    cover_image_id_required: bool = True,
) -> dict[str, Any]:
    unsupported = set(patch) - ARTICLE_FIELDS
    if unsupported:
        raise ValueError(f"article patch contains forbidden fields: {sorted(unsupported)}")
    title = str(patch.get("title") or "").strip()
    body = str(patch.get("body") or "").strip()
    meta_title = str(patch.get("meta_title") or "").strip()
    meta_description = str(patch.get("meta_description") or "").strip()
    raw_cover = patch.get("cover_image")
    cover_image: dict[str, str] | None = None
    if raw_cover is not None:
        if not isinstance(raw_cover, dict):
            raise ValueError("cover_image must be an uploaded media object")
        cover_src = str(raw_cover.get("src") or raw_cover.get("url") or "").strip()
        cover_id = str(
            raw_cover.get("image_id") or raw_cover.get("id") or ""
        ).strip()
        cover_alt = str(raw_cover.get("alt") or "").strip()
        if not cover_src.startswith(("http://", "https://")):
            raise ValueError("cover_image src must be an absolute HTTP URL")
        if cover_image_id_required and not cover_id.isdigit():
            raise ValueError("cover_image image_id must be a numeric media ID")
        if not cover_alt:
            raise ValueError("cover_image alt is required")
        cover_image = {
            "src": cover_src,
            "alt": cover_alt,
        }
        if cover_id:
            cover_image["image_id"] = cover_id

    images: list[str] = []
    alts: dict[str, str] = {}
    for raw in patch.get("images") or []:
        if isinstance(raw, str):
            src = raw.strip()
            alt = ""
        elif isinstance(raw, dict):
            src = str(raw.get("src") or raw.get("url") or "").strip()
            alt = str(raw.get("alt") or "").strip()
        else:
            raise ValueError("article images must contain URL strings or objects")
        if src and src not in images:
            images.append(src)
        if src and alt:
            alts[src] = alt
    raw_alts = patch.get("image_alts") or {}
    if isinstance(raw_alts, dict):
        for src, alt in raw_alts.items():
            if str(src).strip() and str(alt).strip():
                alts[str(src).strip()] = str(alt).strip()
    elif raw_alts:
        raise ValueError("image_alts must be an object keyed by image URL")
    for src, alt in _extract_images(body).items():
        if src not in images:
            images.append(src)
        if alt:
            alts.setdefault(src, alt)
    canonical = {
        "title": title,
        "body": body,
        "meta_title": meta_title,
        "meta_description": meta_description,
        "images": images,
        "image_alts": alts,
    }
    if cover_image is not None:
        canonical["cover_image"] = cover_image
    return canonical


def _image_patch_supported(action: dict[str, Any]) -> bool:
    capability = action.get("capability_snapshot")
    if not isinstance(capability, dict):
        return False
    fields = set(
        ((capability.get("supported_fields") or {}).get("articles") or ())
    )
    images = (capability.get("connectors") or {}).get("images") or {}
    return (
        {"images", "image_alts"} <= fields
        and images.get("status") == "available"
        and images.get("write") is True
        and (images.get("upload") is True or images.get("ingest") is True)
    )


def _cover_patch_supported(action: dict[str, Any]) -> bool:
    capability = action.get("capability_snapshot")
    if not isinstance(capability, dict):
        return False
    fields = set(
        ((capability.get("supported_fields") or {}).get("articles") or ())
    )
    images = (capability.get("connectors") or {}).get("images") or {}
    return (
        "cover_image" in fields
        and images.get("status") == "available"
        and images.get("write") is True
        and (images.get("upload") is True or images.get("ingest") is True)
    )


def _article_media_ingest_supported(action: dict[str, Any]) -> bool:
    capability = action.get("capability_snapshot")
    if not isinstance(capability, dict):
        return False
    images = (capability.get("connectors") or {}).get("images") or {}
    return (
        images.get("status") == "available"
        and images.get("write") is True
        and images.get("ingest") is True
        and str(images.get("transport") or "").endswith("article_publish")
    )


def _article_media_transport(action: dict[str, Any]) -> str | None:
    capability = action.get("capability_snapshot")
    if not isinstance(capability, dict):
        return None
    images = (capability.get("connectors") or {}).get("images") or {}
    transport = str(images.get("transport") or "").strip()
    if transport:
        return transport
    return "direct_upload" if images.get("upload") is True else None


def _article_read_only_fields(site: dict[str, Any]) -> list[str]:
    # The self-hosted content API exposes one article title only.  Its public
    # template derives the document <title> from that value; a separate
    # meta-title cannot be written or independently read back.
    return ["meta_title"] if is_content_openapi_site(site) else []


async def _has_confirmed_prewrite_failure(
    session: AsyncSession, action: dict[str, Any]
) -> bool:
    """Recognize persisted local guards that failed before a remote call."""
    row = (
        await session.execute(
            text(
                """
                SELECT payload->>'raw_error' AS raw_error
                  FROM seo_agent.tasks
                 WHERE task_type='review'
                   AND payload->>'kind'='strategy_exception'
                   AND payload->>'action_id'=:action_id
                   AND payload->>'error_code'='ACTION_CONNECTOR_ERROR'
                 ORDER BY updated_at DESC
                 LIMIT 1
                """
            ),
            {"action_id": str(action.get("action_id") or "")},
        )
    ).mappings().first()
    return str((row or {}).get("raw_error") or "") in {
        "update publish must use the remote ID approved by the seo_strategy execution",
        "article published_post_id does not match the approved update target",
        "article is not linked to a human-approved seo_strategy execution",
        "new article strategy cannot publish as an update",
    }


async def _close_confirmed_prewrite_lineage(
    session: AsyncSession, action: dict[str, Any]
) -> None:
    execution = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status='failed',
                       error_message='confirmed local pre-write failure; remote writer was not called',
                       decision=decision || jsonb_build_object(
                         'remote_outcome','confirmed_not_applied'
                       ),
                       finished_at=COALESCE(finished_at, now()),
                       updated_at=now()
                 WHERE task_type IN ('new_article','update_article')
                   AND payload->>'strategy_action_id'=:action_id
                   AND status IN ('queued','running')
                 RETURNING id::text AS id
                """
            ),
            {"action_id": str(action.get("action_id") or "")},
        )
    ).mappings().first()
    if execution:
        await cancel_unpublished_effect(
            session,
            execution_task_id=str(execution["id"]),
            reason="confirmed local pre-write failure; remote writer was not called",
        )
    await session.commit()


def _cover_image_id_required(action: dict[str, Any]) -> bool:
    return not _article_media_ingest_supported(action)


def _capability_filtered_article_patch(
    action: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    filtered = dict(patch)
    if not _image_patch_supported(action):
        filtered.pop("images", None)
        filtered.pop("image_alts", None)
    if not _cover_patch_supported(action):
        filtered.pop("cover_image", None)
    return filtered


def _validate_article_patch(
    patch: dict[str, Any],
    *,
    keyword: str,
    internal_link_plan: list[dict[str, Any]],
    cover_image_id_required: bool = True,
    site: dict[str, Any] | None = None,
) -> None:
    if len(patch["title"]) < 8:
        raise ValueError("article title is too short")
    if len(patch["meta_title"]) < 8 or len(patch["meta_title"]) > 70:
        raise ValueError("meta_title must be between 8 and 70 characters")
    if not 120 <= len(patch["meta_description"]) <= 160:
        raise ValueError("meta_description must be between 120 and 160 characters")
    if len(patch["body"]) < 1200:
        raise ValueError("article body must contain at least 1200 characters")
    if len(re.findall(r"^#\s+.+$", patch["body"], re.M)) != 1:
        raise ValueError("article body must contain exactly one Markdown H1")
    if is_content_openapi_site(site or {}):
        validate_content_openapi_markdown(patch["body"])
    for src in patch["images"]:
        if src not in patch["body"]:
            raise ValueError(f"article image is not inserted in the body: {src}")
        if not patch["image_alts"].get(src):
            raise ValueError(f"article image is missing ALT text: {src}")
    cover_image = patch.get("cover_image")
    if cover_image and (
        not str(cover_image.get("src") or "").startswith(("http://", "https://"))
        or (
            cover_image_id_required
            and not str(cover_image.get("image_id") or "").isdigit()
        )
        or not str(cover_image.get("alt") or "").strip()
    ):
        raise ValueError(
            "cover_image requires an absolute src, alt, and connector-specific media identity"
        )
    checks = _qa(
        patch["body"],
        keyword,
        patch["meta_description"],
        patch["title"],
        internal_link_plan,
    )
    failed = [str(item["key"]) for item in checks if item.get("ok") is not True]
    if failed:
        raise ValueError(f"article QA failed: {', '.join(failed)}")


def _before_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    if not context.get("post_id"):
        return {
            "title": None,
            "body": None,
            "meta_title": None,
            "meta_description": None,
            "images": [],
            "image_alts": {},
            "cover_image": None,
        }
    body = str(context.get("post_content_md") or context.get("post_content_html") or "")
    return {
        "title": context.get("post_title"),
        "body": body,
        "meta_title": context.get("post_meta_title"),
        "meta_description": context.get("post_meta_description"),
        "images": list(_extract_images(body)),
        "image_alts": _extract_images(body),
        "cover_image": _context_cover_image(context),
    }


def _context_cover_image(context: dict[str, Any]) -> dict[str, str] | None:
    src = str(context.get("post_cover_url") or "").strip()
    raw = context.get("post_raw")
    raw = raw if isinstance(raw, dict) else {}
    image_id = str(raw.get("featured_media") or "").strip()
    if not src and not image_id:
        return None
    return {
        "src": src,
        "image_id": image_id,
        "alt": str(raw.get("featured_media_alt") or "").strip(),
    }


async def _ensure_approved_execution(
    session: AsyncSession,
    *,
    action: dict[str, Any],
    context: dict[str, Any],
) -> str:
    if action.get("run_mode") != "approval_execution":
        raise ValueError("dry-run Strategy Actions cannot create execution records")
    source_strategy_task_id = str(action["source_strategy_task_id"])
    approved_post_id = (
        str(context.get("post_id") or "").strip()
        if action.get("action_type") == "update_article"
        else ""
    )
    if action.get("action_type") == "update_article" and not approved_post_id:
        raise ValueError("approved update target has no local post identity")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"strategy-action-execution:{action['action_id']}"},
    )
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, post_id::text AS post_id
                  FROM seo_agent.tasks
                 WHERE task_type IN ('new_article','update_article')
                   AND payload->>'strategy_action_id'=:action_id
                 ORDER BY created_at ASC
                 LIMIT 1
                """
            ),
            {"action_id": str(action["action_id"])},
        )
    ).mappings().first()
    if existing:
        existing_post_id = str(existing.get("post_id") or "").strip()
        if approved_post_id and existing_post_id and existing_post_id != approved_post_id:
            raise ValueError("existing execution target no longer matches the approved article")
        if approved_post_id and not existing_post_id:
            await session.execute(
                text(
                    """
                    UPDATE seo_agent.tasks
                       SET post_id=COALESCE(post_id, CAST(:post_id AS uuid)),
                           updated_at=now()
                     WHERE id=CAST(:id AS uuid)
                    """
                ),
                {"id": existing["id"], "post_id": approved_post_id},
            )
        await session.commit()
        return str(existing["id"])

    strategy = (
        await session.execute(
            text(
                """
                SELECT t.id::text AS id, t.status, t.priority, t.score,
                       t.site_id::text AS site_id, t.keyword_id::text AS keyword_id,
                       t.post_id::text AS post_id, t.article_id::text AS article_id,
                       t.title, t.decision,
                       t.payload->>'business_id' AS business_id,
                       t.payload->>'plan_id' AS plan_id,
                       t.payload->>'strategy_run_id' AS strategy_run_id,
                       t.payload->>'schedule_class' AS schedule_class
                  FROM seo_agent.tasks t
                 WHERE t.id=CAST(:id AS uuid)
                   AND t.task_type='review'
                   AND t.payload->>'kind'='seo_strategy'
                 FOR UPDATE
                """
            ),
            {"id": source_strategy_task_id},
        )
    ).mappings().first()
    if not strategy:
        raise ValueError("bound seo_strategy task not found")
    if strategy["status"] not in {"queued", "done"}:
        raise ValueError("bound seo_strategy is no longer executable")
    if strategy["schedule_class"] != "execute_now":
        raise ValueError("only execute_now formal strategies can create Actions")
    if (
        str(strategy["business_id"]) != str(action["business_id"])
        or str(strategy["site_id"]) != str(action["site_id"])
        or str(strategy["plan_id"]) != str(action["plan_id"])
        or str(strategy["strategy_run_id"]) != str(action["run_id"])
    ):
        raise ValueError("Strategy Action lineage changed before execution")

    decision = dict(strategy.get("decision") or {})
    execution_type = str(action["action_type"])
    execution_id = str(uuid4())
    target_url = str(action.get("target_url") or "").strip() or None
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks
              (id, task_type, status, priority, score, site_id, keyword_id,
               post_id, article_id, title, target_url, payload, required_data,
               decision, logs, started_at)
            VALUES
              (CAST(:id AS uuid), :task_type, 'running', :priority, :score,
               CAST(:site_id AS uuid), CAST(:keyword_id AS uuid),
               CAST(:post_id AS uuid), CAST(:article_id AS uuid), :title,
               :target_url, CAST(:payload AS jsonb), :required_data,
               CAST(:execution_decision AS jsonb),
               jsonb_build_array(jsonb_build_object(
                 'stage','running',
                 'message','Unified Strategy Action claimed the execution.',
                 'at',now()
               )),
               now())
            """
        ),
        {
            "id": execution_id,
            "task_type": execution_type,
            "priority": strategy["priority"] or "P2",
            "score": strategy["score"] or 0,
            "site_id": strategy["site_id"],
            "keyword_id": strategy["keyword_id"],
            "post_id": approved_post_id or strategy["post_id"],
            "article_id": strategy["article_id"],
            "title": f"Strategy Action: {strategy['title']}",
            "target_url": target_url,
            "payload": json.dumps(
                {
                    "strategy_task_id": source_strategy_task_id,
                    "strategy_action_id": str(action["action_id"]),
                    "strategy_run_id": str(action["run_id"]),
                    "strategy": decision,
                    "scope_key": decision.get("scope_key"),
                    "strategy_fingerprint": decision.get("strategy_fingerprint"),
                    "evidence_fingerprint": decision.get("evidence_fingerprint"),
                },
                ensure_ascii=False,
            ),
            "required_data": ["approved_strategy_action"],
            "execution_decision": json.dumps(
                {
                    "source_strategy_id": source_strategy_task_id,
                    "strategy_action_id": str(action["action_id"]),
                    "strategy_type": execution_type,
                    "current_stage": "running",
                },
                ensure_ascii=False,
            ),
        },
    )
    decision.update(
        {
            "review_status": "approved",
            "approval_source": "strategy_action",
            "execution_task_id": execution_id,
            "execution_status": "running",
        }
    )
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET status='done', decision=CAST(:decision AS jsonb),
                   finished_at=COALESCE(finished_at, now()), updated_at=now()
             WHERE id=CAST(:id AS uuid)
            """
        ),
        {
            "id": source_strategy_task_id,
            "decision": json.dumps(decision, ensure_ascii=False),
        },
    )
    await session.commit()
    return execution_id


async def _claim_execution(
    session: AsyncSession,
    *,
    execution_id: str,
    action: dict[str, Any],
) -> None:
    row = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status=CASE WHEN status='queued' THEN 'running' ELSE status END,
                       payload=payload || jsonb_build_object(
                         'strategy_action_id', CAST(:action_id AS text),
                         'strategy_run_id', CAST(:run_id AS text)
                       ),
                       started_at=COALESCE(started_at, now()),
                       updated_at=now()
                 WHERE id=CAST(:id AS uuid)
                   AND task_type IN ('new_article','update_article')
                   AND status IN ('queued','running','done')
                RETURNING status
                """
            ),
            {
                "id": execution_id,
                "action_id": action["action_id"],
                "run_id": action["run_id"],
            },
        )
    ).first()
    if not row:
        raise ValueError("article execution task cannot be claimed")
    await session.commit()


async def _save_action_article(
    session: AsyncSession,
    *,
    action: dict[str, Any],
    context: dict[str, Any],
    execution_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    strategy = dict(context.get("strategy_decision") or {})
    keyword = str(strategy.get("query") or action.get("topic") or "")
    slug = (
        str(context.get("post_slug") or "").strip()
        if action.get("action_type") == "update_article"
        else _slug(patch["title"])
    )
    if not slug:
        raise ValueError("article slug cannot be resolved without changing an approved update URL")
    checks = _qa(
        patch["body"],
        keyword,
        patch["meta_description"],
        patch["title"],
        strategy.get("internal_link_plan") or [],
    )
    image_plan: list[dict[str, Any]] = []
    if patch.get("cover_image"):
        image_plan.append(
            {
                **dict(patch["cover_image"]),
                "role": "cover",
            }
        )
    image_plan.extend(
        {
            "src": src,
            "alt": patch["image_alts"].get(src),
            "role": "content",
        }
        for src in patch["images"]
    )
    saved = await save_article(
        session,
        {
            "task_id": execution_id,
            "site_id": action["site_id"],
            "keyword_id": context.get("keyword_id"),
            "title": patch["title"],
            "slug": slug,
            "target_url": context.get("post_url"),
            "status": "generated",
            "language_code": context.get("language_code"),
            "market": context.get("market"),
            "brief_md": str(strategy.get("recommended_action") or strategy.get("reason") or ""),
            "prompt_text": "Content supplied through an approved unified Strategy Action.",
            "content_md": patch["body"],
            "article_parts": {
                "title": patch["title"],
                "meta_title": patch["meta_title"],
                "meta_description": patch["meta_description"],
            },
            "meta_title": patch["meta_title"],
            "meta_description": patch["meta_description"],
            "primary_keyword": keyword,
            "internal_link_plan": strategy.get("internal_link_plan") or [],
            "image_plan": image_plan,
            "references_plan": (
                (strategy.get("execution_evidence") or {}).get("evidence_sources") or []
            ),
            "qa_checklist": checks,
            "qa_summary": {"ok": all(item.get("ok") is True for item in checks)},
            "generation_provider": action.get("generation_provider"),
            "generation_model": action.get("generation_model"),
            "raw_ai_response": {
                "strategy_action_id": action["action_id"],
                "generation_run_id": action.get("generation_run_id"),
                "generation_mode": action.get("generation_mode"),
            },
        },
    )
    if saved.get("id"):
        return saved
    row = (
        await session.execute(
            text(
                "SELECT id::text AS id FROM seo_agent.articles "
                "WHERE task_id=CAST(:task_id AS uuid)"
            ),
            {"task_id": execution_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("article draft was not persisted")
    return dict(row)


async def _finish_execution(
    session: AsyncSession,
    *,
    execution_id: str,
    status: str,
    article_id: str,
    target_url: str | None,
    error: str | None,
) -> None:
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET status=CAST(:status AS text),
                   article_id=CAST(:article_id AS uuid),
                   target_url=COALESCE(NULLIF(:target_url,''), target_url),
                   error_message=:error,
                   finished_at=CASE WHEN :status IN ('done','failed','blocked')
                                    THEN now() ELSE finished_at END,
                   updated_at=now()
             WHERE id=CAST(:id AS uuid)
            """
        ),
        {
            "id": execution_id,
            "status": status,
            "article_id": article_id,
            "target_url": target_url or "",
            "error": error,
        },
    )


async def _reconcile_confirmed_article_execution(
    session: AsyncSession,
    *,
    action: dict[str, Any],
    context: dict[str, Any],
    remote_id: str,
    readback: dict[str, Any],
) -> dict[str, Any]:
    """Repair local lineage after exact readback, without calling a remote writer."""
    article_id = str(action.get("article_id") or "")
    execution_id = str(action.get("execution_task_id") or "")
    publish_task_id = str(action.get("publish_task_id") or "")
    if not article_id or not execution_id:
        source_strategy_task_id = str(action.get("source_strategy_task_id") or "")
        action_type = str(action.get("action_type") or "")
        if not source_strategy_task_id or action_type not in ARTICLE_ACTIONS:
            raise ValueError("confirmed remote write is missing its local article lineage")
        lineage_rows = (
            await session.execute(
                text(
                    """
                    SELECT execution.id::text AS execution_task_id,
                           article.id::text AS article_id,
                           publish.id::text AS publish_task_id
                      FROM seo_agent.tasks execution
                      JOIN seo_agent.articles article
                        ON article.task_id=execution.id
                       AND article.site_id=execution.site_id
                      LEFT JOIN LATERAL (
                        SELECT candidate.id
                          FROM seo_agent.tasks candidate
                         WHERE candidate.task_type='publish'
                           AND candidate.site_id=execution.site_id
                           AND candidate.article_id=article.id
                           AND candidate.payload->>'execution_task_id'=execution.id::text
                         ORDER BY candidate.created_at DESC
                         LIMIT 1
                      ) publish ON TRUE
                     WHERE execution.site_id=CAST(:site_id AS uuid)
                       AND execution.task_type=:action_type
                       AND execution.payload->>'strategy_task_id'=:source_strategy_task_id
                       AND (:execution_task_id='' OR execution.id=CAST(:execution_task_id AS uuid))
                       AND (:article_id='' OR article.id=CAST(:article_id AS uuid))
                     ORDER BY execution.created_at DESC
                     LIMIT 2
                    """
                ),
                {
                    "site_id": action["site_id"],
                    "action_type": action_type,
                    "source_strategy_task_id": source_strategy_task_id,
                    "execution_task_id": execution_id,
                    "article_id": article_id,
                },
            )
        ).mappings().all()
        if len(lineage_rows) != 1:
            raise ValueError(
                "confirmed remote write local article lineage is missing or ambiguous"
            )
        lineage = lineage_rows[0]
        execution_id = str(lineage.get("execution_task_id") or "")
        article_id = str(lineage.get("article_id") or "")
        publish_task_id = str(
            publish_task_id or lineage.get("publish_task_id") or ""
        )
    if article_id and not execution_id:
        article = (
            await session.execute(
                text(
                    "SELECT task_id::text AS task_id FROM seo_agent.articles "
                    "WHERE id=CAST(:article_id AS uuid) AND site_id=CAST(:site_id AS uuid)"
                ),
                {"article_id": article_id, "site_id": action["site_id"]},
            )
        ).mappings().first()
        execution_id = str((article or {}).get("task_id") or "")
    if not article_id or not execution_id:
        raise ValueError("confirmed remote write is missing its local article lineage")
    if not publish_task_id:
        publish_task = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id
                      FROM seo_agent.tasks
                     WHERE task_type='publish'
                       AND article_id=CAST(:article_id AS uuid)
                       AND site_id=CAST(:site_id AS uuid)
                     ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"article_id": article_id, "site_id": action["site_id"]},
            )
        ).mappings().first()
        publish_task_id = str((publish_task or {}).get("id") or "")
    if not publish_task_id:
        raise ValueError("confirmed remote write is missing its local publish task")

    target_url = str(
        action.get("target_url")
        or context.get("post_url")
        or ""
    ).strip()
    if not target_url.startswith(("https://", "http://")):
        raise ValueError("confirmed remote write is missing its public target URL")

    article = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.articles
                   SET published_post_id=:remote_id,
                       published_url=:target_url,
                       published_at=COALESCE(published_at, :published_at),
                       status='published',
                       updated_at=now()
                 WHERE id=CAST(:article_id AS uuid)
                   AND site_id=CAST(:site_id AS uuid)
                RETURNING id::text AS id
                """
            ),
            {
                "article_id": article_id,
                "site_id": action["site_id"],
                "remote_id": remote_id,
                "target_url": target_url,
                "published_at": datetime.now(UTC),
            },
        )
    ).mappings().first()
    if not article:
        raise ValueError("confirmed remote write article scope no longer matches")

    publish_task = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status='done',
                       target_url=:target_url,
                       error_message=NULL,
                       payload=COALESCE(payload, '{}'::jsonb) || CAST(:payload AS jsonb),
                       decision=COALESCE(decision, '{}'::jsonb) || CAST(:decision AS jsonb),
                       finished_at=now(),
                       updated_at=now()
                 WHERE id=CAST(:publish_task_id AS uuid)
                   AND task_type='publish'
                   AND article_id=CAST(:article_id AS uuid)
                   AND site_id=CAST(:site_id AS uuid)
                RETURNING id::text AS id
                """
            ),
            {
                "publish_task_id": publish_task_id,
                "article_id": article_id,
                "site_id": action["site_id"],
                "target_url": target_url,
                "payload": json.dumps(
                    {
                        "remote_outcome": "confirmed_applied",
                        "reconciled_without_remote_write": True,
                        "remote_id": remote_id,
                    },
                    ensure_ascii=False,
                ),
                "decision": json.dumps(
                    {
                        "ok": True,
                        "remote_outcome": "confirmed_applied",
                        "reconciliation": "exact_remote_readback",
                    },
                    ensure_ascii=False,
                ),
            },
        )
    ).mappings().first()
    if not publish_task:
        raise ValueError("confirmed remote write publish task scope no longer matches")

    await _finish_execution(
        session,
        execution_id=execution_id,
        status="done",
        article_id=article_id,
        target_url=target_url,
        error=None,
    )
    strategy = dict(context.get("strategy_decision") or {})
    effect = await ensure_effect(
        session,
        execution_task_id=execution_id,
        strategy_task_id=str(action["source_strategy_task_id"]),
        site_id=str(action["site_id"]),
        article_id=article_id,
        strategy=strategy,
    )
    expected_effect_id = str(action.get("effect_id") or "")
    if expected_effect_id and str(effect.get("id") or "") != expected_effect_id:
        raise ValueError("confirmed remote write effect lineage no longer matches")
    await mark_effect_published(
        session,
        execution_task_id=execution_id,
        article_id=article_id,
        target_url=target_url,
        action=str(action["action_type"]),
        topic_cooldown_days=get_settings().strategy_topic_cooldown_days,
    )
    await session.commit()
    return {
        "article_id": article_id,
        "execution_task_id": execution_id,
        "publish_task_id": publish_task_id,
        "effect_id": str(effect["id"]),
        "target_url": target_url,
        "readback_verified_at": datetime.now(UTC).isoformat(),
        "readback_fields": sorted(readback),
    }


async def _read_remote_article(
    session: AsyncSession,
    *,
    site: dict[str, Any],
    remote_id: str,
) -> dict[str, Any] | None:
    if not remote_id:
        return None
    publisher = await publisher_for_site_runtime(
        session,
        site,
        dry_run=False,
        require_active=True,
    )
    return await publisher.get_article(remote_id)


def normalize_remote_article(remote: dict[str, Any]) -> dict[str, Any]:
    title = _nested_text(remote.get("title"))
    body = _nested_text(
        remote.get("body")
        or remote.get("content")
        or remote.get("content_html")
        or remote.get("content_md")
    )
    meta = remote.get("meta") if isinstance(remote.get("meta"), dict) else {}
    metafields = remote.get("metafields")
    if isinstance(metafields, list):
        field_map = {
            str(item.get("key") or ""): item.get("value")
            for item in metafields
            if isinstance(item, dict)
        }
    elif isinstance(metafields, dict):
        field_map = {
            str(key): (
                value.get("value") if isinstance(value, dict) else value
            )
            for key, value in metafields.items()
        }
    else:
        field_map = {}
    meta_title = _nested_text(
        remote.get("meta_title")
        or remote.get("seo_title")
        or remote.get("titleTag")
        or field_map.get("title_tag")
        or meta.get("rank_math_title")
        or meta.get("_yoast_wpseo_title")
    )
    meta_description = _nested_text(
        remote.get("meta_description")
        or remote.get("meta_descript")
        or remote.get("descript")
        or remote.get("summary")
        or remote.get("excerpt")
        or remote.get("descriptionTag")
        or field_map.get("description_tag")
        or meta.get("rank_math_description")
        or meta.get("_yoast_wpseo_metadesc")
    )
    images = _extract_images(body)
    cover_image = _extract_cover_image(remote)
    return {
        "title": title,
        "body": body,
        "meta_title": meta_title,
        "meta_description": meta_description,
        "images": list(images),
        "image_alts": images,
        "cover_image": cover_image,
    }


def normalize_article_readback(
    remote: dict[str, Any], *, site: dict[str, Any]
) -> dict[str, Any]:
    """Apply connector-specific derived fields to the common readback shape."""
    readback = normalize_remote_article(remote)
    if (
        is_content_openapi_site(site)
        and not readback.get("meta_title")
        and readback.get("title")
    ):
        # This API has no separate meta-title field.  Its public template builds
        # the document title from the article title, so that title is the only
        # remotely writable and verifiable SEO-title value.
        readback["meta_title"] = readback["title"]
    return readback


def _extract_cover_image(remote: dict[str, Any]) -> dict[str, str] | None:
    media_id = str(
        remote.get("featured_media")
        or remote.get("image_cover_id")
        or remote.get("cover_image_id")
        or remote.get("image_id")
        or ""
    ).strip()
    src = str(
        remote.get("image_cover_url")
        or remote.get("cover_url")
        or remote.get("src")
        or ""
    ).strip()
    alt = str(
        remote.get("image_cover_alt")
        or remote.get("cover_alt")
        or remote.get("image_alt")
        or ""
    ).strip()
    embedded = remote.get("_embedded")
    embedded = embedded if isinstance(embedded, dict) else {}
    featured = embedded.get("wp:featuredmedia")
    media = featured[0] if isinstance(featured, list) and featured else {}
    media = media if isinstance(media, dict) else {}
    media_id = str(media.get("id") or media_id).strip()
    src = str(media.get("source_url") or media.get("url") or src).strip()
    alt = str(media.get("alt_text") or media.get("alt") or alt).strip()
    if not media_id and not src:
        return None
    return {
        "image_id": media_id,
        "src": _normalize_url(src),
        "alt": re.sub(r"\s+", " ", unescape(alt)).strip(),
    }


def _extract_images(body: str) -> dict[str, str]:
    images: dict[str, str] = {}
    for alt, src in re.findall(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)", body or ""):
        images[_normalize_url(src)] = alt.strip()
    for attrs in re.findall(r"<img\b([^>]*)>", body or "", re.I):
        src_match = re.search(r"\bsrc=[\"']([^\"']+)[\"']", attrs, re.I)
        if not src_match:
            continue
        alt_match = re.search(r"\balt=[\"']([^\"']*)[\"']", attrs, re.I)
        images[_normalize_url(src_match.group(1))] = unescape(
            alt_match.group(1) if alt_match else ""
        ).strip()
    return images


def _normalize_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        split = urlsplit(raw)
        return urlunsplit(
            (
                split.scheme.casefold(),
                split.netloc.casefold(),
                split.path,
                split.query,
                "",
            )
        )
    except ValueError:
        return raw


def _nested_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("raw") or value.get("rendered") or value.get("value") or ""
    return str(value or "").strip()


def _public_site(site: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in site.items()
        if key not in {"api_config"}
    }


__all__ = [
    "ARTICLE_ACTIONS",
    "ARTICLE_FIELDS",
    "StrategyArticleActionAdapter",
    "canonical_article_patch",
    "get_article_generation_context",
    "preflight_article_action",
    "upload_strategy_article_image",
    "normalize_remote_article",
]
