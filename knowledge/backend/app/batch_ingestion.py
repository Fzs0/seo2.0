from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import UUID, uuid4

from defusedxml import ElementTree
from lxml import html as lxml_html
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .ai_extractor import AIExtractionError
from .quality_gate import QualityReviewError
from .errors import InvalidInputError, NotFoundError, UrlFetchError
from .knowledge_service import KnowledgeService
from .schemas import BatchSourceSpec, ImportDocumentRequest
from .web_importer import HTML_CONTENT_TYPES, SafeWebClient, WebArticleFetcher


DISCOVERY_MAX_BYTES = 5 * 1024 * 1024
MAX_SITEMAPS = 100
MAX_SITEMAP_DEPTH = 3
MAX_DISCOVERED_URLS = 100_000
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
XML_TYPES = {
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
}
ROBOTS_TYPES = {"text/plain"}
LISTING_PATH_SEGMENTS = {
    "archive",
    "archives",
    "author",
    "authors",
    "category",
    "categories",
    "feed",
    "rss",
    "atom",
    "search",
    "tag",
    "tags",
}


@dataclass(frozen=True, slots=True)
class DiscoveredUrl:
    url: str
    published_at: datetime | None = None
    modified_at: datetime | None = None
    source_kind: str = "sitemap"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    methods: tuple[str, ...]
    discovered: tuple[DiscoveredUrl, ...]
    warnings: tuple[str, ...] = ()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_date(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _scope_path(seed_url: str) -> str:
    path = urlsplit(seed_url).path or "/"
    if path == "/":
        return path
    trimmed = path.rstrip("/")
    last = trimmed.rsplit("/", 1)[-1].lower()
    if last in {"feed", "rss", "atom"} or last.endswith(
        (".xml", ".rss", ".atom")
    ):
        parent = trimmed.rsplit("/", 1)[0]
        return (parent or "") + "/"
    if path.endswith("/") or "." not in last:
        return trimmed + "/"
    parent = trimmed.rsplit("/", 1)[0]
    return (parent or "") + "/"


def _in_scope(url: str, *, host: str, scope: str) -> bool:
    parsed = urlsplit(url)
    return parsed.hostname == host and (parsed.path or "/").startswith(scope)


def _looks_like_listing_url(url: str, *, scope: str) -> bool:
    path = (urlsplit(url).path or "/").rstrip("/") or "/"
    if path == (scope.rstrip("/") or "/"):
        return True
    segments = [segment.lower() for segment in path.split("/") if segment]
    if any(segment in LISTING_PATH_SEGMENTS for segment in segments):
        return True
    if any(
        segment == "page" and index + 1 < len(segments) and segments[index + 1].isdigit()
        for index, segment in enumerate(segments)
    ):
        return True
    return path.lower().endswith((".xml", ".rss", ".atom"))


class BatchDiscovery:
    """Discovers article URLs without coupling to any publisher-specific layout."""

    def __init__(self, safe_client: SafeWebClient | None = None) -> None:
        self._client = safe_client or SafeWebClient()

    async def discover(self, spec: BatchSourceSpec) -> DiscoveryResult:
        host = urlsplit(spec.seed_url).hostname
        if not host:
            raise InvalidInputError("seed_url must include a host")
        scope = _scope_path(spec.seed_url)
        discovered: dict[str, DiscoveredUrl] = {}
        methods: list[str] = []
        warnings: list[str] = []

        if spec.discovery_mode in {"auto", "sitemap"}:
            sitemap_candidates = await self._sitemap_candidates(spec.seed_url)
            sitemap_found = False
            for candidate in sitemap_candidates:
                if urlsplit(candidate).hostname != host:
                    continue
                try:
                    entries = await self._discover_sitemap(
                        candidate, host=host, scope=scope
                    )
                except UrlFetchError:
                    continue
                if entries:
                    sitemap_found = True
                    for entry in entries:
                        discovered.setdefault(entry.url, entry)
            if sitemap_found:
                methods.append("sitemap")
            elif spec.discovery_mode == "sitemap":
                raise InvalidInputError("没有从 Sitemap 中发现可导入的文章 URL")

        if spec.discovery_mode in {"auto", "feed"}:
            feed_candidates = await self._feed_candidates(spec.seed_url)
            feed_found = False
            for candidate in feed_candidates:
                if urlsplit(candidate).hostname != host:
                    continue
                try:
                    entries = await self._discover_feed(
                        candidate, host=host, scope=scope
                    )
                except UrlFetchError:
                    continue
                if entries:
                    feed_found = True
                    for entry in entries:
                        current = discovered.get(entry.url)
                        if current is None:
                            discovered[entry.url] = entry
                        else:
                            discovered[entry.url] = DiscoveredUrl(
                                url=entry.url,
                                published_at=entry.published_at or current.published_at,
                                modified_at=current.modified_at or entry.modified_at,
                                source_kind="sitemap+feed",
                            )
            if feed_found:
                methods.append("feed")
            elif spec.discovery_mode == "feed":
                raise InvalidInputError("没有从 RSS/Atom Feed 中发现可导入的文章 URL")

        if not discovered:
            raise InvalidInputError("没有发现可导入的文章 URL")
        if spec.discovery_mode == "auto" and len(methods) == 1:
            warnings.append(f"自动发现仅找到 {methods[0]} 来源")
        return DiscoveryResult(tuple(methods), tuple(discovered.values()), tuple(warnings))

    async def _sitemap_candidates(self, seed_url: str) -> list[str]:
        parsed = urlsplit(seed_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        scope = _scope_path(seed_url).rstrip("/")
        candidates: list[str] = []
        if parsed.path.lower().endswith(".xml"):
            candidates.append(seed_url)
        try:
            robots = await self._client.fetch(
                f"{origin}/robots.txt",
                accepted_types=ROBOTS_TYPES,
                max_bytes=512 * 1024,
                accept="text/plain",
            )
            for line in robots.text.splitlines():
                match = re.match(r"\s*sitemap\s*:\s*(\S+)", line, re.I)
                if match:
                    candidates.append(urljoin(origin, match.group(1)))
        except UrlFetchError:
            pass
        candidates.extend(
            [
                f"{origin}{scope}/sitemap_index.xml" if scope else f"{origin}/sitemap_index.xml",
                f"{origin}{scope}/sitemap.xml" if scope else f"{origin}/sitemap.xml",
                f"{origin}/sitemap_index.xml",
                f"{origin}/sitemap.xml",
            ]
        )
        return list(dict.fromkeys(candidates))

    async def _feed_candidates(self, seed_url: str) -> list[str]:
        parsed = urlsplit(seed_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        scope = _scope_path(seed_url).rstrip("/")
        candidates: list[str] = []
        if any(word in parsed.path.lower() for word in ("feed", "rss", "atom")):
            candidates.append(seed_url)
        try:
            page = await self._client.fetch(
                seed_url,
                accepted_types=HTML_CONTENT_TYPES,
                max_bytes=DISCOVERY_MAX_BYTES,
                accept="text/html,application/xhtml+xml",
            )
            root = lxml_html.fromstring(page.text)
            for link in root.xpath("//link[@href]"):
                rel = (link.get("rel") or "").lower().split()
                content_type = (link.get("type") or "").lower()
                if "alternate" in rel and content_type in {
                    "application/rss+xml",
                    "application/atom+xml",
                }:
                    candidates.append(urljoin(page.final_url, link.get("href")))
        except (UrlFetchError, ValueError, TypeError):
            pass
        candidates.extend(
            [
                f"{origin}{scope}/feed/" if scope else f"{origin}/feed/",
                f"{origin}/feed/",
                f"{origin}/rss.xml",
                f"{origin}/atom.xml",
            ]
        )
        return list(dict.fromkeys(candidates))

    async def _discover_sitemap(
        self, start_url: str, *, host: str, scope: str
    ) -> list[DiscoveredUrl]:
        pending = [(start_url, 0)]
        visited: set[str] = set()
        result: dict[str, DiscoveredUrl] = {}
        while pending:
            current, depth = pending.pop(0)
            if current in visited or len(visited) >= MAX_SITEMAPS:
                continue
            if urlsplit(current).hostname != host:
                continue
            visited.add(current)
            resource = await self._client.fetch(
                current,
                accepted_types=XML_TYPES,
                max_bytes=DISCOVERY_MAX_BYTES,
                accept="application/xml,text/xml,application/rss+xml",
            )
            if urlsplit(resource.final_url).hostname != host:
                raise UrlFetchError("发现资源重定向到了其他域名")
            try:
                root = ElementTree.fromstring(resource.content)
            except ElementTree.ParseError as exc:
                raise UrlFetchError("Sitemap XML 无法解析") from exc
            root_kind = _local_name(root.tag)
            if root_kind == "sitemapindex":
                if depth >= MAX_SITEMAP_DEPTH:
                    continue
                for node in root:
                    loc = next(
                        (child.text for child in node if _local_name(child.tag) == "loc"),
                        None,
                    )
                    if loc:
                        pending.append((urljoin(resource.final_url, loc.strip()), depth + 1))
            elif root_kind == "urlset":
                for node in root:
                    values = {_local_name(child.tag): child.text for child in node}
                    loc = values.get("loc")
                    if not loc:
                        continue
                    url = urljoin(resource.final_url, loc.strip())
                    if _in_scope(url, host=host, scope=scope) and not _looks_like_listing_url(
                        url, scope=scope
                    ):
                        result[url] = DiscoveredUrl(
                            url=url,
                            modified_at=_parse_date(values.get("lastmod")),
                            source_kind="sitemap",
                        )
                        if len(result) >= MAX_DISCOVERED_URLS:
                            return list(result.values())
            else:
                raise UrlFetchError("该 XML 不是 Sitemap")
        return list(result.values())

    async def _discover_feed(
        self, feed_url: str, *, host: str, scope: str
    ) -> list[DiscoveredUrl]:
        resource = await self._client.fetch(
            feed_url,
            accepted_types=XML_TYPES,
            max_bytes=DISCOVERY_MAX_BYTES,
            accept="application/rss+xml,application/atom+xml,application/xml,text/xml",
        )
        if urlsplit(resource.final_url).hostname != host:
            raise UrlFetchError("Feed 重定向到了其他域名")
        try:
            root = ElementTree.fromstring(resource.content)
        except ElementTree.ParseError as exc:
            raise UrlFetchError("Feed XML 无法解析") from exc
        entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
        result: list[DiscoveredUrl] = []
        for entry in entries:
            values: dict[str, str] = {}
            link: str | None = None
            for child in entry:
                name = _local_name(child.tag)
                if child.text:
                    values.setdefault(name, child.text.strip())
                if name == "link":
                    href = child.attrib.get("href")
                    rel = child.attrib.get("rel", "alternate")
                    if href and rel in {"alternate", ""}:
                        link = href
                    elif child.text and link is None:
                        link = child.text.strip()
            link = link or values.get("link") or values.get("guid")
            if not link:
                continue
            url = urljoin(resource.final_url, link)
            if not _in_scope(url, host=host, scope=scope) or _looks_like_listing_url(
                url, scope=scope
            ):
                continue
            result.append(
                DiscoveredUrl(
                    url=url,
                    published_at=_parse_date(
                        values.get("pubdate") or values.get("published") or values.get("date")
                    ),
                    modified_at=_parse_date(values.get("updated")),
                    source_kind="feed",
                )
            )
        return result


def _cutoff(spec: BatchSourceSpec) -> datetime | None:
    if spec.date_from is not None:
        return _parse_date(spec.date_from.isoformat())
    if spec.years is not None:
        return datetime.now(UTC) - timedelta(days=365 * spec.years)
    return None


def _eligible(item: DiscoveredUrl, spec: BatchSourceSpec) -> bool:
    cutoff = _cutoff(spec)
    dates = [date for date in (item.published_at, item.modified_at) if date is not None]
    if not dates:
        return spec.include_unknown_dates
    if cutoff is None:
        return True
    return any(date >= cutoff for date in dates)


def _date_status(item: DiscoveredUrl) -> str:
    if item.published_at is not None:
        return "published"
    if item.modified_at is not None:
        return "modified"
    return "unknown"


def _run_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "counts": {
            "discovered": row["discovered_count"],
            "queued": row["queued_count"],
            "processing": row["processing_count"],
            "succeeded": row["completed_count"],
            "skipped": row["skipped_count"],
            "failed": row["failed_count"],
            "cancelled": row["cancelled_count"],
        },
        "warnings": row["warnings"] or [],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


class BatchIngestionService:
    def __init__(
        self, session: AsyncSession, discovery: BatchDiscovery | None = None
    ) -> None:
        self._session = session
        self._discovery = discovery or BatchDiscovery()

    async def _prepare(
        self, spec: BatchSourceSpec
    ) -> tuple[DiscoveryResult, list[DiscoveredUrl], list[DiscoveredUrl]]:
        if spec.rights_confirmed is not True:
            raise InvalidInputError("rights_confirmed must be true")
        result = await self._discovery.discover(spec)
        eligible = [item for item in result.discovered if _eligible(item, spec)]
        eligible.sort(
            key=lambda item: max(
                (date for date in (item.published_at, item.modified_at) if date is not None),
                default=datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        return result, eligible, eligible[: spec.max_articles]

    async def preview(self, spec: BatchSourceSpec) -> dict[str, Any]:
        result, eligible, selected = await self._prepare(spec)
        return {
            "discovered_total": len(result.discovered),
            "eligible_total": len(eligible),
            "unknown_date_total": sum(
                item.published_at is None and item.modified_at is None
                for item in result.discovered
            ),
            "selected_total": len(selected),
            "sample_items": [
                {
                    "url": item.url,
                    "title": None,
                    "published_at": item.published_at,
                    "modified_at": item.modified_at,
                    "date_status": _date_status(item),
                }
                for item in selected[:20]
            ],
            "warnings": list(result.warnings),
            "discovery_mode": spec.discovery_mode,
        }

    async def start(self, spec: BatchSourceSpec) -> dict[str, Any]:
        result, _, selected = await self._prepare(spec)
        parsed = urlsplit(spec.seed_url)
        run_status = "queued" if selected else "completed"
        async with self._session.begin():
            source_id = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.ingestion_sources
                            (seed_url, source_name, domain, discovery_mode, spec)
                        VALUES (:seed_url, :source_name, :domain, :mode, CAST(:spec AS jsonb))
                        ON CONFLICT (seed_url, source_name) DO UPDATE SET
                            discovery_mode = EXCLUDED.discovery_mode,
                            spec = EXCLUDED.spec,
                            status = 'active'
                        RETURNING id
                        """
                    ),
                    {
                        "seed_url": spec.seed_url,
                        "source_name": spec.source_name,
                        "domain": parsed.hostname,
                        "mode": spec.discovery_mode,
                        "spec": json.dumps(spec.model_dump(mode="json")),
                    },
                )
            ).scalar_one()
            run = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.sync_runs
                            (ingestion_source_id, status, spec, discovered_count,
                             queued_count, warnings, finished_at)
                        VALUES (:source_id, :status, CAST(:spec AS jsonb), :discovered,
                                :queued, CAST(:warnings AS jsonb),
                                CASE WHEN :status = 'completed' THEN now() END)
                        RETURNING id
                        """
                    ),
                    {
                        "source_id": source_id,
                        "status": run_status,
                        "spec": json.dumps(spec.model_dump(mode="json")),
                        "discovered": len(result.discovered),
                        "queued": len(selected),
                        "warnings": json.dumps(list(result.warnings)),
                    },
                )
            ).scalar_one()
            for item in selected:
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.crawl_items
                            (run_id, url, published_at, modified_at, source_kind)
                        VALUES (:run_id, :url, :published_at, :modified_at, :source_kind)
                        ON CONFLICT (run_id, url) DO NOTHING
                        """
                    ),
                    {
                        "run_id": run,
                        "url": item.url,
                        "published_at": item.published_at,
                        "modified_at": item.modified_at,
                        "source_kind": item.source_kind,
                    },
                )
        return await self.status(run)

    async def status(self, run_id: UUID) -> dict[str, Any]:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT r.*,
                      (SELECT count(*) FROM knowledge.crawl_items i WHERE i.run_id=r.id AND i.status IN ('queued','retry')) AS queued_count_live,
                      (SELECT count(*) FROM knowledge.crawl_items i WHERE i.run_id=r.id AND i.status='processing') AS processing_count_live,
                      (SELECT count(*) FROM knowledge.crawl_items i WHERE i.run_id=r.id AND i.status='completed') AS completed_count_live,
                      (SELECT count(*) FROM knowledge.crawl_items i WHERE i.run_id=r.id AND i.status='failed') AS failed_count_live,
                      (SELECT count(*) FROM knowledge.crawl_items i WHERE i.run_id=r.id AND i.status='skipped') AS skipped_count_live,
                      (SELECT count(*) FROM knowledge.crawl_items i WHERE i.run_id=r.id AND i.status='cancelled') AS cancelled_count_live
                    FROM knowledge.sync_runs r WHERE r.id=:run_id
                    """
                ),
                {"run_id": run_id},
            )
        ).mappings().first()
        if row is None:
            raise NotFoundError("batch run not found")
        values = dict(row)
        for name in ("queued", "processing", "completed", "failed", "skipped", "cancelled"):
            values[f"{name}_count"] = values[f"{name}_count_live"]
        return _run_from_row(values)

    async def items(self, run_id: UUID) -> dict[str, Any]:
        exists = (
            await self._session.execute(
                text("SELECT 1 FROM knowledge.sync_runs WHERE id=:id"), {"id": run_id}
            )
        ).scalar_one_or_none()
        if exists is None:
            raise NotFoundError("batch run not found")
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT id, url, published_at, modified_at, source_kind, status,
                           attempts, document_id, error, next_attempt_at, updated_at
                    FROM knowledge.crawl_items WHERE run_id=:run_id
                    ORDER BY created_at, id
                    """
                ),
                {"run_id": run_id},
            )
        ).mappings().all()
        return {"items": [dict(row) for row in rows]}

    async def cancel(self, run_id: UUID) -> dict[str, Any]:
        async with self._session.begin():
            changed = (
                await self._session.execute(
                    text(
                        """
                        UPDATE knowledge.sync_runs SET cancel_requested=true,
                            status=CASE WHEN status IN ('queued','running') THEN 'cancelling' ELSE status END
                        WHERE id=:id RETURNING id
                        """
                    ),
                    {"id": run_id},
                )
            ).scalar_one_or_none()
            if changed is None:
                raise NotFoundError("batch run not found")
            await self._session.execute(
                text(
                    """
                    UPDATE knowledge.crawl_items SET status='cancelled', error='run cancelled'
                    WHERE run_id=:id AND status IN ('queued','retry')
                    """
                ),
                {"id": run_id},
            )
            await self._finish_run(self._session, run_id)
        return await self.status(run_id)

    @staticmethod
    async def _finish_run(session: AsyncSession, run_id: UUID) -> None:
        await session.execute(
            text(
                """
                UPDATE knowledge.sync_runs r SET
                    status=CASE WHEN r.cancel_requested THEN 'cancelled' ELSE 'completed' END,
                    finished_at=now()
                WHERE r.id=:run_id AND NOT EXISTS (
                    SELECT 1 FROM knowledge.crawl_items i
                    WHERE i.run_id=r.id AND i.status IN ('queued','retry','processing')
                )
                """
            ),
            {"run_id": run_id},
        )


_DEFAULT_EXTRACTOR = object()
_DEFAULT_QUALITY_REVIEWER = object()


class BatchWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        article_fetcher: WebArticleFetcher | None = None,
        extractor: Any = _DEFAULT_EXTRACTOR,
        quality_reviewer: Any = _DEFAULT_QUALITY_REVIEWER,
    ) -> None:
        self._factory = session_factory
        self._fetcher = article_fetcher or WebArticleFetcher()
        self._extractor = extractor
        self._quality_reviewer = quality_reviewer

    async def run_once(self) -> bool:
        lease_token = uuid4()
        async with self._factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.crawl_items SET status='retry', lease_token=NULL,
                            lease_expires_at=NULL, error='worker lease expired'
                        WHERE status='processing' AND lease_expires_at < now()
                        """
                    )
                )
                await session.execute(
                    text(
                        """
                        WITH cancelled_items AS (
                            UPDATE knowledge.crawl_items i
                            SET status='cancelled', error='run cancelled'
                            FROM knowledge.sync_runs run
                            WHERE i.run_id=run.id AND run.cancel_requested
                              AND i.status IN ('queued','retry')
                            RETURNING i.run_id
                        )
                        UPDATE knowledge.sync_runs r SET status='cancelled', finished_at=now()
                        WHERE r.cancel_requested AND r.status='cancelling'
                          AND NOT EXISTS (
                            SELECT 1 FROM knowledge.crawl_items i
                            WHERE i.run_id=r.id AND i.status IN ('queued','retry','processing')
                          )
                        """
                    )
                )
                row = (
                    await session.execute(
                        text(
                            """
                            SELECT i.id, i.run_id, i.url, i.published_at, r.spec
                            FROM knowledge.crawl_items i
                            JOIN knowledge.sync_runs r ON r.id=i.run_id
                            JOIN knowledge.ingestion_sources s ON s.id=r.ingestion_source_id
                            WHERE i.status IN ('queued','retry')
                              AND i.next_attempt_at <= now() AND NOT r.cancel_requested
                            ORDER BY i.created_at, i.id
                            FOR UPDATE OF i SKIP LOCKED LIMIT 1
                            """
                        )
                    )
                ).mappings().first()
                if row is None:
                    return False
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.crawl_items SET status='processing', attempts=attempts+1,
                            lease_token=:lease, lease_expires_at=now()+make_interval(secs=>:seconds),
                            error=NULL WHERE id=:id
                        """
                    ),
                    {"lease": lease_token, "seconds": LEASE_SECONDS, "id": row["id"]},
                )
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.sync_runs SET status='running',
                            started_at=COALESCE(started_at,now()) WHERE id=:run_id
                        """
                    ),
                    {"run_id": row["run_id"]},
                )

        try:
            spec = BatchSourceSpec.model_validate(row["spec"])
            article = await self._fetcher.fetch(row["url"])
            if not _in_scope(
                article.final_url,
                host=urlsplit(spec.seed_url).hostname or "",
                scope=_scope_path(spec.seed_url),
            ):
                raise UrlFetchError("文章重定向超出资料源域名或路径范围")
            request = ImportDocumentRequest(
                source_name=spec.source_name,
                canonical_url=article.final_url,
                title=article.title,
                channel=spec.channel,
                content_type="article",
                language_code=spec.language_code or article.language_code or "und",
                market=spec.market,
                author=article.author,
                published_at=article.published_at or row["published_at"],
                raw_content=article.raw_content,
                rights_confirmed=True,
            )
            async with self._factory() as import_session:
                service_kwargs: dict[str, Any] = {}
                if self._extractor is not _DEFAULT_EXTRACTOR:
                    service_kwargs["extractor"] = self._extractor
                if self._quality_reviewer is not _DEFAULT_QUALITY_REVIEWER:
                    service_kwargs["quality_reviewer"] = self._quality_reviewer
                service = KnowledgeService(import_session, **service_kwargs)
                imported = await service.import_document(
                    request,
                    allow_revision=True,
                    require_ai=True,
                    require_quality=True,
                )
            await self._mark_success(row["id"], row["run_id"], imported["document"]["id"])
        except (AIExtractionError, QualityReviewError, UrlFetchError, ValidationError) as exc:
            await self._mark_failure(row["id"], row["run_id"], str(exc))
        except Exception as exc:
            await self._mark_failure(row["id"], row["run_id"], "temporary processing failure")
        return True

    async def _mark_success(self, item_id: UUID, run_id: UUID, document_id: UUID) -> None:
        async with self._factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.crawl_items SET status='completed', document_id=:document_id,
                            lease_token=NULL, lease_expires_at=NULL, error=NULL WHERE id=:id
                        """
                    ),
                    {"id": item_id, "document_id": document_id},
                )
                await BatchIngestionService._finish_run(session, run_id)

    async def _mark_failure(self, item_id: UUID, run_id: UUID, message: str) -> None:
        async with self._factory() as session:
            async with session.begin():
                attempts = (
                    await session.execute(
                        text("SELECT attempts FROM knowledge.crawl_items WHERE id=:id"),
                        {"id": item_id},
                    )
                ).scalar_one()
                status = "failed" if attempts >= MAX_ATTEMPTS else "retry"
                delay = min(300, 30 * (2 ** max(attempts - 1, 0)))
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.crawl_items SET status=:status, error=:error,
                            next_attempt_at=now()+make_interval(secs=>:delay),
                            lease_token=NULL, lease_expires_at=NULL WHERE id=:id
                        """
                    ),
                    {"id": item_id, "status": status, "error": message[:2000], "delay": delay},
                )
                await BatchIngestionService._finish_run(session, run_id)

    async def run_forever(self) -> None:
        while True:
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                worked = False
            await asyncio.sleep(0.2 if worked else 2.0)
