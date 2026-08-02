from __future__ import annotations

import json
from typing import Any

import pytest

from app.clients.publishers import PublishResult
from app.services import publish_service


class Result:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def mappings(self) -> "Result":
        return self

    def first(self) -> Any:
        return self.value


class Session:
    def __init__(self, *, article: dict[str, Any] | None = None, approval: dict[str, Any] | None = None, site: dict[str, Any] | None = None) -> None:
        self.article = article or _article()
        self.approval = approval if approval is not None else _approval()
        self.site = site or _site()
        self.inserts: list[dict[str, Any]] = []
        self.article_updates: list[dict[str, Any]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
        sql = str(statement)
        params = params or {}
        if "INSERT INTO seo_agent.tasks" in sql:
            self.inserts.append(params)
            return Result({"id": "publish-task"})
        if "UPDATE seo_agent.articles" in sql:
            self.article_updates.append(params)
            return Result()
        if "UPDATE seo_agent.tasks" in sql:
            return Result()
        if "FROM seo_agent.articles WHERE" in sql:
            return Result(self.article)
        if "FROM seo_agent.sites WHERE" in sql:
            return Result(self.site)
        if "FROM seo_agent.tasks execution" in sql:
            return Result(self.approval)
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self) -> None:
        self.commits += 1


class Publisher:
    def __init__(self) -> None:
        self.published = 0
        self.publish_requests: list[Any] = []
        self.updated: list[str] = []
        self.update_requests: list[Any] = []
        self.seo_synced: list[tuple[str, Any]] = []
        self.gets: list[str] = []
        self.found: dict[str, Any] | None = None
        self.remote: dict[str, Any] | None = _remote()

    async def publish(self, req: Any) -> PublishResult:
        self.published += 1
        self.publish_requests.append(req)
        return PublishResult(ok=True, dry_run=req.status == "draft", post_id=None if req.status == "draft" else "42")

    async def update(self, post_id: str, req: Any) -> PublishResult:
        self.updated.append(post_id)
        self.update_requests.append(req)
        return PublishResult(ok=True, dry_run=req.status == "draft", post_id=None if req.status == "draft" else post_id)

    async def sync_seo_metadata(self, post_id: str, req: Any) -> PublishResult:
        self.seo_synced.append((post_id, req))
        return PublishResult(ok=True, dry_run=False, post_id=post_id, url="https://example.com/hello/")

    async def get_article(self, post_id: str) -> dict[str, Any] | None:
        self.gets.append(post_id)
        return self.remote

    async def find_article_by_slug(self, slug: str) -> dict[str, Any] | None:
        return self.found


class UnknownRemotePublisher(Publisher):
    async def publish(self, req: Any) -> PublishResult:
        self.published += 1
        return PublishResult(
            ok=False,
            dry_run=False,
            error="create timed out and readback timed out",
            remote_outcome="unknown_remote_state",
            raw={"retry_policy": "manual_readback_required"},
        )


class IdentityConflictPublisher(Publisher):
    async def find_article_by_slug(self, slug: str) -> dict[str, Any] | None:
        raise RuntimeError("SHOPIFY_ARTICLE_IDENTITY_CONFLICT: blog_gid_or_handle")


class AcknowledgedUpdatePublisher(Publisher):
    async def update(self, post_id: str, req: Any) -> PublishResult:
        self.updated.append(post_id)
        self.update_requests.append(req)
        return PublishResult(
            ok=True,
            dry_run=False,
            post_id=post_id,
            remote_outcome="acknowledged",
            raw={"data": True},
        )


def _use_publisher(monkeypatch: pytest.MonkeyPatch, publisher: Publisher) -> None:
    async def resolve(*_args: Any, **_kwargs: Any) -> Publisher:
        return publisher
    async def reconcile(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"article_changes": 0, "task_changes": 0}
    async def exception(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"exception_id": "exception-id"}

    monkeypatch.setattr(publish_service, "publisher_for_site_runtime", resolve)
    monkeypatch.setattr(publish_service, "reconcile_article_public_url", reconcile)
    monkeypatch.setattr(publish_service, "record_exception", exception)


def _article(**changes: Any) -> dict[str, Any]:
    return {
        "id": "article-id",
        "task_id": "execution-id",
        "site_id": "site-id",
        "title": "Hello",
        "slug": "hello",
        "target_url": "",
        "status": "generated",
        "content_md": "# Hello",
        "meta_title": "Hello",
        "meta_description": "Description",
        "primary_keyword": "hello",
        "language_code": "en",
        "market": "US",
        "qa_checklist": [{"key": "ok", "ok": True}],
        "image_plan": [],
        "published_post_id": None,
        "published_url": None,
        "published_at": None,
        **changes,
    }


