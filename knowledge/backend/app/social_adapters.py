from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import quote_plus, urljoin, urlsplit

from .config import get_settings
from .schemas import SignalCrawlJobIn


@dataclass(frozen=True, slots=True)
class AdapterSignal:
    external_id: str
    canonical_url: str
    content_kind: str
    title: str | None
    content: str
    author_handle: str | None
    published_at: datetime | None
    metadata: dict[str, Any]
    thread_key: str | None = None
    parent_external_id: str | None = None
    engagement: dict[str, Any] | None = None
    language_code: str = "en"


@dataclass(frozen=True, slots=True)
class AdapterResult:
    searches: int
    urls: tuple[str, ...]
    warnings: tuple[str, ...]
    signals: tuple[AdapterSignal, ...] = ()


@dataclass(frozen=True, slots=True)
class _VideoRef:
    url: str
    title: str | None
    keyword: str


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _iso_timestamp(value: Any) -> datetime | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _proxy_for_requests() -> str | None:
    proxy = get_settings().web_proxy_url
    if proxy and proxy.startswith("socks5://"):
        return "socks5h://" + proxy.removeprefix("socks5://")
    return proxy


def _proxy_for_browser() -> str | None:
    proxy = get_settings().web_proxy_url
    if proxy and proxy.startswith("socks5h://"):
        return "socks5://" + proxy.removeprefix("socks5h://")
    return proxy


def _browser_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "max_requests_per_crawl": 1,
        "max_request_retries": 1,
        "ignore_http_error_status_codes": [403],
        "headless": True,
        "browser_new_context_options": {"user_agent": "Mozilla/5.0"},
    }
    channel = get_settings().browser_channel
    proxy = _proxy_for_browser()
    launch: dict[str, Any] = {}
    if channel:
        launch["channel"] = channel
    if proxy:
        launch["proxy"] = {"server": proxy}
    if launch:
        options["browser_launch_options"] = launch
    return options


async def _browser_extract(
    url: str, extractor: Callable[[Any], Any]
) -> Any:
    from crawlee.crawlers import PlaywrightCrawler

    result: Any = None
    crawler = PlaywrightCrawler(**_browser_options())

    @crawler.router.default_handler
    async def handle(context: Any) -> None:
        nonlocal result
        result = await extractor(context.page)

    await crawler.run([url])
    return result


def _reddit_posts_sync(job: SignalCrawlJobIn) -> tuple[list[dict[str, Any]], list[str], int]:
    from scrapi_reddit import build_search_target, build_session, extract_links, fetch_json

    session = build_session("LocalKnowledgeImporter/0.1", verify=True)
    proxy = _proxy_for_requests()
    if proxy:
        session.trust_env = False
        session.proxies.update({"http": proxy, "https": proxy})

    posts: list[dict[str, Any]] = []
    warnings: list[str] = []
    searches = 0
    seen: set[str] = set()
    try:
        for keyword in job.keywords:
            after: str | None = None
            for _page in range(job.max_pages):
                target = build_search_target(
                    keyword,
                    search_types=["post"],
                    sort="new",
                    time_filter="all",
                    limit=min(job.max_signals, 100),
                    after=after,
                )
                try:
                    listing = fetch_json(
                        session, target.url, params=target.params, retries=2, backoff=0.5
                    )
                except Exception as exc:
                    warnings.append(f"关键词「{keyword}」搜索失败：{str(exc)[:300]}")
                    break
                searches += 1
                for post in extract_links(listing):
                    post_id = _clean(post.get("id"))
                    if not post_id or post_id in seen:
                        continue
                    seen.add(post_id)
                    post["_keyword"] = keyword
                    posts.append(post)
                    if len(posts) >= job.max_signals:
                        return posts, warnings, searches
                after = _clean(listing.get("data", {}).get("after")) or None
                if not after:
                    break
    finally:
        session.close()
    return posts, warnings, searches


