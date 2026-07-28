"""Safe article publishing with approval, idempotency, and remote verification."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import unquote, urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.publishers import PublishRequest, PublishResult
from app.core.article_urls import resolve_article_public_url
from app.core.remote_outcomes import policy_for_remote_outcome
from app.services.article_url_reconciliation_service import reconcile_article_public_url
from app.services.strategy_exception_service import record_exception
from app.services.shopify_connection_service import publisher_for_site_runtime
from app.services.article_qa import assess_article_qa


class PublishError(Exception):
    pass


async def publish_article(
    session: AsyncSession,
    *,
    article_id: str,
    site_id: str | None,
    dry_run: bool = True,
    actor: str = "publish_api",
    update_post_id: str | None = None,
) -> dict[str, Any]:
    article_sql = (
        "SELECT id, task_id, site_id, title, slug, target_url, status, content_md, meta_title, meta_description, "
        "primary_keyword, language_code, market, qa_checklist, qa_summary, published_post_id, published_url, published_at "
        "FROM seo_agent.articles WHERE id = CAST(:id AS uuid)"
        + (" FOR UPDATE" if not dry_run else "")
    )
    a = (await session.execute(text(article_sql), {"id": article_id})).mappings().first()
    if not a:
        raise PublishError(f"article id={article_id} not found")
    if not a["content_md"]:
        raise PublishError(f"article id={article_id} has empty content_md")
    qa = assess_article_qa(a["qa_checklist"], a.get("qa_summary"))
    if not qa.passed:
        if qa.state.value == "invalid":
            raise PublishError(f"article id={article_id} QA 数据格式异常: {qa.message}")
        failed = ", ".join(qa.failed_keys)
        raise PublishError(
            f"article id={article_id} QA not passed: {failed or qa.message or 'missing checklist'}"
        )

    target_site_id = site_id or a["site_id"]
    if not target_site_id:
        raise PublishError(f"article id={article_id} has no assigned site")
    s = (
        await session.execute(
            text(
                "SELECT id, business_id, site_key, name, site_type, domain, base_url, api_base_url, status, api_config, "
                "content_role, market, language_code FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
            ),
            {"id": target_site_id},
        )
    ).mappings().first()
    if not s:
        raise PublishError(f"site id={target_site_id} not found")
    if s["status"] != "active":
        raise PublishError(f"site id={target_site_id} is not active (status={s['status']})")
    if not dry_run and not s["api_config"] and str(s["site_type"]).casefold() not in {"shopify", "shopify_admin"}:
        raise PublishError(f"site id={target_site_id} has empty api_config")
    article_language = _locale_token(a["language_code"])
    site_language = _locale_token(s["language_code"])
    if article_language and site_language and article_language != site_language:
        raise PublishError(f"article language {a['language_code']} cannot publish to site language {s['language_code']}")
    article_market = _locale_token(a["market"])
    site_market = _locale_token(s["market"])
    if article_market and site_market and article_market != site_market:
        raise PublishError(f"article market {a['market']} cannot publish to site market {s['market']}")

    req = PublishRequest(
        title=a["title"] or a["slug"] or "untitled",
        slug=a["slug"] or f"article-{a['id']}",
        content_md=a["content_md"],
        meta_title=a["meta_title"] or a["title"] or "",
        meta_description=a["meta_description"] or "",
        status="draft" if dry_run else "publish",
        excerpt=a["meta_description"] or "",
        primary_keyword=a["primary_keyword"] or "",
    )
    publisher = await publisher_for_site_runtime(
        session, dict(s), dry_run=dry_run, require_active=not dry_run
    )

    if dry_run:
        result = await (publisher.update(update_post_id, req) if update_post_id else publisher.publish(req))
        return _response(result, article_id, target_site_id, task_id=None, action="dry_run")

    approval = await _approved_execution(session, a["task_id"], target_site_id)
    if not approval:
        raise PublishError("article is not linked to a human-approved seo_strategy execution")
    approved_remote_id = str(approval.get("approved_remote_id") or "")
    if approval["execution_type"] == "update_article":
        if not update_post_id or not approved_remote_id or str(update_post_id) != approved_remote_id:
            raise PublishError("update publish must use the remote ID approved by the seo_strategy execution")
        if a["published_post_id"] and str(a["published_post_id"]) != approved_remote_id:
            raise PublishError("article published_post_id does not match the approved update target")
    elif update_post_id:
        raise PublishError("new article strategy cannot publish as an update")

    action = "create"
    reused = False
    idempotent = False
    verification: dict[str, Any] = {
        "ok": False,
        "checks": {"exists": False, "title": False, "url_or_slug": False, "public_status": False},
        "remote": {"id": "", "title": "", "slug": "", "url": "", "status": ""},
    }
    try:
        remote: dict[str, Any] | None = None
        remote_id = str(a["published_post_id"] or "")
        if remote_id:
            action, reused, idempotent = "existing_remote", True, True
            remote = await publisher.get_article(remote_id)
            result = PublishResult(ok=True, dry_run=False, post_id=remote_id, url=a["published_url"] or "", raw={})
        elif update_post_id:
            action = "update"
            result = await publisher.update(str(update_post_id), req)
            remote_id = str(result.post_id or update_post_id)
        else:
            remote = await publisher.find_article_by_slug(req.slug)
            if remote:
                action, reused, idempotent = "slug_reuse", True, True
                remote_id = _remote_id(remote)
                result = PublishResult(ok=bool(remote_id), dry_run=False, post_id=remote_id or None, url=_remote_url(remote), raw={})
            else:
                result = await publisher.publish(req)
                remote_id = str(result.post_id or "")

        if result.ok and remote_id:
            remote = remote or await publisher.get_article(remote_id)
            verification = _verify_remote(
                remote,
                req,
                remote_id,
                require_slug_match=action in {"create", "slug_reuse"},
            )
            if verification["ok"]:
                result.post_id = remote_id
                result.url = resolve_article_public_url(
                    dict(s),
                    slug=req.slug,
                    article_id=remote_id,
                    remote_url=verification["remote"]["url"],
                    canonical_url=result.url,
                ) or result.url
            else:
                result.ok = False
                result.error = "remote verification failed: " + ", ".join(
                    key for key, ok in verification["checks"].items() if not ok
                )
        elif result.ok:
            result.ok = False
            result.error = "remote publish did not return a post ID"
    except Exception as error:  # noqa: BLE001 - connector errors must become an auditable failed task
        verification["error"] = str(error)
        identity_conflict = "SHOPIFY_ARTICLE_IDENTITY_CONFLICT" in str(error)
        result = PublishResult(
            ok=False,
            dry_run=False,
            error=str(error),
            remote_outcome="identity_conflict" if identity_conflict else None,
            raw=(
                {
                    "error_code": "SHOPIFY_ARTICLE_IDENTITY_CONFLICT",
                    "retry_policy": "manual_identity_resolution_required",
                }
                if identity_conflict
                else {}
            ),
        )

    decision = {
        "adapter": publisher.__class__.__name__,
        "ok": result.ok,
        "dry_run": False,
        "action": action,
        "idempotent": idempotent,
        "reused": reused,
        "remote_outcome": result.remote_outcome,
        "remote_verification": verification,
    }
    task_id = await _save_publish_task(
        session,
        article_id=article_id,
        site_id=str(target_site_id),
        site_key=s["site_key"],
        target_url=result.url or a["target_url"] or "",
        actor=actor,
        update_post_id=update_post_id,
        approval=approval,
        result=result,
        decision=decision,
    )
    exception_id = await _record_uncertain_publish_exception(
        session,
        result=result,
        task_id=task_id,
        approval=approval,
        site=dict(s),
        target_url=result.url or a["target_url"] or "",
    )
    if result.ok:
        await session.execute(
            text(
                """
                UPDATE seo_agent.articles
                   SET site_id = CAST(:site_id AS uuid), published_post_id = :post_id,
                       published_url = :url, published_at = :ts, status = 'published', updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "site_id": target_site_id,
                "post_id": result.post_id,
                "url": result.url or "",
                "ts": datetime.now(UTC),
                "id": article_id,
            },
        )
        await reconcile_article_public_url(
            session,
            article_id=article_id,
            remote_url=result.url,
        )
    await session.commit()
    return _response(
        result,
        article_id,
        target_site_id,
        task_id=task_id,
        action=action,
        idempotent=idempotent,
        reused=reused,
        remote_verification=verification,
        exception_id=exception_id,
    )