def _site(**changes: Any) -> dict[str, Any]:
    return {
        "id": "site-id",
        "site_key": "site",
        "name": "Site",
        "site_type": "wp",
        "domain": "https://example.com",
        "base_url": None,
        "api_base_url": None,
        "status": "active",
        "api_config": {"username": "user"},
        "content_role": "blog",
        "market": "US",
        "language_code": "en",
        **changes,
    }


def _approval(**changes: Any) -> dict[str, Any]:
    return {
        "strategy_task_id": "strategy-id",
        "execution_task_id": "execution-id",
        "execution_type": "new_article",
        "approved_remote_id": None,
        **changes,
    }


def _remote(**changes: Any) -> dict[str, Any]:
    return {
        "id": 42,
        "title": {"rendered": "Hello"},
        "slug": "hello",
        "link": "https://example.com/hello/",
        "status": "publish",
        **changes,
    }


@pytest.mark.asyncio
async def test_dry_run_is_database_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(approval=None)
    publisher = Publisher()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(session, article_id="article-id", site_id=None, dry_run=True)

    assert result["ok"] is True
    assert result["task_id"] is None
    assert session.inserts == []
    assert session.article_updates == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_publish_request_uses_uploaded_cover_from_article_image_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cover_url = "https://site.example.com/wp-content/uploads/cover.png"
    session = Session(
        article=_article(
            content_md=(
                "# Hello\n\n"
                "Useful body.\n\n"
                "![Compatibility diagram](https://site.example.com/wp-content/uploads/body.png)"
            ),
            image_plan=[
                {
                    "role": "cover",
                    "image_id": "501",
                    "src": cover_url,
                    "alt": "Replacement pod beside its charging dock",
                },
                {
                    "role": "content",
                    "src": "https://site.example.com/wp-content/uploads/body.png",
                    "alt": "Compatibility diagram",
                },
            ],
        )
    )
    publisher = Publisher()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session,
        article_id="article-id",
        site_id=None,
        dry_run=True,
    )

    assert result["ok"] is True
    request = publisher.publish_requests[0]
    assert request.image_cover_id == "501"
    assert request.image_cover_url == cover_url
    assert request.image_cover_alt == "Replacement pod beside its charging dock"
    assert "![Compatibility diagram]" in request.content_md


@pytest.mark.asyncio
async def test_live_publish_rejects_missing_approved_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(approval={})
    publisher = Publisher()
    _use_publisher(monkeypatch, publisher)

    with pytest.raises(publish_service.PublishError, match="human-approved"):
        await publish_service.publish_article(session, article_id="article-id", site_id=None, dry_run=False)

    assert publisher.published == 0
    assert session.inserts == []


@pytest.mark.asyncio
async def test_existing_remote_id_is_verified_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(article=_article(published_post_id="42", published_url="https://example.com/hello/"))
    publisher = Publisher()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(session, article_id="article-id", site_id=None, dry_run=False)

    assert result["ok"] is True
    assert result["idempotent"] is result["reused"] is True
    assert result["action"] == "existing_remote"
    assert publisher.gets == ["42"]
    assert publisher.published == 0
    decision = json.loads(session.inserts[0]["decision"])
    assert decision["remote_verification"]["ok"] is True
    assert decision["idempotent"] is decision["reused"] is True


@pytest.mark.asyncio
async def test_matching_slug_is_reused_without_creating(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    publisher = Publisher()
    publisher.found = _remote()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(session, article_id="article-id", site_id=None, dry_run=False)

    assert result["ok"] is True
    assert result["action"] == "slug_reuse"
    assert result["post_id"] == "42"
    assert publisher.published == 0
    assert len(session.article_updates) == 1


@pytest.mark.asyncio
async def test_unknown_remote_publish_is_persisted_as_blocked_p1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    publisher = UnknownRemotePublisher()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session, article_id="article-id", site_id=None, dry_run=False
    )

    assert result["ok"] is False
    assert result["remote_outcome"] == "unknown_remote_state"
    assert result["exception_id"] == "exception-id"
    assert session.inserts[0]["status"] == "blocked"
    assert json.loads(session.inserts[0]["payload"])["remote_outcome"] == "unknown_remote_state"
    assert session.article_updates == []


@pytest.mark.asyncio
async def test_acknowledged_update_with_verified_readback_is_not_treated_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session(
        approval=_approval(
            execution_type="update_article",
            approved_remote_id="42",
        )
    )
    publisher = AcknowledgedUpdatePublisher()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session,
        article_id="article-id",
        site_id=None,
        dry_run=False,
        update_post_id="42",
    )

    assert result["ok"] is True
    assert result["remote_outcome"] == "acknowledged"
    assert result["exception_id"] is None
    assert session.inserts[0]["status"] == "done"
    assert len(session.article_updates) == 1