def _reddit_collect_sync(job: SignalCrawlJobIn) -> tuple[list[AdapterSignal], list[str], int]:
    from scrapi_reddit import build_search_target, build_session, extract_links, fetch_json, flatten_comments

    session = build_session("LocalKnowledgeImporter/0.1", verify=True)
    proxy = _proxy_for_requests()
    if proxy:
        session.trust_env = False
        session.proxies.update({"http": proxy, "https": proxy})

    signals: list[AdapterSignal] = []
    warnings: list[str] = []
    searches = 0
    seen_posts: set[str] = set()
    seen_signals: set[str] = set()

    try:
        for keyword in job.keywords:
            after: str | None = None
            for _page in range(job.max_pages):
                target = build_search_target(
                    keyword,
                    search_types=["post"],
                    sort="new",
                    time_filter="all",
                    limit=min(job.max_signals, 100),
                    after=after,
                )
                try:
                    listing = fetch_json(
                        session, target.url, params=target.params, retries=2, backoff=0.5
                    )
                except Exception as exc:
                    warnings.append(f"关键词「{keyword}」搜索失败：{str(exc)[:300]}")
                    break
                searches += 1
                for post in extract_links(listing):
                    post_id = _clean(post.get("id"))
                    if not post_id or post_id in seen_posts:
                        continue
                    seen_posts.add(post_id)
                    permalink = _clean(post.get("permalink"))
                    canonical_url = _clean(post.get("post_url")) or f"https://www.reddit.com{permalink}"
                    title = _clean(post.get("title")) or None
                    body = _clean(post.get("selftext"))
                    content = "\n\n".join(value for value in (title, body) if value)
                    if content and post_id not in seen_signals:
                        seen_signals.add(post_id)
                        signals.append(
                            AdapterSignal(
                                external_id=post_id,
                                canonical_url=canonical_url,
                                content_kind="post",
                                title=title,
                                content=content,
                                author_handle=_clean(post.get("author")) or None,
                                published_at=_timestamp(post.get("created_utc")),
                                thread_key=post_id,
                                engagement={
                                    "score": post.get("score"),
                                    "upvote_ratio": post.get("upvote_ratio"),
                                    "comment_count": post.get("num_comments"),
                                },
                                metadata={
                                    "collector": "scrapi-reddit",
                                    "keyword": keyword,
                                    "subreddit": post.get("subreddit"),
                                },
                            )
                        )
                    if len(signals) >= job.max_signals:
                        return signals, warnings, searches

                    try:
                        post_json = fetch_json(
                            session,
                            f"https://www.reddit.com{permalink}.json",
                            params={"raw_json": 1, "limit": 500},
                            retries=2,
                            backoff=0.5,
                        )
                    except Exception as exc:
                        warnings.append(f"帖子 {post_id} 评论获取失败：{str(exc)[:300]}")
                        continue
                    comments = flatten_comments(
                        post_json,
                        {
                            "post_id": post_id,
                            "title": title,
                            "subreddit": post.get("subreddit"),
                        },
                    )
                    for comment in comments:
                        comment_id = _clean(comment.get("comment_id"))
                        body = _clean(comment.get("body"))
                        if not comment_id or not body or comment_id in seen_signals:
                            continue
                        seen_signals.add(comment_id)
                        parent_id = _clean(comment.get("parent_id")) or None
                        signals.append(
                            AdapterSignal(
                                external_id=comment_id,
                                canonical_url=_clean(comment.get("permalink")) or canonical_url,
                                content_kind="reply" if parent_id and parent_id.startswith("t1_") else "comment",
                                title=title,
                                content=body,
                                author_handle=_clean(comment.get("author")) or None,
                                published_at=_timestamp(comment.get("created_utc")),
                                thread_key=post_id,
                                parent_external_id=parent_id,
                                engagement={"score": comment.get("score"), "depth": comment.get("depth")},
                                metadata={
                                    "collector": "scrapi-reddit",
                                    "keyword": keyword,
                                    "subreddit": comment.get("subreddit"),
                                },
                            )
                        )
                        if len(signals) >= job.max_signals:
                            return signals, warnings, searches
                after = _clean(listing.get("data", {}).get("after")) or None
                if not after:
                    break
    finally:
        session.close()
    return signals, warnings, searches