async def sync_article_seo_metadata(
    session: AsyncSession,
    *,
    article_id: str,
    actor: str = "article_metadata_sync",
) -> dict[str, Any]:
    """Synchronize Shopify search metadata without regenerating or replacing article body."""
    article = (
        await session.execute(
            text(
                "SELECT id, site_id, title, slug, target_url, status, meta_title, meta_description, "
                "published_post_id, published_url FROM seo_agent.articles "
                "WHERE id = CAST(:id AS uuid) FOR UPDATE"
            ),
            {"id": article_id},
        )
    ).mappings().first()
    if not article:
        raise PublishError(f"article id={article_id} not found")
    if not article["site_id"] or not article["published_post_id"]:
        raise PublishError("article has not been published to a remote post")
    if not (str(article["meta_title"] or "").strip() or str(article["meta_description"] or "").strip()):
        raise PublishError("article has no SEO title or meta description to sync")

    site = (
        await session.execute(
            text(
                "SELECT id, business_id, site_key, name, site_type, domain, base_url, api_base_url, status, api_config, "
                "content_role, market, language_code FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
            ),
            {"id": article["site_id"]},
        )
    ).mappings().first()
    if not site:
        raise PublishError(f"site id={article['site_id']} not found")
    if site["status"] != "active":
        raise PublishError(f"site id={article['site_id']} is not active (status={site['status']})")
    if str(site["site_type"] or "").casefold() not in {"shopify", "shopify_admin"}:
        raise PublishError("SEO metadata sync is currently available only for Shopify articles")

    req = PublishRequest(
        title=article["title"] or article["slug"] or "untitled",
        slug=article["slug"] or f"article-{article['id']}",
        content_md="",
        meta_title=article["meta_title"] or article["title"] or "",
        meta_description=article["meta_description"] or "",
        status="publish",
    )
    publisher = await publisher_for_site_runtime(session, dict(site), dry_run=False)
    try:
        result = await publisher.sync_seo_metadata(str(article["published_post_id"]), req)
    except Exception as error:  # noqa: BLE001 - remote failures need an audit task
        result = PublishResult(ok=False, dry_run=False, error=str(error))

    decision = {
        "adapter": publisher.__class__.__name__,
        "ok": result.ok,
        "dry_run": False,
        "action": "sync_seo_metadata",
        "content_changed": False,
    }
    task_id = await _save_seo_metadata_sync_task(
        session,
        article_id=str(article["id"]),
        site_id=str(site["id"]),
        site_key=str(site["site_key"]),
        target_url=result.url or article["published_url"] or article["target_url"] or "",
        actor=actor,
        result=result,
        decision=decision,
    )
    await session.commit()
    return _response(
        result,
        str(article["id"]),
        str(site["id"]),
        task_id=task_id,
        action="sync_seo_metadata",
    )


