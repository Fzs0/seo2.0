"""v3 Localize Comments: 中文化所有 COMMENT ON（不建表 / 不改结构）。

rev: f252ae2221b1
depends: 149bcf2501fe
"""
from alembic import op

revision = "f252ae2221b1"
down_revision = "149bcf2501fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # === sites 表注释 ===
    op.execute("COMMENT ON TABLE seo_agent.sites IS '目标发布站点主档。每行代表一个主站、WordPress 站或博客站，是 keywords/posts/tasks/articles 的关联根。';")

    op.execute("COMMENT ON COLUMN seo_agent.sites.id IS 'uuid 主键，跨进程（Node.js + 未来 Python）共享 ID。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.site_key IS '站点业务唯一键，例如 main_store、blog_a。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.name IS '人类可读站点名，例如 主站-英文商城。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.site_type IS '站点类型枚举：main / wp / blog / other。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.domain IS '站点主域名，例如 example.com。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.base_url IS '站点首页完整 URL，用于拼接相对路径。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.api_base_url IS '站点 API 入口 URL（WordPress REST、Shopify Storefront 等）。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.market IS '目标市场，例如 United States / Germany。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.language_code IS '内容语言 BCP-47 代码，例如 en / de / ja。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.google_gl IS 'Google SERP gl 参数，例如 us / de / jp。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.google_hl IS 'Google SERP hl 参数，例如 en / de / ja。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.semrush_database IS 'Semrush 数据库代码，必须与 market 匹配，例如 us / de。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.content_role IS '内容角色描述，例如 主站商业集合、博客 A 知识教程。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.content_scope IS '内容覆盖范围说明，例如 承接全部商业集合页与商业前教育。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.is_main IS '是否主站；一个项目最多一行 true。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.allow_external_links IS '该站文章是否允许向站外输出可点击链接。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.publish_config IS '发布配置 jsonb，例如对接 WP / Shopify 的额外参数。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.api_config IS 'API 配置 jsonb，例如认证方式、限流策略。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.status IS '站点状态枚举：active / paused / archived。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.notes IS '人工备注。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.raw IS '原始抓取数据 jsonb，保留不可结构化的来源字段。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.created_at IS '行创建时间，DB 默认 now()。';")
    op.execute("COMMENT ON COLUMN seo_agent.sites.updated_at IS '最近更新时间，由 set_updated_at 触发器维护。';")

    # === keywords 表注释（按列名映射） ===
    kw_comments = {
        "id": "uuid 主键。",
        "keyword": "关键词原文，例如 best disposable vapes。",
        "normalized_keyword": "规范化关键词（小写、压缩空白），generated 列，用于去重。",
        "source": "来源标识，例如 semrush / ahrefs / manual。",
        "source_file": "来源文件名，便于回溯导入批次。",
        "semrush_database": "该关键词所属 Semrush 数据库，例如 us / de。",
        "market": "关键词所属市场，用于跨市场一致性检查。",
        "language_code": "关键词所属语言。",
        "google_gl": "该关键词在 Google SERP 校验时应使用的 gl 参数。",
        "google_hl": "该关键词在 Google SERP 校验时应使用的 hl 参数。",
        "volume": "月搜索量。",
        "kd": "Keyword Difficulty（0~100）。",
        "cpc": "CPC（USD）。",
        "intent": "搜索意图标签，例如 informational / commercial / transactional。",
        "serp_features": "SERP 特性快照 jsonb 数组，例如 featured_snippet / paa。",
        "topic_cluster": "主题集群，避免一篇文章只覆盖一个散词。",
        "seed_keyword": "种子词，Semrush 导出中的 parent 词。",
        "page_group": "页面组，Semrush 导出中的 page group 字段。",
        "assigned_site_id": "分站结果 → sites.id，ON DELETE SET NULL。",
        "assigned_site_label": "分站展示标签，例如 主站-集合页，便于不连表查询。",
        "page_type": "页面类型，例如 集合页 / 产品列表页。",
        "page_role": "页面角色，例如 Commercial Hub / Buyer Education。",
        "target_asset_url": "推荐承接资产 URL，可能为已存在或规划中。",
        "asset_status": "资产状态枚举：existing / planned / needs_review / missing。",
        "content_action": "建议内容动作枚举：update_or_link / optimize_existing_page / create_or_update_supporting_content / manual_parent_review / create_commercial_page_first / create_new_article。",
        "priority": "优先级枚举：P0 / P1 / P2 / P3 / Hold。",
        "score": "本地规则评分（0~100），由 scorer.mjs 计算。",
        "status": "生命周期状态枚举：imported / analyzed / planned / queued / written / published / reviewed / hold / dropped。",
        "reason": "分站评分理由或人工备注。",
        "ai_review": "AI 复核结果 jsonb，含 confidence / needsSerpCheck 等字段。",
        "raw": "原始导入行 jsonb，便于回溯字段映射。",
        "imported_at": "导入时间。",
        "created_at": "行创建时间。",
        "updated_at": "最近更新时间。",
    }
    for col, text in kw_comments.items():
        op.execute(f"COMMENT ON COLUMN seo_agent.keywords.{col} IS '{esc(text)}';")

    op.execute("COMMENT ON TABLE seo_agent.keywords IS '关键词主档。每行代表一个从 Semrush 等来源导入的关键词及其本地规则分析结果。';")

    # === posts 表注释 ===
    post_comments = {
        "id": "uuid 主键。",
        "site_id": "所属站点 → sites.id，ON DELETE CASCADE。",
        "external_id": "外部系统的 ID，例如 WordPress post_id。",
        "title": "文章标题。",
        "slug": "URL slug。",
        "url": "文章完整 URL。",
        "status": "文章状态（来自外部系统，例如 publish / draft / pending）。",
        "author": "作者名。",
        "category_id": "外部系统的分类 ID。",
        "language_code": "文章语言。",
        "market": "目标市场。",
        "primary_keyword_id": "对应主关键词 → keywords.id，ON DELETE SET NULL。",
        "primary_keyword": "冗余存储主关键词文本，便于不连表查询。",
        "topic_cluster": "主题集群。",
        "page_type": "页面类型。",
        "content_format": "正文格式枚举：markdown / html / mixed / unknown。",
        "content_md": "Markdown 正文。",
        "content_html": "HTML 正文（保留以适配 Gutenberg / Shopify 等）。",
        "excerpt": "摘要。",
        "meta_title": "SEO title。",
        "meta_description": "SEO meta description。",
        "meta_keywords": "SEO keywords jsonb 数组。",
        "cover_url": "封面图 URL。",
        "published_at": "外部发布时间。",
        "modified_at": "外部最近修改时间。",
        "fetched_at": "本地抓取时间。",
        "source": "来源标识，例如 wp_api / shopify / manual。",
        "raw": "原始抓取数据 jsonb。",
        "created_at": "行创建时间。",
        "updated_at": "最近更新时间。",
    }
    for col, text in post_comments.items():
        op.execute(f"COMMENT ON COLUMN seo_agent.posts.{col} IS '{esc(text)}';")

    op.execute("COMMENT ON TABLE seo_agent.posts IS '已发布文章主档。每行代表一个已抓取或已发布的站点文章，包含原始 Markdown / HTML 与 SEO 元数据。';")

    # === serp_snapshots 表注释 ===
    serp_comments = {
        "id": "uuid 主键。",
        "keyword_id": "对应关键词 → keywords.id，ON DELETE SET NULL。",
        "keyword": "冗余存储关键词原文。",
        "normalized_keyword": "规范化关键词，generated 列。",
        "engine": "搜索引擎，默认 google。",
        "google_gl": "Google SERP gl 参数。",
        "google_hl": "Google SERP hl 参数。",
        "location": "地点全称，例如 United States。",
        "requested_at": "请求时间。",
        "top_result_count": "Top 10 结果数量。",
        "organic_results": "自然结果 jsonb 数组。",
        "related_questions": "People Also Ask jsonb 数组。",
        "related_searches": "相关搜索 jsonb 数组。",
        "page_type_summary": "页面类型总结，例如 前 10 中 6 个集合页、3 个评测、1 个博客。",
        "intent_summary": "意图总结。",
        "competitor_gaps": "竞品缺口分析 jsonb 数组。",
        "raw": "原始 SERP 响应 jsonb。",
        "created_at": "行创建时间。",
    }
    for col, text in serp_comments.items():
        op.execute(f"COMMENT ON COLUMN seo_agent.serp_snapshots.{col} IS '{esc(text)}';")

    op.execute("COMMENT ON TABLE seo_agent.serp_snapshots IS 'Google SERP Top 10 一次性快照。每行代表一次对指定关键词 + locale 的 SERP 抓取结果，用于本地决策时不再二次依赖外部。';")

    # === tasks 表注释 ===
    task_comments = {
        "id": "uuid 主键。",
        "task_type": "任务类型枚举：keyword_review / serp_check / new_article / update_article / internal_link / publish / review / product_extract。",
        "status": "任务状态枚举：queued / running / blocked / failed / done / canceled / skipped。",
        "priority": "优先级枚举：P0 / P1 / P2 / P3 / Hold。",
        "score": "关联关键词的本地规则分（冗余，便于不连表查询）。",
        "site_id": "关联站点 → sites.id，ON DELETE SET NULL。",
        "keyword_id": "关联关键词 → keywords.id，ON DELETE SET NULL。",
        "post_id": "关联已发布文章 → posts.id，ON DELETE SET NULL。",
        "article_id": "关联生成稿件 → articles.id，ON DELETE SET NULL。",
        "serp_snapshot_id": "关联 SERP 快照 → serp_snapshots.id，ON DELETE SET NULL。",
        "target_url": "任务目标 URL，例如要发布的页面或要抓取的接口。",
        "title": "任务标题，便于人眼扫读。",
        "payload": "任务参数 jsonb，例如 prompt、模型配置。",
        "required_data": "执行该任务所需的硬门槛数据，例如 serp_check、site_profile。",
        "decision": "任务最终决策 jsonb，例如 score / chosen_site / reason。",
        "logs": "执行过程日志 jsonb 数组。",
        "error_message": "失败时的错误信息。",
        "run_after": "最早可执行时间（用于节流/排队）。",
        "started_at": "实际开始时间。",
        "finished_at": "实际结束时间（成功或失败均记）。",
        "created_at": "行创建时间。",
        "updated_at": "最近更新时间。",
    }
    for col, text in task_comments.items():
        op.execute(f"COMMENT ON COLUMN seo_agent.tasks.{col} IS '{esc(text)}';")

    op.execute("COMMENT ON TABLE seo_agent.tasks IS '任务事件表。每行代表一个 AI 任务或人工任务（new_article / update_article / publish / review 等）的执行实例。';")

    # === articles 表注释 ===
    art_comments = {
        "id": "uuid 主键。",
        "task_id": "生成该稿件的任务 → tasks.id，ON DELETE SET NULL。",
        "site_id": "目标站点 → sites.id，ON DELETE SET NULL。",
        "keyword_id": "对应关键词 → keywords.id，ON DELETE SET NULL。",
        "serp_snapshot_id": "引用 SERP 快照 → serp_snapshots.id，ON DELETE SET NULL。",
        "title": "文章标题。",
        "slug": "URL slug。",
        "target_url": "目标 URL。",
        "status": "稿件状态枚举：draft / generated / approved / published / failed / archived。",
        "language_code": "文章语言。",
        "market": "目标市场。",
        "brief_md": "本地生成的 brief（Markdown）。",
        "prompt_text": "实际下发给 AI 的 prompt 文本。",
        "content_md": "Markdown 正文。",
        "content_html": "HTML 正文。",
        "article_parts": "正文分块结构 jsonb，例如 Title/MetaTitle/MetaDescription/H1/Body/References 等。",
        "meta_title": "SEO title。",
        "meta_description": "SEO meta description。",
        "primary_keyword": "主关键词文本（冗余）。",
        "secondary_keywords": "副关键词 text[]。",
        "internal_link_plan": "内链计划 jsonb，包含目标资产、状态、是否允许正文落地。",
        "image_plan": "图片位置计划 jsonb，遵循 seo-standard.json imagePlacements。",
        "references_plan": "References 计划 jsonb，含 trigger / sources。",
        "qa_checklist": "上线前 QA 自检清单 jsonb。",
        "generation_provider": "生成供应商标识，例如 openai / deepseek / claude。",
        "generation_model": "生成模型名称，例如 gpt-4.1-mini / deepseek-chat。",
        "published_post_id": "发布到外部系统后的 post id。",
        "published_url": "发布后的 URL。",
        "published_at": "发布时间。",
        "saved_path": "本地落盘路径（Markdown 文件）。",
        "log_path": "生成日志本地路径。",
        "raw_ai_response": "AI 原始响应 jsonb，便于复盘与重放。",
        "created_at": "行创建时间。",
        "updated_at": "最近更新时间。",
    }
    for col, text in art_comments.items():
        op.execute(f"COMMENT ON COLUMN seo_agent.articles.{col} IS '{esc(text)}';")

    op.execute("COMMENT ON TABLE seo_agent.articles IS '生成稿件主档。每行代表一次由 AI 生成的、待人工审核或已发布的文章版本，包含 brief、prompt、正文与各类结构化字段。';")


def esc(s: str) -> str:
    return s.replace("'", "''")


def downgrade() -> None:
    # COMMENT 没有原子回滚，把所有 COMMENT 设为空字符串
    targets = [
        ("sites", list(range(23))),
        ("keywords", list(range(34))),
        ("posts", list(range(30))),
        ("serp_snapshots", list(range(20))),
        ("tasks", list(range(22))),
        ("articles", list(range(34))),
    ]
    for table, _ in targets:
        op.execute(f"COMMENT ON TABLE seo_agent.{table} IS NULL;")
    op.execute("DO $$ DECLARE r record; BEGIN FOR r IN SELECT table_schema, table_name, column_name FROM information_schema.columns WHERE table_schema = 'seo_agent' LOOP EXECUTE format('COMMENT ON COLUMN %I.%I.%I IS NULL', r.table_schema, r.table_name, r.column_name); END LOOP; END $$;")