async def _discover_youtube(job: SignalCrawlJobIn) -> tuple[list[_VideoRef], list[str], int]:
    from crawlee.crawlers import PlaywrightCrawler

    refs: list[_VideoRef] = []
    warnings: list[str] = []
    searches = 0
    seen: set[str] = set()

    for keyword in job.keywords:
        for page_number in range(1, job.max_pages + 1):
            search_url = job.search_url_template.replace("{query}", quote_plus(keyword)).replace(
                "{page}", str(page_number)
            )
            found: list[dict[str, str | None]] = []
            crawler = PlaywrightCrawler(**_browser_options())

            @crawler.router.default_handler
            async def handle(context: Any) -> None:
                locator = context.page.locator("a#video-title")
                if await locator.count() == 0:
                    locator = context.page.locator("a[href*='/watch?v=']")
                found.extend(
                    await locator.evaluate_all(
                        """(elements) => elements.map((element) => ({
                            href: element.href || element.getAttribute('href'),
                            title: element.getAttribute('title') || element.textContent
                        }))"""
                    )
                )

            try:
                await crawler.run([search_url])
            except Exception as exc:
                warnings.append(f"YouTube 搜索「{keyword}」失败：{str(exc)[:300]}")
                continue
            searches += 1
            for item in found:
                url = _clean(item.get("href"))
                if not url or "/watch" not in url or "v=" not in url:
                    continue
                parsed = urlsplit(url)
                canonical = f"https://www.youtube.com{parsed.path}?{parsed.query}"
                if canonical in seen:
                    continue
                seen.add(canonical)
                refs.append(_VideoRef(canonical, _clean(item.get("title")) or None, keyword))
                if len(refs) >= job.max_signals:
                    return refs, warnings, searches
            if len(refs) >= job.max_signals:
                break
    if not refs:
        warnings.append("没有从 YouTube 动态搜索结果中发现视频链接")
    return refs, warnings, searches


async def _discover_reddit(job: SignalCrawlJobIn) -> tuple[list[str], list[str], int]:
    urls: list[str] = []
    warnings: list[str] = []
    searches = 0
    seen: set[str] = set()
    for keyword in job.keywords:
        for page_number in range(1, job.max_pages + 1):
            search_url = job.search_url_template.replace("{query}", quote_plus(keyword)).replace(
                "{page}", str(page_number)
            )

            async def extract(page: Any) -> list[dict[str, str | None]]:
                await page.wait_for_timeout(5000)
                return await page.locator(
                    "a[data-testid='post-title'], a[data-testid='post-title-text'], "
                    "shreddit-post a[href*='/comments/'], a[href*='/comments/']"
                ).evaluate_all(
                    """(elements) => elements.map((element) => ({
                        href: element.href || element.getAttribute('href'),
                        title: element.getAttribute('aria-label') || element.textContent
                    }))"""
                )

            try:
                found = await _browser_extract(search_url, extract)
            except Exception as exc:
                warnings.append(f"Reddit 搜索「{keyword}」失败：{str(exc)[:300]}")
                continue
            searches += 1
            for item in found or []:
                url = _clean(item.get("href"))
                if not url or "/comments/" not in url or url in seen:
                    continue
                seen.add(url)
                urls.append(url)
                if len(urls) >= job.max_signals:
                    return urls, warnings, searches
            if len(urls) >= job.max_signals:
                break
    if not urls:
        warnings.append("没有从 Reddit 浏览器搜索结果中发现帖子链接")
    return urls, warnings, searches


def _reddit_comment_content(value: str) -> str:
    lines = [_clean(line) for line in value.splitlines() if _clean(line)]
    if len(lines) <= 3:
        return " ".join(lines)
    body = lines[3:]
    body = [line for line in body if not re.fullmatch(r"\d+ more repl(?:y|ies)", line, re.I)]
    return " ".join(body).strip()


