"""从 config/ai-stages.local.json 同步到 .env（六个 AI 阶段）。

用法：
  python scripts/sync_ai_stages.py

把 ai-stages.local.json 的 baseUrl / apiKey / model 展平为 .env 风格的：
  AI_KEYWORD_ANALYSIS_BASE_URL=...
  AI_KEYWORD_ANALYSIS_KEY=...
  AI_KEYWORD_ANALYSIS_MODEL=...
  ...（其它阶段同理）

stage 名到 .env 字段名的映射（与 .env.example / Settings 对齐）：
  keywordAnalysis    -> AI_KEYWORD_ANALYSIS_*
  productExtraction  -> AI_PRODUCT_EXTRACTION_*
  briefGeneration    -> AI_BRIEF_GENERATION_*
  articleGeneration  -> AI_ARTICLE_GENERATION_*
  contentOptimization-> AI_CONTENT_OPTIMIZATION_*
  siteDiagnosis      -> AI_SITE_DIAGNOSIS_*
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "config" / "ai-stages.local.json"
ENV_PATH = ROOT / ".env"

STAGE_TO_ENV = {
    "keywordAnalysis": "AI_KEYWORD_ANALYSIS",
    "productExtraction": "AI_PRODUCT_EXTRACTION",
    "briefGeneration": "AI_BRIEF_GENERATION",
    "articleGeneration": "AI_ARTICLE_GENERATION",
    "contentOptimization": "AI_CONTENT_OPTIMIZATION",
    "siteDiagnosis": "AI_SITE_DIAGNOSIS",
}


def load_existing_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main() -> None:
    if not CFG_PATH.exists():
        raise SystemExit(f"找不到 {CFG_PATH}")

    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    stages = cfg.get("stages", {}) or {}
    env = load_existing_env()

    injected: list[str] = []
    for stage_key, prefix in STAGE_TO_ENV.items():
        node = stages.get(stage_key, {}) or {}
        if not node:
            continue
        base = node.get("baseUrl", "") or ""
        key = node.get("apiKey", "") or ""
        model = node.get("model", "") or ""
        env[f"{prefix}_BASE_URL"] = base
        env[f"{prefix}_KEY"] = key
        env[f"{prefix}_MODEL"] = model
        injected.append(f"{prefix}_*  ←  {stage_key}")

    # 同步 serpapi + image
    serp = ROOT / "config" / "serpapi.local.json"
    if serp.exists():
        s = json.loads(serp.read_text(encoding="utf-8"))
        if s.get("apiKey"):
            env["SERPAPI_KEY"] = s["apiKey"]
            injected.append("SERPAPI_KEY  ←  serpapi.local.json")

    img = ROOT / "config" / "image-providers.local.json"
    if img.exists():
        i = json.loads(img.read_text(encoding="utf-8"))
        providers = i.get("providers", {}) or {}
        for name, p in providers.items():
            if not isinstance(p, dict):
                continue
            k = p.get("apiKey") or ""
            if not k:
                continue
            field = f"IMAGE_{name.upper()}_KEY"
            env[field] = k
            injected.append(f"{field}  ←  image-providers.local.json")

    # 重写 .env（保留所有现有字段，更新 AI_* / SERP / IMAGE_*）
    lines = ["# .env 由 scripts/sync_env.py 生成；请勿手动改 AI_*/SERPAPI_KEY/IMAGE_*", "#"]
    if injected:
        lines.append("# 由 sync_env.py 注入的字段：")
        lines.extend([f"#   {x}" for x in injected])
        lines.append("#")
    # 输出顺序：先 DB，再 AI（每个一组），最后其它
    sections = [
        ["APP_NAME", "APP_ENV", "APP_PORT"],
        ["DATABASE_URL", "DATABASE_POOL_SIZE", "DATABASE_MAX_OVERFLOW"],
        ["REDIS_URL"],
        ["LOG_LEVEL", "LOG_JSON"],
        ["RULE_AUTO_RELOAD_SECONDS"],
        (
            "AI_KEYWORD_ANALYSIS_BASE_URL AI_KEYWORD_ANALYSIS_KEY AI_KEYWORD_ANALYSIS_MODEL "
            "AI_PRODUCT_EXTRACTION_BASE_URL AI_PRODUCT_EXTRACTION_KEY AI_PRODUCT_EXTRACTION_MODEL "
            "AI_BRIEF_GENERATION_BASE_URL AI_BRIEF_GENERATION_KEY AI_BRIEF_GENERATION_MODEL "
            "AI_ARTICLE_GENERATION_BASE_URL AI_ARTICLE_GENERATION_KEY AI_ARTICLE_GENERATION_MODEL "
            "AI_CONTENT_OPTIMIZATION_BASE_URL AI_CONTENT_OPTIMIZATION_KEY AI_CONTENT_OPTIMIZATION_MODEL "
            "AI_SITE_DIAGNOSIS_BASE_URL AI_SITE_DIAGNOSIS_KEY AI_SITE_DIAGNOSIS_MODEL"
        ).split(),
        ["SERPAPI_KEY"],
        ["IMAGE_PEXELS_KEY", "IMAGE_UNSPLASH_KEY", "IMAGE_PIXABAY_KEY"],
        ["AI_BRIEF_CACHE_TTL_MS", "AI_BRIEF_CACHE_MAX"],
        ["HTTP_TIMEOUT_SECONDS", "HTTP_RETRY_MAX"],
    ]
    written: set[str] = set()
    for keys in sections:
        for k in keys:
            if k in env and k not in written:
                lines.append(f"{k}={env[k]}")
                written.add(k)
    # 写出
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {ENV_PATH}；注入字段：")
    for x in injected:
        print(f"  - {x}")


if __name__ == "__main__":
    main()