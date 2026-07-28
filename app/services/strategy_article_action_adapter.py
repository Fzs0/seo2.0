"""Production article adapter for the unified Strategy Action lifecycle.

The adapter bridges the new Action state machine to the existing, guarded
``seo_strategy`` approval, article storage, publisher, and effect-observation
services.  It never generates content itself: Codex (or a human) submits the
complete, reviewable article patch to the Action preview endpoint.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.article_generation_service import _qa, _slug
from app.services.article_service import save_article
from app.services.publish_service import publish_article
from app.services.shopify_connection_service import publisher_for_site_runtime
from app.services.strategy_effect_service import (
    cancel_unpublished_effect,
    ensure_effect,
    mark_effect_published,
)
from app.services.strategy_service import review_strategy


ARTICLE_ACTIONS = frozenset({"new_article", "update_article"})
ARTICLE_FIELDS = frozenset(
    {"title", "body", "meta_title", "meta_description", "images", "image_alts"}
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
            proposed = canonical_article_patch(patch)
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
        context = await _load_action_context(self.session, action)
        patch = canonical_article_patch(
            dict(action.get("approved_patch") or action.get("proposed_patch") or {})
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
        readback = normalize_remote_article(remote or {})
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
            "article_id": article_id,
            "execution_task_id": execution_id,
            "publish_task_id": published.get("task_id"),
            "effect_id": effect.get("id"),
            "target_url": target_url,
        }

    async def recover(self, action: dict[str, Any]) -> dict[str, Any]:
        try:
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
            readback = normalize_remote_article(remote)
            proposed = canonical_article_patch(
                dict(action.get("approved_patch") or action.get("proposed_patch") or {})
            )
            matches = all(
                _semantic_value(field, proposed.get(field))
                == _semantic_value(field, readback.get(field))
                for field in proposed
            )
            reconciliation: dict[str, Any] = {}
            if matches:
                reconciliation = await _reconcile_confirmed_article_execution(
                    self.session,
                    action=action,
                    context=context,
                    remote_id=remote_id,
                    readback=readback,
                )
            return {
                "recovery_status": "confirmed_applied"
                if matches
                else "partially_applied",
                "result": "updated"
                if action.get("action_type") == "update_article"
                else "created",
                "submitted_patch": proposed,
                "readback": readback,
                "remote_response": {
                    "remote_id": remote_id,
                    "remote_outcome": (
                        "confirmed_applied" if matches else "partially_applied"
                    ),
                    "reconciled_without_remote_write": matches,
                },
                **reconciliation,
            }
        except Exception:
            rollback = getattr(self.session, "rollback", None)
            if rollback is not None:
                await rollback()
            return {"recovery_status": "unknown_remote_state"}


async def get_article_generation_context(
    session: AsyncSession,
    *,
    action_id: str,
) -> dict[str, Any]:
    """Return read-only evidence required for Codex to author an article patch."""
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
        "optional_patch_fields": (
            ["images", "image_alts"] if image_patch_supported else []
        ),
        "forbidden_patch_fields": [
            "slug",
            "url",
            "canonical",
            "redirect",
            "status",
        ],
        "image_upload_endpoint": (
            f"/api/v1/sites/{action['site_id']}/images/upload"
            if image_patch_supported
            else None
        ),
        "next_step": f"POST /api/v1/strategy-actions/{action_id}/preview",
    }


async def _load_action_context(
    session: AsyncSession,
    action: dict[str, Any],
) -> dict[str, Any]:
    strategy_task_id = str(action.get("source_strategy_task_id") or "")
    if not strategy_task_id:
        raise ValueError("strategy action is not bound to a persisted seo_strategy")
    row = (
        await session.execute(
            text(
                """
                SELECT strategy.id::text AS strategy_task_id,
                       strategy.status AS strategy_status,
                       strategy.decision AS strategy_decision,
                       strategy.keyword_id::text AS keyword_id,
                       strategy.post_id::text AS post_id,
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
                       post.meta_description AS post_meta_description
                  FROM seo_agent.tasks strategy
                  JOIN seo_agent.sites site ON site.id=strategy.site_id
                  LEFT JOIN seo_agent.posts post
                    ON post.id=strategy.post_id AND post.site_id=strategy.site_id
                 WHERE strategy.id=CAST(:strategy_task_id AS uuid)
                   AND strategy.task_type='review'
                   AND strategy.payload->>'kind'='seo_strategy'
                """
            ),
            {"strategy_task_id": strategy_task_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("bound seo_strategy task not found")
    context = dict(row)
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


def canonical_article_patch(patch: dict[str, Any]) -> dict[str, Any]:
    unsupported = set(patch) - ARTICLE_FIELDS
    if unsupported:
        raise ValueError(f"article patch contains forbidden fields: {sorted(unsupported)}")
    title = str(patch.get("title") or "").strip()
    body = str(patch.get("body") or "").strip()
    meta_title = str(patch.get("meta_title") or "").strip()
    meta_description = str(patch.get("meta_description") or "").strip()

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
    return {
        "title": title,
        "body": body,
        "meta_title": meta_title,
        "meta_description": meta_description,
        "images": images,
        "image_alts": alts,
    }


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
        and images.get("upload") is True
    )


def _capability_filtered_article_patch(
    action: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    if _image_patch_supported(action):
        return patch
    return {
        field: value
        for field, value in patch.items()
        if field not in {"images", "image_alts"}
    }


def _validate_article_patch(
    patch: dict[str, Any],
    *,
    keyword: str,
    internal_link_plan: list[dict[str, Any]],
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
    for src in patch["images"]:
        if src not in patch["body"]:
            raise ValueError(f"article image is not inserted in the body: {src}")
        if not patch["image_alts"].get(src):
            raise ValueError(f"article image is missing ALT text: {src}")
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
        }
    body = str(context.get("post_content_md") or context.get("post_content_html") or "")
    return {
        "title": context.get("post_title"),
        "body": body,
        "meta_title": context.get("post_meta_title"),
        "meta_description": context.get("post_meta_description"),
        "images": list(_extract_images(body)),
        "image_alts": _extract_images(body),
    }


async def _ensure_approved_execution(
    session: AsyncSession,
    *,
    action: dict[str, Any],
    context: dict[str, Any],
) -> str:
    decision = dict(context.get("strategy_decision") or {})
    execution_id = str(decision.get("execution_task_id") or "")
    if context.get("strategy_status") == "queued":
        reviewed = await review_strategy(
            session,
            task_id=str(action["source_strategy_task_id"]),
            approved=True,
        )
        execution_id = str(reviewed.get("execution_task_id") or "")
    elif context.get("strategy_status") == "done":
        if decision.get("review_status") != "approved":
            raise ValueError("bound seo_strategy is not approved")
    else:
        raise ValueError("bound seo_strategy is no longer approvable")
    if not execution_id:
        raise ValueError("approved seo_strategy did not create an execution task")
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
            "image_plan": [
                {"src": src, "alt": patch["image_alts"].get(src)}
                for src in patch["images"]
            ],
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
    return {
        "title": title,
        "body": body,
        "meta_title": meta_title,
        "meta_description": meta_description,
        "images": list(images),
        "image_alts": images,
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


def _semantic_value(field: str, value: Any) -> Any:
    if field == "body":
        plain = re.sub(r"<[^>]+>", " ", str(value or ""))
        # Images and ALT are compared as independent approved fields. Keeping
        # Markdown ALT text here creates a false body mismatch after OEMApps
        # renders the same image as an HTML tag.
        plain = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", plain)
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r" \1 ", plain)
        plain = re.sub(r"^#{1,6}\s*", "", plain, flags=re.M)
        plain = re.sub(r"^\s*\d+[.)]\s+", "", plain, flags=re.M)
        plain = re.sub(r"[*_`~>|-]+", " ", plain)
        return re.sub(r"\s+", " ", unescape(plain)).strip()
    if field == "images":
        values = value if isinstance(value, list) else []
        return sorted(_normalize_url(str(item)) for item in values)
    if field == "image_alts":
        values = value if isinstance(value, dict) else {}
        return {
            _normalize_url(str(src)): re.sub(r"\s+", " ", str(alt)).strip()
            for src, alt in sorted(values.items())
        }
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


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
    "normalize_remote_article",
]
