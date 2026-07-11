"""v1 业务路由：完全复用 Node.js 路由的请求/响应体结构。

- GET/POST /api/v1/workflow/standard        配置标准读写
- POST /api/v1/workflow/standard/reload     重载
- GET  /api/v1/workflow/sample              样例
- POST /api/v1/workflow/analyze             分析关键词
- POST /api/v1/workflow/import-csv          CSV 导入
- POST /api/v1/workflow/import-file         文件导入
- POST /api/v1/workflow/brief               Brief（含 AI 增强，回退本地）
- POST /api/v1/workflow/prompt              Prompt
- POST /api/v1/workflow/mock-article        Mock Article
- POST /api/v1/workflow/project-package     项目包
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.engine.content_plan import (
    article_brief_template_for,
    image_plan_for,
    reference_plan,
)
from app.engine.locale import locale_for_project
from app.engine.loader import get_store
from app.services.brief_service import build_brief_with_optional_ai
from app.services.executor import upsert_keywords, upsert_sites
from app.services.keyword_service import (
    analyze_keywords,
    build_brief,
    import_and_analyze_csv,
    import_and_analyze_file,
)
from app.services.keyword_ai_service import analyze_keyword_strategy
from app.clients.ai_provider import generate_ai_content, is_stage_configured
from app.services.article_generation_service import generate_article_from_keyword, generate_article_pipeline

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------- 标准配置读写 ----------

class StandardBody(BaseModel):
    standard: dict[str, Any] | None = None


@router.get("/workflow/standard")
async def get_standard() -> dict[str, Any]:
    store = get_store()
    return {"standard": store.payload, "version": store.version}


@router.post("/workflow/standard/reload")
async def reload_standard() -> dict[str, Any]:
    store = get_store()
    await store.load_from_db()
    return {"standard": store.payload, "version": store.version, "reloaded": True}


@router.post("/workflow/standard")
async def save_standard(body: StandardBody) -> dict[str, Any]:
    """本接口只把 payload 写回内存 + 文件占位（v1 文件不变），

    真正的 DB 写库走 /api/v1/rule-sets（v2）。"""
    if not body.standard:
        return {"error": "missing standard body"}
    # 同步到文件（与 Node.js 兼容）
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    target = repo_root / "workflows" / "seo-standard.json"
    target.write_text(__import__("json").dumps(body.standard, ensure_ascii=False, indent=2), encoding="utf-8")
    store = get_store()
    # 重新从文件读一遍（注意：这里不读 DB，是为了和 Node.js 行为一致；v2 走 DB 路径）
    store._payload = body.standard  # noqa: SLF001 同步内存（admin/reload 会从 PG 再覆一次）
    store._version = body.standard.get("version", store.version)  # noqa: SLF001
    return {"standard": store.payload, "version": store.version, "saved": True}


@router.get("/workflow/sample")
async def get_sample() -> dict[str, Any]:
    store = get_store()
    return {
        "project": store.get("positioning.defaultProject", {}),
        "keywords": store.get("__sample", []),
        "sites": store.get("sites", []),
        "standardVersion": store.version,
    }


# ---------- 导入与分析 ----------

class AnalyzeBody(BaseModel):
    keywords: list[dict[str, Any]] = Field(default_factory=list)
    project: dict[str, Any] | None = None


class CsvBody(BaseModel):
    csv: str = ""
    project: dict[str, Any] | None = None
    save: bool = False
    filename: str | None = None


class FileBody(BaseModel):
    filename: str = ""
    contentBase64: str | None = None
    contentText: str | None = None
    project: dict[str, Any] | None = None
    save: bool = False


class KeywordAiAnalyzeBody(BaseModel):
    keywordIds: list[str] = Field(default_factory=list)
    limit: int = 10
    opportunityType: str | None = None


@router.post("/workflow/analyze")
async def analyze(body: AnalyzeBody) -> dict[str, Any]:
    project = body.project or get_store().get("positioning.defaultProject", {})
    keywords = analyze_keywords(body.keywords, project)
    return {"keywords": keywords, "sites": get_store().get("sites", []), "standardVersion": get_store().version}


@router.post("/workflow/import-csv")
async def import_csv(body: CsvBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    project = body.project or get_store().get("positioning.defaultProject", {})
    keywords = import_and_analyze_csv(body.csv, project)
    saved = await upsert_keywords(session, [{**k, "sourceFile": body.filename} for k in keywords]) if body.save else 0
    if body.save:
        await session.commit()
    return {"keywords": keywords, "saved": saved, "sites": get_store().get("sites", []), "standardVersion": get_store().version}


@router.post("/workflow/import-file")
async def import_file(body: FileBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    project = body.project or get_store().get("positioning.defaultProject", {})
    keywords = import_and_analyze_file(
        {"filename": body.filename, "contentBase64": body.contentBase64 or "", "contentText": body.contentText or ""},
        project,
    )
    saved = await upsert_keywords(session, [{**k, "sourceFile": body.filename} for k in keywords]) if body.save else 0
    if body.save:
        await session.commit()
    return {
        "keywords": keywords,
        "saved": saved,
        "sites": get_store().get("sites", []),
        "standardVersion": get_store().version,
        "filename": body.filename,
    }


@router.post("/workflow/keywords/ai-analyze")
async def ai_analyze_keywords(body: KeywordAiAnalyzeBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await analyze_keyword_strategy(
        session,
        keyword_ids=body.keywordIds or None,
        limit=body.limit,
        opportunity_type=body.opportunityType,
    )


# ---------- Brief / Prompt / Article ----------

class BriefBody(BaseModel):
    keyword: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    aiStage: dict[str, Any] | None = None


class PromptBody(BaseModel):
    keyword: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    briefOverride: str | None = None
    brief: str | None = None


@router.post("/workflow/brief")
async def brief(body: BriefBody) -> dict[str, Any]:
    payload = {"keyword": body.keyword, "project": body.project, "aiStage": body.aiStage or {}}
    return await build_brief_with_optional_ai(payload)


@router.post("/workflow/prompt")
async def prompt(body: PromptBody) -> dict[str, Any]:
    from app.engine.locale import locale_for_project
    from app.engine.content_plan import article_brief_template_for

    project = body.project or {}
    item = body.keyword or {}
    brief_text = (body.briefOverride or body.brief or "").strip()
    if not brief_text:
        # 退化：用 build_brief 拿 brief
        local = build_brief(item, project)
        brief_text = (local.get("brief") or "") if isinstance(local.get("brief"), str) else ""
    locale = locale_for_project(project)
    return {
        "brief": brief_text,
        "locale": locale,
        "articleBriefTemplate": article_brief_template_for(item, project),
        "prompt": _compose_prompt(item, project, brief_text, locale),
    }


def _compose_prompt(item: dict[str, Any], project: dict[str, Any], brief_text: str, locale: dict[str, Any]) -> str:
    store = get_store()
    return "\n".join(
        [
            "你是 Google SEO 内容策略与文章写作助手。",
            "",
            f"主站：{project.get('domain') or ''}",
            f"目标市场：{project.get('market') or ''}",
            f"核心产品：{project.get('coreProducts') or ''}",
            "",
            "## 关键词 Brief",
            brief_text,
            "",
            "## 文章 Brief 模板模块",
            json_dumps_compact(store.get("articleBriefTemplate.modules", [])),
            "",
            "## 锚文本规则",
            json_dumps_compact(store.get("anchorTextRules", {})),
            "",
            "## 输出格式",
            json_dumps_compact(store.get("articleOutputFormat", {})),
            "",
            "## 引用",
            json_dumps_compact(reference_plan(item)),
            "",
            "## Locale",
            f"gl={locale.get('googleGl') or 'not-set'} / hl={locale.get('googleHl') or 'not-set'}",
        ]
    )


def json_dumps_compact(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        return str(value)


class MockArticleBody(BaseModel):
    keyword: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    brief: str | None = None
    prompt: str | None = None


class ArticleGenerateBody(BaseModel):
    keywordId: str


@router.post("/workflow/article-generate")
async def generate_article(body: ArticleGenerateBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await generate_article_from_keyword(session, body.keywordId)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/workflow/article-pipeline")
async def article_pipeline(body: ArticleGenerateBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    result = await generate_article_pipeline(session, body.keywordId)
    if result.get("status") == "failed":
        return result
    return result


@router.post("/workflow/mock-article")
async def mock_article(body: MockArticleBody) -> dict[str, Any]:
    item = body.keyword or {}
    project = body.project or {}
    if is_stage_configured("article_generation"):
        prompt_text = _article_prompt(item, project, body.brief or "", body.prompt or "")
        ai = await generate_ai_content(stage="article_generation", prompt=prompt_text, project=project, keyword=item)
        return {
            "content": ai.get("content") or "",
            "provider": ai.get("provider") or "",
            "model": ai.get("model") or "",
            "status": ai.get("status"),
            "generated": bool(ai.get("content")),
        }
    asset = {"url": item.get("targetAsset"), "status": item.get("assetStatus") or "planned", "contentAction": item.get("contentAction") or "create_new_article"}
    refs = reference_plan(item)
    images = image_plan_for(item)
    return {"content": _mock_md(item, asset, refs, images), "generated": False, "status": "ai-not-configured"}


def _article_prompt(item: dict[str, Any], project: dict[str, Any], brief: str, prompt: str) -> str:
    return "\n\n".join(
        [
            prompt or "Write an SEO article from the brief.",
            "Return Markdown only.",
            "Include: H1, intro, useful H2 sections, FAQ, meta title, meta description.",
            f"Primary keyword: {item.get('keyword') or ''}",
            f"Target site/role: {item.get('assignedSite') or item.get('assigned_site_label') or ''}",
            f"Project: {json_dumps_compact(project)}",
            f"Brief: {brief}",
        ]
    )


def _mock_md(item: dict[str, Any], asset: dict[str, Any], refs: dict[str, Any], images: list[dict[str, str]]) -> str:
    title = (item.get("keyword") or "untitled").title()
    return "\n".join(
        [
            f"## Title\n\n{title}\n",
            f"## Meta Title\n\n{title}: Practical Guide Before You Decide\n",
            f"## Meta Description\n\nLearn about {item.get('keyword')} with a clear decision path and SEO-ready structure.\n",
            f"## URL Slug\n\n{title.lower().replace(' ', '-')}\n",
            f"## Primary Keyword: {item.get('keyword')}\n",
            "## Secondary Keywords: (fill)\n",
            "## Last Updated\n\nToday\n",
            f"# {title}\n",
            f"Outline by Brief template.\n",
            *(f"- {i['name']}: {i['position']}" for i in images),
            *(
                [f"## References\n\n- {s['name']}: [{s['label']}]({s['url']})" for s in refs.get("sources", [])]
                if refs.get("triggered")
                else []
            ),
        ]
    )


# ---------- sync workspace (PG) ----------

class SyncWorkspaceBody(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)


@router.post("/database/sync-workspace")
async def sync_workspace(body: SyncWorkspaceBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    snap = body.snapshot
    sites_n = await upsert_sites(session, snap.get("sites", []) or [])
    kw_n = await upsert_keywords(session, snap.get("keywords", []) or [])
    await session.commit()
    return {"sites": sites_n, "keywords": kw_n}