async def _reddit_detail(url: str, *, limit: int) -> tuple[list[AdapterSignal], str | None]:
    async def extract(page: Any) -> dict[str, Any]:
        title_locator = page.locator("[data-testid='post-title-text'], h1").first
        title = _clean(await title_locator.text_content()) if await title_locator.count() else ""
        comments = await page.locator("shreddit-comment").evaluate_all(
            """(elements) => elements.slice(0, 500).map((element) => ({
                id: element.getAttribute('thingid'),
                parent_id: element.getAttribute('parentid'),
                author: element.getAttribute('author'),
                created: element.getAttribute('created'),
                score: element.getAttribute('score'),
                permalink: element.getAttribute('permalink'),
                text: element.innerText || ''
            }))"""
        )
        return {"title": title, "comments": comments}

    try:
        data = await _browser_extract(url, extract)
    except Exception as exc:
        return [], str(exc)[:300]
    post_match = re.search(r"/comments/([a-z0-9]+)/", url, re.I)
    post_id = f"t3_{post_match.group(1)}" if post_match else url
    title = _clean(data.get("title")) or None
    signals = [
        AdapterSignal(
            external_id=post_id,
            canonical_url=url,
            content_kind="post",
            title=title,
            content=title or url,
            author_handle=None,
            published_at=None,
            thread_key=post_id,
            metadata={"collector": "crawlee-playwright"},
        )
    ]
    for comment in data.get("comments", [])[: max(0, limit - 1)]:
        comment_id = _clean(comment.get("id"))
        content = _reddit_comment_content(str(comment.get("text") or ""))
        if not comment_id or not content:
            continue
        parent_id = _clean(comment.get("parent_id")) or None
        permalink = _clean(comment.get("permalink"))
        signals.append(
            AdapterSignal(
                external_id=comment_id,
                canonical_url=urljoin("https://www.reddit.com", permalink) if permalink else url,
                content_kind="reply" if parent_id else "comment",
                title=title,
                content=content,
                author_handle=_clean(comment.get("author")) or None,
                published_at=_iso_timestamp(comment.get("created")),
                thread_key=post_id,
                parent_external_id=parent_id,
                engagement={"score": comment.get("score")},
                metadata={"collector": "crawlee-playwright"},
            )
        )
    return signals, None


async def _reddit_browser_collect(job: SignalCrawlJobIn) -> AdapterResult:
    urls, warnings, searches = await _discover_reddit(job)
    signals: list[AdapterSignal] = []
    for url in urls:
        remaining = job.max_signals - len(signals)
        if remaining <= 0:
            break
        detail_signals, error = await _reddit_detail(url, limit=remaining)
        if error:
            warnings.append(f"帖子页面采集失败：{error}")
            continue
        for item in detail_signals:
            signals.append(
                AdapterSignal(
                    external_id=item.external_id,
                    canonical_url=item.canonical_url,
                    content_kind=item.content_kind,
                    title=item.title,
                    content=item.content,
                    author_handle=item.author_handle,
                    published_at=item.published_at,
                    metadata={**item.metadata, "keyword": " | ".join(job.keywords)},
                    thread_key=item.thread_key,
                    parent_external_id=item.parent_external_id,
                    engagement=item.engagement,
                    language_code=item.language_code,
                )
            )
            if len(signals) >= job.max_signals:
                break
    return AdapterResult(searches, tuple(urls), tuple(warnings), tuple(signals))