async def _record_uncertain_publish_exception(
    session: AsyncSession,
    *,
    result: PublishResult,
    task_id: Any,
    approval: dict[str, Any],
    site: dict[str, Any],
    target_url: str,
) -> str | None:
    policy = policy_for_remote_outcome(result.remote_outcome)
    if not policy or policy.task_status != "blocked":
        return None
    exception = await record_exception(
        session,
        {
            "run_id": approval.get("strategy_run_id"),
            "action_id": approval.get("strategy_action_id"),
            "business_id": site.get("business_id"),
            "site_id": str(site["id"]),
            "publish_task_id": str(task_id),
            "execution_task_id": approval.get("execution_task_id"),
            "target_url": target_url,
            "type": "remote_write_uncertain",
            "error_code": policy.error_code,
            "stage": "publishing",
            "severity": policy.severity,
            "summary": "Remote publish outcome requires manual readback before retry.",
            "remote_write_occurred": policy.remote_write_occurred,
            "retryable": policy.auto_retry_allowed,
            "responsibility_type": "human_operator",
            "unlock_condition": "Complete a fresh remote readback and resolve the exception.",
        },
    )
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET payload = payload || "
            "jsonb_build_object('exception_id', CAST(:exception_id AS text)), updated_at=now() "
            "WHERE id=CAST(:task_id AS uuid)"
        ),
        {"task_id": task_id, "exception_id": exception["exception_id"]},
    )
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET status='blocked',
                   payload=payload || jsonb_build_object(
                     'remote_outcome', CAST(:remote_outcome AS text),
                     'publish_task_id', CAST(:publish_task_id AS text),
                     'exception_id', CAST(:exception_id AS text)
                   ),
                   error_message=COALESCE(error_message, :error_message),
                   finished_at=COALESCE(finished_at, now()),
                   updated_at=now()
             WHERE id=CAST(:execution_task_id AS uuid)
            """
        ),
        {
            "execution_task_id": approval.get("execution_task_id"),
            "remote_outcome": result.remote_outcome,
            "publish_task_id": str(task_id),
            "exception_id": exception["exception_id"],
            "error_message": result.error,
        },
    )
    if approval.get("strategy_action_id"):
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status='blocked',
                       payload=payload || jsonb_build_object(
                         'status', 'blocked',
                         'result', 'blocked',
                         'recovery_status', CAST(:remote_outcome AS text),
                         'publish_task_id', CAST(:publish_task_id AS text),
                         'execution_task_id', CAST(:execution_task_id AS text),
                         'exception_id', CAST(:exception_id AS text)
                       ),
                       decision=decision || jsonb_build_object(
                         'status', 'blocked',
                         'result', 'blocked',
                         'remote_outcome', CAST(:remote_outcome AS text)
                       ),
                       finished_at=COALESCE(finished_at, now()),
                       updated_at=now()
                 WHERE id=CAST(:action_id AS uuid)
                   AND payload->>'kind'='strategy_action'
                """
            ),
            {
                "action_id": approval["strategy_action_id"],
                "remote_outcome": result.remote_outcome,
                "publish_task_id": str(task_id),
                "execution_task_id": approval.get("execution_task_id"),
                "exception_id": exception["exception_id"],
            },
        )
    if approval.get("strategy_run_id"):
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status=CASE
                         WHEN decision->>'status'='awaiting_approval' THEN 'blocked'
                         ELSE 'done'
                       END,
                       decision=decision || jsonb_build_object(
                         'status', CASE
                           WHEN decision->>'status'='awaiting_approval' THEN 'blocked'
                           ELSE 'partial'
                         END,
                         'current_stage', CASE
                           WHEN decision->>'status'='awaiting_approval' THEN 'blocked'
                           ELSE 'partial'
                         END,
                         'remote_outcome', CAST(:remote_outcome AS text),
                         'exception_id', CAST(:exception_id AS text),
                         'next_action', 'resolve_remote_state'
                       ),
                       finished_at=COALESCE(finished_at, now()),
                       updated_at=now()
                 WHERE id=CAST(:run_id AS uuid)
                   AND payload->>'kind'='strategy_run'
                   AND decision->>'status' IN (
                     'awaiting_approval', 'executing', 'verifying', 'observing'
                   )
                """
            ),
            {
                "run_id": approval["strategy_run_id"],
                "remote_outcome": result.remote_outcome,
                "exception_id": exception["exception_id"],
            },
        )
    return str(exception["exception_id"])


async def _approved_execution(session: AsyncSession, task_id: Any, site_id: Any) -> dict[str, Any] | None:
    if not task_id:
        return None
    row = (
        await session.execute(
            text(
                """
                SELECT strategy.id::text AS strategy_task_id, execution.id::text AS execution_task_id,
                       execution.task_type AS execution_type, post.external_id AS approved_remote_id,
                       COALESCE(execution.payload->>'strategy_run_id', strategy.payload->>'strategy_run_id')
                         AS strategy_run_id,
                       execution.payload->>'strategy_action_id' AS strategy_action_id
                  FROM seo_agent.tasks execution
                  JOIN seo_agent.tasks strategy
                    ON strategy.id::text = execution.payload->>'strategy_task_id'
                  LEFT JOIN seo_agent.posts post ON post.id = execution.post_id AND post.site_id = execution.site_id
                 WHERE execution.id = CAST(:task_id AS uuid)
                   AND execution.site_id = CAST(:site_id AS uuid)
                   AND execution.task_type IN ('new_article', 'update_article')
                   AND execution.status IN ('running', 'done')
                   AND execution.decision->>'source_strategy_id' = strategy.id::text
                   AND strategy.task_type = 'review' AND strategy.status = 'done'
                   AND strategy.payload->>'kind' = 'seo_strategy'
                   AND strategy.decision->>'review_status' = 'approved'
                   AND strategy.decision->>'execution_task_id' = execution.id::text
                """
            ),
            {"task_id": task_id, "site_id": site_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _save_publish_task(
    session: AsyncSession,
    *,
    article_id: str,
    site_id: str,
    site_key: str,
    target_url: str,
    actor: str,
    update_post_id: str | None,
    approval: dict[str, Any],
    result: PublishResult,
    decision: dict[str, Any],
) -> Any:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, site_id, keyword_id, article_id, target_url, title,
                   payload, decision, error_message, run_after, started_at, finished_at)
                VALUES
                  ('publish', :status, 'P1', CAST(:site_id AS uuid),
                   (SELECT keyword_id FROM seo_agent.articles WHERE id = CAST(:article_id AS uuid)),
                   CAST(:article_id AS uuid), :target_url, :title,
                   CAST(:payload AS jsonb), CAST(:decision AS jsonb), :error_message, now(), now(), now())
                RETURNING id
                """
            ),
            {
                "status": _publish_task_status(result),
                "site_id": site_id,
                "article_id": article_id,
                "target_url": target_url,
                "title": f"publish article {article_id} -> {site_key}",
                "payload": json.dumps(
                    {
                        "dry_run": False,
                        "actor": actor,
                        "update_post_id": update_post_id,
                        "strategy_task_id": approval["strategy_task_id"],
                        "execution_task_id": approval["execution_task_id"],
                        "strategy_run_id": approval.get("strategy_run_id"),
                        "remote_outcome": result.remote_outcome,
                        "remote_evidence": result.raw,
                    },
                    ensure_ascii=False,
                ),
                "decision": json.dumps(decision, ensure_ascii=False),
                "error_message": result.error,
            },
        )
    ).mappings().first()
    return row["id"] if row else None