@pytest.mark.asyncio
async def test_verified_update_without_connector_outcome_is_confirmed_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session(
        approval=_approval(
            execution_type="update_article",
            approved_remote_id="42",
        )
    )
    publisher = Publisher()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session,
        article_id="article-id",
        site_id=None,
        dry_run=False,
        update_post_id="42",
    )

    assert result["ok"] is True
    assert result["remote_outcome"] == "confirmed_applied"
    assert result["exception_id"] is None
    assert session.inserts[0]["status"] == "done"
    assert json.loads(session.inserts[0]["payload"])["remote_outcome"] == (
        "confirmed_applied"
    )
    assert len(session.article_updates) == 1


@pytest.mark.asyncio
async def test_acknowledged_update_with_failed_readback_becomes_unknown_remote_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session(
        approval=_approval(
            execution_type="update_article",
            approved_remote_id="42",
        )
    )
    publisher = AcknowledgedUpdatePublisher()
    publisher.remote = _remote(title={"rendered": "Unexpected old title"})
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session,
        article_id="article-id",
        site_id=None,
        dry_run=False,
        update_post_id="42",
    )

    assert result["ok"] is False
    assert result["remote_outcome"] == "unknown_remote_state"
    assert result["exception_id"] == "exception-id"
    assert session.inserts[0]["status"] == "blocked"
    assert session.article_updates == []


@pytest.mark.asyncio
async def test_shopify_identity_conflict_blocks_before_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    publisher = IdentityConflictPublisher()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session, article_id="article-id", site_id=None, dry_run=False
    )

    assert result["remote_outcome"] == "identity_conflict"
    assert session.inserts[0]["status"] == "blocked"
    assert publisher.published == 0


@pytest.mark.asyncio
async def test_oemapps_remote_detail_url_cannot_override_canonical_publish_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session(site=_site(
        site_type="main",
        domain="example.com",
        base_url="https://example.com",
        api_base_url="https://openapi.oemapps.com",
    ))
    publisher = Publisher()
    publisher.remote = {
        "id": 42,
        "title": "Hello",
        "slug": "hello",
        "url": "https://example.com/blogs/detail/42",
        "status": "published",
    }
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session,
        article_id="article-id",
        site_id=None,
        dry_run=False,
    )

    assert result["ok"] is True
    assert result["url"] == "https://example.com/blogs/hello"
    assert session.article_updates[0]["url"] == "https://example.com/blogs/hello"


@pytest.mark.asyncio
async def test_publish_audit_preserves_the_connector_raw_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    publisher = Publisher()
    publisher.remote = _remote(status="published", raw_status="1")
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session,
        article_id="article-id",
        site_id=None,
        dry_run=False,
    )

    assert result["ok"] is True
    decision = json.loads(session.inserts[0]["decision"])
    assert decision["remote_verification"]["remote"]["status"] == "published"
    assert decision["remote_verification"]["remote"]["raw_status"] == "1"


@pytest.mark.asyncio
async def test_failed_remote_verification_saves_failed_task_without_publishing_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    publisher = Publisher()
    publisher.remote = _remote(title={"rendered": "Wrong"}, status="draft")
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(session, article_id="article-id", site_id=None, dry_run=False)

    assert result["ok"] is False
    assert session.inserts[0]["status"] == "failed"
    assert session.article_updates == []
    decision = json.loads(session.inserts[0]["decision"])
    assert decision["remote_verification"]["checks"]["title"] is False
    assert decision["remote_verification"]["checks"]["public_status"] is False


@pytest.mark.asyncio
async def test_update_uses_approved_remote_id_and_verifies_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(approval=_approval(execution_type="update_article", approved_remote_id="17"))
    publisher = Publisher()
    publisher.remote = _remote(id=17, slug="old-slug", link="https://example.com/old-slug/")
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.publish_article(
        session,
        article_id="article-id",
        site_id=None,
        dry_run=False,
        update_post_id="17",
    )

    assert result["ok"] is True
    assert result["action"] == "update"
    assert publisher.updated == ["17"]
    assert publisher.gets == ["17"]


@pytest.mark.asyncio
async def test_sync_seo_metadata_updates_only_the_published_shopify_article(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(
        article=_article(
            status="published",
            published_post_id="gid://shopify/Article/17",
            published_url="https://example.myshopify.com/blogs/news/hello",
        ),
        site=_site(site_type="shopify", domain="example.myshopify.com"),
    )
    publisher = Publisher()
    _use_publisher(monkeypatch, publisher)

    result = await publish_service.sync_article_seo_metadata(session, article_id="article-id")

    assert result["ok"] is True
    assert result["action"] == "sync_seo_metadata"
    assert publisher.seo_synced and publisher.seo_synced[0][0] == "gid://shopify/Article/17"
    assert publisher.seo_synced[0][1].content_md == ""
    assert publisher.seo_synced[0][1].meta_description == "Description"
    assert publisher.updated == []
    assert session.article_updates == []
    assert json.loads(session.inserts[0]["decision"])["action"] == "sync_seo_metadata"
