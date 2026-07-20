# Local knowledge backend

This is an independent FastAPI application for the first knowledge-system phase.
It can fetch one public article URL, discover bounded batches through generic
Sitemap and RSS/Atom adapters, run keyword-based public HTML crawl recipes, and
accept platform-neutral market signals such as posts, comments, replies, reviews
and video comments. Public-web requests share
SSRF, redirect, timeout and response-size protections.

AI extraction returns zero to four grounded candidates per document. A separate
document-level quality gate classifies them before human review. Historical claims
use a durable dry-run/apply workflow; dry-run never changes `review_status`, and
only AI quality rejections can be restored through the API. AI never approves a
claim.

Set `KNOWLEDGE_DATABASE_URL` to a `postgresql+asyncpg://...` connection string,
then run from `Y:\`:

```powershell
python -m knowledge.backend.app.main
```

The application listens only on `127.0.0.1:8010`. CORS allows only
`http://127.0.0.1:5174` and `http://localhost:5174` by default. Additional local
origins can be supplied as a comma-separated `KNOWLEDGE_CORS_ORIGINS`; non-local
origins are rejected. Do not expose this unauthenticated first-phase service to a
network.

Business routes use the `/api/v1/knowledge` prefix. OpenAPI is available locally
at `http://127.0.0.1:8010/docs`.

Public signal crawling is configured through `signals/crawl/*`. A crawl job uses a
public search URL template containing `{query}` and optionally `{page}`. Reddit
uses `scrapi-reddit` first and falls back to `crawlee[playwright]` when the
public JSON endpoint is blocked. YouTube uses Crawlee/Playwright for dynamic
search results and `youtube-comment-downloader` for comments. Other sites use
the bounded HTML adapter. All adapters run serially with a delay and daily
interval; they do not bypass login walls, CAPTCHAs, robots rules or rate limits.

Install runtime dependencies from `backend/requirements.txt`. On Windows, set
`KNOWLEDGE_BROWSER_CHANNEL=chrome` if Chrome is installed. Otherwise install a
Playwright browser with `python -m playwright install chromium`.

If the machine needs a proxy to access public websites, set
`KNOWLEDGE_WEB_PROXY=socks5h://127.0.0.1:7897` in the project `.env` and restart the
backend. The crawler intentionally ignores ambient system proxy environment
variables unless this setting is explicitly provided.