async def _save_seo_metadata_sync_task(
    session: AsyncSession,
    *,
    article_id: str,
    site_id: str,
    site_key: str,
    target_url: str,
    actor: str,
    result: PublishResult,
    decision: dict[str, Any],
) -> Any:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, site_id, keyword_id, article_id, target_url, title,
                   payload, decision, error_message, run_after, started_at, finished_at)
                VALUES
                  ('publish', :status, 'P2', CAST(:site_id AS uuid),
                   (SELECT keyword_id FROM seo_agent.articles WHERE id = CAST(:article_id AS uuid)),
                   CAST(:article_id AS uuid), :target_url, :title,
                   CAST(:payload AS jsonb), CAST(:decision AS jsonb), :error_message, now(), now(), now())
                RETURNING id
                """
            ),
            {
                "status": _publish_task_status(result),
                "site_id": site_id,
                "article_id": article_id,
                "target_url": target_url,
                "title": f"sync SEO metadata article {article_id} -> {site_key}",
                "payload": json.dumps({"actor": actor, "action": "sync_seo_metadata", "content_changed": False}, ensure_ascii=False),
                "decision": json.dumps(decision, ensure_ascii=False),
                "error_message": result.error,
            },
        )
    ).mappings().first()
    return row["id"] if row else None


def _verify_remote(
    remote: dict[str, Any] | None,
    req: PublishRequest,
    post_id: str,
    *,
    require_slug_match: bool,
) -> dict[str, Any]:
    remote = remote or {}
    remote_id = _remote_id(remote)
    title = _remote_text(remote.get("title"))
    url = _remote_url(remote)
    slug = _remote_slug(remote, url)
    status = str(remote.get("status") or ("publish" if remote.get("isPublished") is True else "")).strip().casefold()
    checks = {
        "exists": bool(remote_id) and remote_id == str(post_id),
        "title": _normal_text(title) == _normal_text(req.title),
        "url_or_slug": slug == req.slug.strip().strip("/").casefold() if require_slug_match else bool(url or slug),
        "public_status": status in {"publish", "published", "public"},
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "remote": {
            "id": remote_id,
            "title": title,
            "slug": slug,
            "url": url,
            "status": status,
            "raw_status": remote.get("raw_status", remote.get("status")),
        },
    }


def _remote_id(remote: dict[str, Any]) -> str:
    return str(remote.get("id") or remote.get("post_id") or remote.get("articleId") or "")


def _remote_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("raw") or value.get("rendered") or ""
    return unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def _remote_url(remote: dict[str, Any]) -> str:
    return str(remote.get("url") or remote.get("link") or remote.get("permalink") or "").strip()


def _remote_slug(remote: dict[str, Any], url: str) -> str:
    slug = str(remote.get("slug") or remote.get("handle") or "").strip()
    if not slug and url:
        slug = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    return slug.strip("/").casefold()


def _normal_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _publish_task_status(result: PublishResult) -> str:
    if result.ok:
        return "done"
    policy = policy_for_remote_outcome(result.remote_outcome)
    return policy.task_status if policy else "failed"


def _response(
    result: PublishResult,
    article_id: str,
    site_id: Any,
    *,
    task_id: Any,
    action: str,
    idempotent: bool = False,
    reused: bool = False,
    remote_verification: dict[str, Any] | None = None,
    exception_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "post_id": result.post_id,
        "url": result.url,
        "error": result.error,
        "remote_outcome": result.remote_outcome,
        "raw": result.raw,
        "task_id": task_id,
        "article_id": article_id,
        "site_id": site_id,
        "action": action,
        "idempotent": idempotent,
        "reused": reused,
        "remote_verification": remote_verification,
        "exception_id": exception_id,
    }


async def get_publish_task(session: AsyncSession, task_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, task_type, status, priority, article_id, target_url, title, "
                "decision, error_message, started_at, finished_at, created_at "
                "FROM seo_agent.tasks WHERE id = CAST(:id AS uuid) AND task_type = 'publish'"
            ),
            {"id": task_id},
        )
    ).mappings().first()
    return dict(row) if row else None


def _locale_token(value: Any) -> str:
    return str(value or "").strip().lower().split("/", 1)[0].strip()


__all__ = ["PublishError", "publish_article", "sync_article_seo_metadata", "get_publish_task"]