def _youtube_comments_sync(
    ref: _VideoRef, *, limit: int, thread_key: str
) -> tuple[list[AdapterSignal], str | None]:
    from youtube_comment_downloader import YoutubeCommentDownloader

    downloader = YoutubeCommentDownloader()
    proxy = _proxy_for_requests()
    if proxy:
        downloader.session.trust_env = False
        downloader.session.proxies.update({"http": proxy, "https": proxy})
    try:
        comments: list[AdapterSignal] = []
        for comment in downloader.get_comments_from_url(ref.url, sleep=0.1):
            comment_id = _clean(comment.get("cid"))
            content = _clean(comment.get("text"))
            if not comment_id or not content:
                continue
            comments.append(
                AdapterSignal(
                    external_id=comment_id,
                    canonical_url=f"{ref.url}&lc={comment_id}",
                    content_kind="reply" if comment.get("reply") else "video_comment",
                    title=ref.title,
                    content=content,
                    author_handle=_clean(comment.get("author")) or None,
                    published_at=_timestamp(comment.get("time_parsed")),
                    thread_key=thread_key,
                    engagement={
                        "votes": comment.get("votes"),
                        "replies": comment.get("replies"),
                        "hearted": bool(comment.get("heart")),
                    },
                    metadata={"collector": "youtube-comment-downloader", "keyword": ref.keyword},
                )
            )
            if len(comments) >= limit:
                break
        return comments, None
    except Exception as exc:
        return [], str(exc)[:300]


async def _youtube_collect(job: SignalCrawlJobIn) -> AdapterResult:
    refs, warnings, searches = await _discover_youtube(job)
    signals: list[AdapterSignal] = []
    for ref in refs:
        video_id = urlsplit(ref.url).query.split("v=", 1)[-1].split("&", 1)[0]
        if not video_id:
            continue
        signals.append(
            AdapterSignal(
                external_id=video_id,
                canonical_url=ref.url,
                content_kind="post",
                title=ref.title,
                content=ref.title or ref.url,
                author_handle=None,
                published_at=None,
                thread_key=video_id,
                metadata={"collector": "crawlee-playwright", "keyword": ref.keyword},
            )
        )
        remaining = job.max_signals - len(signals)
        if remaining <= 0:
            break
        comments, error = await asyncio.to_thread(
            _youtube_comments_sync, ref, limit=remaining, thread_key=video_id
        )
        if error:
            warnings.append(f"视频 {video_id} 评论获取失败：{error}")
        signals.extend(comments)
        if len(signals) >= job.max_signals:
            break
    return AdapterResult(searches, tuple(ref.url for ref in refs), tuple(warnings), tuple(signals))


class RedditAdapter:
    async def preview(self, job: SignalCrawlJobIn) -> AdapterResult:
        posts, warnings, searches = await asyncio.to_thread(_reddit_posts_sync, job)
        if not posts:
            browser_urls, browser_warnings, browser_searches = await _discover_reddit(job)
            return AdapterResult(
                browser_searches,
                tuple(browser_urls),
                tuple([*warnings, *browser_warnings]),
            )
        return AdapterResult(
            searches,
            tuple(_clean(post.get("post_url")) for post in posts if _clean(post.get("post_url"))),
            tuple(warnings),
        )

    async def collect(self, job: SignalCrawlJobIn) -> AdapterResult:
        signals, warnings, searches = await asyncio.to_thread(_reddit_collect_sync, job)
        if not signals:
            browser_result = await _reddit_browser_collect(job)
            return AdapterResult(
                browser_result.searches,
                browser_result.urls,
                tuple([*warnings, *browser_result.warnings]),
                browser_result.signals,
            )
        urls = tuple(signal.canonical_url for signal in signals if signal.content_kind == "post")
        return AdapterResult(searches, urls, tuple(warnings), tuple(signals))


class YouTubeAdapter:
    async def preview(self, job: SignalCrawlJobIn) -> AdapterResult:
        refs, warnings, searches = await _discover_youtube(job)
        return AdapterResult(searches, tuple(ref.url for ref in refs), tuple(warnings))

    async def collect(self, job: SignalCrawlJobIn) -> AdapterResult:
        return await _youtube_collect(job)


def adapter_for_platform(platform: str) -> RedditAdapter | YouTubeAdapter | None:
    normalized = platform.strip().lower()
    if normalized == "reddit":
        return RedditAdapter()
    if normalized in {"youtube", "youtube_comments"}:
        return YouTubeAdapter()
    return None
