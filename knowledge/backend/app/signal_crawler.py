from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote_plus, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import UUID

import httpx
from lxml import html as lxml_html
from trafilatura import bare_extraction
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .errors import ConflictError, InvalidInputError, NotFoundError, UrlFetchError
from .schemas import ImportSignalsRequest, SignalCrawlJobIn
from .signal_service import MarketSignalService
from .social_adapters import adapter_for_platform
from .web_importer import HTML_CONTENT_TYPES, SafeWebClient, USER_AGENT


MIN_SIGNAL_CHARS = 20
NEXT_RUN_SQL = "now()+make_interval(hours => interval_hours)"


@dataclass(frozen=True, slots=True)
class CrawlPreview:
    searches: int
    urls: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CrawledSignal:
    external_id: str
    canonical_url: str
    content_kind: str
    title: str | None
    content: str
    author_handle: str | None
    published_at: datetime | None
    metadata: dict[str, object]
    thread_key: str | None = None
    parent_external_id: str | None = None
    engagement: dict[str, object] | None = None
    language_code: str = "en"


def _render_search_url(template: str, keyword: str, page: int) -> str:
    return template.replace("{query}", quote_plus(keyword)).replace("{page}", str(page))


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _node_text(node: lxml_html.HtmlElement) -> str:
    return _clean_text(node.text_content())


