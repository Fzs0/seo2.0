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

from app.clients.publishers import PublishRequest, PublishResult, publisher_for_site


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
        "primary_keyword, language_code, market, qa_checklist, published_post_id, published_url, published_at "
        "FROM seo_agent.articles WHERE id = CAST(:id AS uuid)"
        + (" FOR UPDATE" if not dry_run else "")
    )
    a = (await session.execute(text(article_sql), {"id": article_id})).mappings().first()
    if not a:
        raise PublishError(f"article id={article_id} not found")
    if not a["content_md"]:
        raise PublishError(f"article id={article_id} has empty content_md")
    failed_qa = [check.get("key") for check in a["qa_checklist"] or [] if not check.get("ok")]
    if not a["qa_checklist"] or failed_qa:
        raise PublishError(f"article id={article_id} QA not passed: {', '.join(str(key) for key in failed_qa) or 'missing checklist'}")

    target_site_id = site_id or a["site_id"]
    if not target_site_id:
        raise PublishError(f"article id={article_id} has no assigned site")
    s = (
        await session.execute(
            text(
                "SELECT id, site_key, name, site_type, domain, base_url, api_base_url, status, api_config, "
                "content_role, market, language_code FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
            ),
            {"id": target_site_id},
        )
    ).mappings().first()
    if not s:
        raise PublishError(f"site id={target_site_id} not found")
    if s["status"] != "active":
        raise PublishError(f"site id={target_site_id} is not active (status={s['status']})")
    if not dry_run and not s["api_config"]:
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
    publisher = publisher_for_site(dict(s), dry_run=dry_run)

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
                result.url = verification["remote"]["url"] or result.url
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
        result = PublishResult(ok=False, dry_run=False, error=str(error))

    decision = {
        "adapter": publisher.__class__.__name__,
        "ok": result.ok,
        "dry_run": False,
        "action": action,
        "idempotent": idempotent,
        "reused": reused,
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
    )


async def _approved_execution(session: AsyncSession, task_id: Any, site_id: Any) -> dict[str, Any] | None:
    if not task_id:
        return None
    row = (
        await session.execute(
            text(
                """
                SELECT strategy.id::text AS strategy_task_id, execution.id::text AS execution_task_id,
                       execution.task_type AS execution_type, post.external_id AS approved_remote_id
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
                "status": "done" if result.ok else "failed",
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
                    },
                    ensure_ascii=False,
                ),
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
        "remote": {"id": remote_id, "title": title, "slug": slug, "url": url, "status": status},
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
) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "post_id": result.post_id,
        "url": result.url,
        "error": result.error,
        "raw": result.raw,
        "task_id": task_id,
        "article_id": article_id,
        "site_id": site_id,
        "action": action,
        "idempotent": idempotent,
        "reused": reused,
        "remote_verification": remote_verification,
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


__all__ = ["PublishError", "publish_article", "get_publish_task"]