def _meta(root: lxml_html.HtmlElement, *names: str) -> str | None:
    for name in names:
        values = root.xpath(
            "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')=$name]/@content"
            " | //meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')=$name]/@content",
            name=name.lower(),
        )
        if values and _clean_text(values[0]):
            return _clean_text(values[0])
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _same_host(url: str, host: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == host.lower()


class PublicSignalCrawler:
    """Deep module for bounded, public HTML search and thread extraction."""

    def __init__(self, client: SafeWebClient | None = None) -> None:
        self._client = client or SafeWebClient()
        self._robots: dict[str, RobotFileParser | None] = {}

    async def preview(self, job: SignalCrawlJobIn) -> CrawlPreview:
        adapter = adapter_for_platform(job.platform)
        if adapter is not None:
            result = await adapter.preview(job)
            return CrawlPreview(result.searches, result.urls, result.warnings)
        host = self._job_host(job)
        urls: list[str] = []
        warnings: list[str] = []
        searches = 0
        for keyword in job.keywords:
            for page in range(1, job.max_pages + 1):
                search_url = _render_search_url(job.search_url_template, keyword, page)
                if not await self._allowed(search_url, host, warnings):
                    continue
                resource = await self._client.fetch(
                    search_url,
                    accepted_types=HTML_CONTENT_TYPES,
                    max_bytes=3 * 1024 * 1024,
                    accept="text/html,application/xhtml+xml",
                )
                if not _same_host(resource.final_url, host):
                    raise UrlFetchError("搜索页重定向到了配置之外的域名")
                searches += 1
                urls.extend(self._search_result_urls(resource.text, resource.final_url, job))
                if len(dict.fromkeys(urls)) >= job.max_signals:
                    break
                await asyncio.sleep(job.delay_ms / 1000)
            if len(dict.fromkeys(urls)) >= job.max_signals:
                break
        unique_urls = tuple(dict.fromkeys(urls))[: job.max_signals]
        if not unique_urls:
            warnings.append("没有从公开搜索页发现符合 detail_url_contains 的帖子链接")
        return CrawlPreview(searches, unique_urls, tuple(warnings))

    async def collect(
        self, job: SignalCrawlJobIn
    ) -> tuple[tuple[CrawledSignal, ...], CrawlPreview]:
        adapter = adapter_for_platform(job.platform)
        if adapter is not None:
            result = await adapter.collect(job)
            return (
                tuple(
                    CrawledSignal(
                        external_id=item.external_id,
                        canonical_url=item.canonical_url,
                        content_kind=item.content_kind,
                        title=item.title,
                        content=item.content,
                        author_handle=item.author_handle,
                        published_at=item.published_at,
                        metadata=item.metadata,
                        thread_key=item.thread_key,
                        parent_external_id=item.parent_external_id,
                        engagement=item.engagement,
                        language_code=item.language_code,
                    )
                    for item in result.signals
                ),
                CrawlPreview(result.searches, result.urls, result.warnings),
            )
        preview = await self.preview(job)
        host = self._job_host(job)
        signals: list[CrawledSignal] = []
        seen: set[str] = set()
        for url in preview.urls:
            if not await self._allowed(url, host, list(preview.warnings)):
                continue
            try:
                resource = await self._client.fetch(
                    url,
                    accepted_types=HTML_CONTENT_TYPES,
                    max_bytes=3 * 1024 * 1024,
                    accept="text/html,application/xhtml+xml",
                )
                if not _same_host(resource.final_url, host):
                    continue
                for signal in _extract_signals(
                    resource.text,
                    final_url=resource.final_url,
                    keyword=self._keyword_for_url(job, url),
                    search_url=job.search_url_template,
                ):
                    fingerprint = hashlib.sha256(signal.content.encode("utf-8")).hexdigest()
                    if fingerprint in seen:
                        continue
                    seen.add(fingerprint)
                    signals.append(signal)
                    if len(signals) >= job.max_signals:
                        return tuple(signals), preview
            except UrlFetchError:
                continue
            await asyncio.sleep(job.delay_ms / 1000)
        return tuple(signals), preview

    def _job_host(self, job: SignalCrawlJobIn) -> str:
        rendered = _render_search_url(job.search_url_template, job.keywords[0], 1)
        host = urlsplit(rendered).hostname
        if not host or urlsplit(rendered).scheme not in {"http", "https"}:
            raise InvalidInputError("search_url_template 必须是公开 HTTP(S) 地址")
        return host

    @staticmethod
    def _search_result_urls(
        html: str, base_url: str, job: SignalCrawlJobIn
    ) -> list[str]:
        root = lxml_html.fromstring(html)
        urls: list[str] = []
        host = urlsplit(base_url).hostname or ""
        for href in root.xpath("//a[@href]/@href"):
            absolute = urljoin(base_url, href)
            parsed = urlsplit(absolute)
            if parsed.scheme not in {"http", "https"} or not _same_host(absolute, host):
                continue
            if any(marker.lower() in absolute.lower() for marker in job.detail_url_contains):
                urls.append(absolute)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _keyword_for_url(job: SignalCrawlJobIn, _url: str) -> str:
        return " | ".join(job.keywords)

    async def _allowed(self, url: str, host: str, warnings: list[str]) -> bool:
        if not _same_host(url, host):
            return False
        if host not in self._robots:
            origin = f"{urlsplit(url).scheme}://{host}"
            try:
                resource = await self._client.fetch(
                    f"{origin}/robots.txt",
                    accepted_types={"text/plain"},
                    max_bytes=512 * 1024,
                    accept="text/plain",
                )
                parser = RobotFileParser()
                parser.set_url(f"{origin}/robots.txt")
                parser.parse(resource.text.splitlines())
                self._robots[host] = parser
            except UrlFetchError:
                self._robots[host] = None
                warnings.append(f"{host} 的 robots.txt 不可读取，已保留低速单线程限制")
        parser = self._robots[host]
        return parser is None or parser.can_fetch(USER_AGENT, url)


def _extract_signals(
    html: str, *, final_url: str, keyword: str, search_url: str
) -> tuple[CrawledSignal, ...]:
    root = lxml_html.fromstring(html)
    for node in root.xpath("//script | //style | //noscript | //svg"):
        node.drop_tree()
    title = _meta(root, "og:title", "twitter:title") or _clean_text(
        root.xpath("string(//title)")
    )
    author = _meta(root, "author", "article:author")
    published_at = _parse_date(
        _meta(root, "article:published_time", "datepublished")
        or next(iter(root.xpath("//time[@datetime]/@datetime")), None)
    )
    canonical = next(iter(root.xpath("//link[@rel='canonical']/@href")), None)
    canonical_url = urljoin(final_url, canonical) if canonical else final_url
    nodes = root.xpath(
        "//article | //*[@role='article'] | //*[@itemprop='commentText'] | //shreddit-comment"
    )
    if not nodes:
        nodes = [root]
    signals: list[CrawledSignal] = []
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        content = _node_text(node)
        if len(content) < MIN_SIGNAL_CHARS:
            continue
        fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        node_author = _clean_text(next(iter(node.xpath(".//*[@itemprop='author']//text()")), "")) or author
        node_date = _parse_date(next(iter(node.xpath(".//time[@datetime]/@datetime")), None)) or published_at
        marker = " ".join(
            str(node.get(name, ""))
            for name in ("id", "class", "itemprop", "role")
        ).lower()
        is_comment = "comment" in marker or node.tag.lower() == "shreddit-comment"
        external_id = node.get("id") or f"{fingerprint[:32]}-{index}"
        signals.append(
            CrawledSignal(
                external_id=external_id,
                canonical_url=f"{canonical_url}#{external_id}" if is_comment else canonical_url,
                content_kind="comment" if is_comment else "forum_thread",
                title=title[:500] if title else None,
                content=content[:20000],
                author_handle=node_author[:300] if node_author else None,
                published_at=node_date,
                metadata={
                    "collector": "public_html",
                    "keyword": keyword,
                    "search_url": search_url,
                    "source_url": final_url,
                },
            )
        )
    return tuple(signals)


def _job_from_row(row: dict) -> dict:
    return dict(row)


def _run_from_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "status": row["status"],
        "counts": row["counts"] or {},
        "warnings": row["warnings"] or [],
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


class SignalCrawlService:
    def __init__(self, session: AsyncSession, crawler: PublicSignalCrawler | None = None):
        self._session = session
        self._crawler = crawler or PublicSignalCrawler()

    async def preview(self, job: SignalCrawlJobIn):
        if job.rights_confirmed is not True:
            raise InvalidInputError("rights_confirmed must be true")
        return await self._crawler.preview(job)

    async def create_job(self, job: SignalCrawlJobIn) -> dict:
        if job.rights_confirmed is not True:
            raise InvalidInputError("rights_confirmed must be true")
        async with self._session.begin():
            row = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.market_signal_crawl_jobs
                            (name, source_name, platform, channel, rights_confirmed,
                             search_url_template, keywords, detail_url_contains,
                             max_pages, max_signals, delay_ms, interval_hours, enabled,
                             next_run_at)
                        VALUES (:name, :source_name, :platform, :channel, :rights_confirmed,
                                :template, CAST(:keywords AS jsonb), CAST(:markers AS jsonb),
                                :max_pages, :max_signals, :delay_ms, :interval_hours,
                                :enabled, CASE WHEN :enabled THEN now() END)
                        RETURNING *
                        """
                    ),
                    {
                        **job.model_dump(mode="json"),
                        "template": job.search_url_template,
                        "keywords": json.dumps(job.keywords),
                        "markers": json.dumps(job.detail_url_contains),
                    },
                )
            ).mappings().one()
        return _job_from_row(dict(row))

    async def list_jobs(self) -> dict:
        rows = (
            await self._session.execute(
                text("SELECT * FROM knowledge.market_signal_crawl_jobs ORDER BY created_at DESC")
            )
        ).mappings().all()
        return {"items": [dict(row) for row in rows]}

    async def run_job(self, job_id: UUID) -> dict:
        row = (
            await self._session.execute(
                text("SELECT * FROM knowledge.market_signal_crawl_jobs WHERE id=:id"),
                {"id": job_id},
            )
        ).mappings().first()
        if row is None:
            raise NotFoundError("signal crawl job not found")
        await self._session.rollback()
        job = SignalCrawlJobIn.model_validate(
            {key: row[key] for key in SignalCrawlJobIn.model_fields if key in row}
        )
        async with self._session.begin():
            run = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.market_signal_crawl_runs(job_id, status, started_at)
                        VALUES (:job_id, 'running', now()) RETURNING *
                        """
                    ),
                    {"job_id": job_id},
                )
            ).mappings().one()
        try:
            signals, preview = await self._crawler.collect(job)
            imported = {"created": 0, "duplicates": 0}
            if signals:
                request = ImportSignalsRequest(
                    source_name=job.source_name,
                    platform=job.platform,
                    channel=job.channel,
                    rights_confirmed=True,
                    signals=[
                        {
                            "external_id": item.external_id,
                            "canonical_url": item.canonical_url,
                            "content_kind": item.content_kind,
                            "title": item.title,
                            "content": item.content,
                            "author_handle": item.author_handle,
                            "thread_key": item.thread_key,
                            "parent_external_id": item.parent_external_id,
                            "language_code": item.language_code,
                            "published_at": item.published_at,
                            "engagement": item.engagement or {},
                            "metadata": item.metadata,
                        }
                        for item in signals
                    ],
                )
                imported = await MarketSignalService(self._session).import_signals(request)
            counts = {
                "searches": preview.searches,
                "urls": len(preview.urls),
                "signals": len(signals),
                "created": imported["created"],
                "duplicates": imported["duplicates"],
            }
            warnings = list(preview.warnings)
            async with self._session.begin():
                updated = (
                    await self._session.execute(
                        text(
                            """
                            UPDATE knowledge.market_signal_crawl_runs
                            SET status='completed', counts=CAST(:counts AS jsonb),
                                warnings=CAST(:warnings AS jsonb), finished_at=now()
                            WHERE id=:id RETURNING *
                            """
                        ),
                        {"id": run["id"], "counts": json.dumps(counts), "warnings": json.dumps(warnings)},
                    )
                ).mappings().one()
                await self._session.execute(
                    text(f"UPDATE knowledge.market_signal_crawl_jobs SET last_run_at=now(), last_error=NULL, next_run_at={NEXT_RUN_SQL} WHERE id=:id"),
                    {"id": job_id},
                )
            return _run_from_row(dict(updated))
        except Exception as exc:
            await self._session.rollback()
            async with self._session.begin():
                failed = (
                    await self._session.execute(
                        text("UPDATE knowledge.market_signal_crawl_runs SET status='failed', error=:error, finished_at=now() WHERE id=:id RETURNING *"),
                        {"id": run["id"], "error": str(exc)[:2000]},
                    )
                ).mappings().one()
                await self._session.execute(
                        text(f"UPDATE knowledge.market_signal_crawl_jobs SET last_run_at=now(), last_error=:error, next_run_at={NEXT_RUN_SQL} WHERE id=:id"),
                    {"id": job_id, "error": str(exc)[:2000]},
                )
            return _run_from_row(dict(failed))

    async def run_status(self, run_id: UUID) -> dict:
        row = (
            await self._session.execute(
                text("SELECT * FROM knowledge.market_signal_crawl_runs WHERE id=:id"),
                {"id": run_id},
            )
        ).mappings().first()
        if row is None:
            raise NotFoundError("signal crawl run not found")
        return _run_from_row(dict(row))


class SignalCrawlWorker:
    def __init__(self, factory: async_sessionmaker[AsyncSession]):
        self._factory = factory

    async def run_once(self) -> bool:
        async with self._factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        text(
                            """
                            SELECT id FROM knowledge.market_signal_crawl_jobs
                            WHERE enabled AND rights_confirmed AND next_run_at <= now()
                            ORDER BY next_run_at, created_at FOR UPDATE SKIP LOCKED LIMIT 1
                            """
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return False
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.market_signal_crawl_jobs
                        SET next_run_at={NEXT_RUN_SQL}
                        WHERE id=:id
                        """
                    ),
                    {"id": row},
                )
        async with self._factory() as session:
            await SignalCrawlService(session).run_job(row)
        return True

    async def run_forever(self) -> None:
        while True:
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                worked = False
            await asyncio.sleep(30 if worked else 60)
