-- =====================================================================
-- SEO Workbench Migration 003: 本地化 COMMENT
-- Depends : 001_agent_memory_schema.sql + 002_rule_engine.sql
-- Target  : PostgreSQL 14+
-- Idempotent: yes (COMMENT ON ... IS '...' 重复执行会覆盖)
--
-- 本次改动只动 COMMENT，不动 schema 结构。
-- v1 表原本没有任何 COMMENT，本 migration 为其首次创建中文注释。
-- v2 表原本有英文 COMMENT，本 migration 改为中文。
--
-- 规范（与 docs/rule-extraction.md 同步）：
--   1. 保留技术名词：PK/FK/UUID/JSONB/CHECK/ENUM/IDENTITY 等不译。
--   2. 保留专有名词：seo-standard / seo_agent / Semrush / WordPress / GSC / GA4。
--   3. 保留 FK 指向：COMMENT 中以"→ 表名.列名"表达。
--   4. 单句短句，避免长段落。
--   5. 表注释 = 用途 + 关键约束。
--   6. 列注释 = 字段含义，必要时补一句约束或默认值。
-- =====================================================================

SET search_path = seo_agent;

-- =====================================================================
-- v1 业务表：sites
-- =====================================================================
COMMENT ON TABLE seo_agent.sites IS
  '目标发布站点主档。每行代表一个主站、WordPress 站或博客站，是 keywords/posts/tasks/articles 的关联根。';

COMMENT ON COLUMN seo_agent.sites.id IS              'uuid 主键，跨进程（Node.js + 未来 Python）共享 ID。';
COMMENT ON COLUMN seo_agent.sites.site_key IS        '站点业务唯一键，例如 "main_store"、"blog_a"。';
COMMENT ON COLUMN seo_agent.sites.name IS            '人类可读站点名，例如 "主站-英文商城"。';
COMMENT ON COLUMN seo_agent.sites.site_type IS       '站点类型枚举：main / wp / blog / other。';
COMMENT ON COLUMN seo_agent.sites.domain IS          '站点主域名，例如 example.com。';
COMMENT ON COLUMN seo_agent.sites.base_url IS        '站点首页完整 URL，用于拼接相对路径。';
COMMENT ON COLUMN seo_agent.sites.api_base_url IS    '站点 API 入口 URL（WordPress REST、Shopify Storefront 等）。';
COMMENT ON COLUMN seo_agent.sites.market IS          '目标市场，例如 United States / Germany。';
COMMENT ON COLUMN seo_agent.sites.language_code IS   '内容语言 BCP-47 代码，例如 en / de / ja。';
COMMENT ON COLUMN seo_agent.sites.google_gl IS       'Google SERP gl 参数，例如 us / de / jp。';
COMMENT ON COLUMN seo_agent.sites.google_hl IS       'Google SERP hl 参数，例如 en / de / ja。';
COMMENT ON COLUMN seo_agent.sites.semrush_database IS'Semrush 数据库代码，必须与 market 匹配，例如 us / de。';
COMMENT ON COLUMN seo_agent.sites.content_role IS    '内容角色描述，例如"主站商业集合"、"博客 A 知识教程"。';
COMMENT ON COLUMN seo_agent.sites.content_scope IS   '内容覆盖范围说明，例如"承接全部商业集合页与商业前教育"。';
COMMENT ON COLUMN seo_agent.sites.is_main IS         '是否主站；一个项目最多一行 true。';
COMMENT ON COLUMN seo_agent.sites.allow_external_links IS '该站文章是否允许向站外输出可点击链接。';
COMMENT ON COLUMN seo_agent.sites.publish_config IS  '发布配置 jsonb，例如对接 WP / Shopify 的额外参数。';
COMMENT ON COLUMN seo_agent.sites.api_config IS      'API 配置 jsonb，例如认证方式、限流策略。';
COMMENT ON COLUMN seo_agent.sites.status IS          '站点状态枚举：active / paused / archived。';
COMMENT ON COLUMN seo_agent.sites.notes IS           '人工备注。';
COMMENT ON COLUMN seo_agent.sites.raw IS             '原始抓取数据 jsonb，保留不可结构化的来源字段。';
COMMENT ON COLUMN seo_agent.sites.created_at IS      '行创建时间，DB 默认 now()。';
COMMENT ON COLUMN seo_agent.sites.updated_at IS      '最近更新时间，由 set_updated_at 触发器维护。';

-- =====================================================================
-- v1 业务表：keywords
-- =====================================================================
COMMENT ON TABLE seo_agent.keywords IS
  '关键词主档。每行代表一个从 Semrush 等来源导入的关键词及其本地规则分析结果。';

COMMENT ON COLUMN seo_agent.keywords.id IS                  'uuid 主键。';
COMMENT ON COLUMN seo_agent.keywords.keyword IS             '关键词原文，例如 "best disposable vapes"。';
COMMENT ON COLUMN seo_agent.keywords.normalized_keyword IS  '规范化关键词（小写、压缩空白），generated 列，用于去重。';
COMMENT ON COLUMN seo_agent.keywords.source IS              '来源标识，例如 semrush / ahrefs / manual。';
COMMENT ON COLUMN seo_agent.keywords.source_file IS         '来源文件名，便于回溯导入批次。';
COMMENT ON COLUMN seo_agent.keywords.semrush_database IS    '该关键词所属 Semrush 数据库，例如 us / de。';
COMMENT ON COLUMN seo_agent.keywords.market IS              '关键词所属市场，用于跨市场一致性检查。';
COMMENT ON COLUMN seo_agent.keywords.language_code IS       '关键词所属语言。';
COMMENT ON COLUMN seo_agent.keywords.google_gl IS           '该关键词在 Google SERP 校验时应使用的 gl 参数。';
COMMENT ON COLUMN seo_agent.keywords.google_hl IS           '该关键词在 Google SERP 校验时应使用的 hl 参数。';
COMMENT ON COLUMN seo_agent.keywords.volume IS              '月搜索量。';
COMMENT ON COLUMN seo_agent.keywords.kd IS                 'Keyword Difficulty（0~100）。';
COMMENT ON COLUMN seo_agent.keywords.cpc IS                'CPC（USD）。';
COMMENT ON COLUMN seo_agent.keywords.intent IS             '搜索意图标签，例如 informational / commercial / transactional。';
COMMENT ON COLUMN seo_agent.keywords.serp_features IS       'SERP 特性快照 jsonb 数组，例如 featured_snippet / paa。';
COMMENT ON COLUMN seo_agent.keywords.topic_cluster IS       '主题集群，避免一篇文章只覆盖一个散词。';
COMMENT ON COLUMN seo_agent.keywords.seed_keyword IS        '种子词，Semrush 导出中的 parent 词。';
COMMENT ON COLUMN seo_agent.keywords.page_group IS          '页面组，Semrush 导出中的 page group 字段。';
COMMENT ON COLUMN seo_agent.keywords.assigned_site_id IS    '分站结果 → sites.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.keywords.assigned_site_label IS '分站展示标签，例如"主站-集合页"，便于不连表查询。';
COMMENT ON COLUMN seo_agent.keywords.page_type IS           '页面类型，例如"集合页 / 产品列表页"。';
COMMENT ON COLUMN seo_agent.keywords.page_role IS           '页面角色，例如 Commercial Hub / Buyer Education。';
COMMENT ON COLUMN seo_agent.keywords.target_asset_url IS    '推荐承接资产 URL，可能为已存在或规划中。';
COMMENT ON COLUMN seo_agent.keywords.asset_status IS         '资产状态枚举：existing / planned / needs_review / missing。';
COMMENT ON COLUMN seo_agent.keywords.content_action IS      '建议内容动作枚举：update_or_link / optimize_existing_page / create_or_update_supporting_content / manual_parent_review / create_commercial_page_first / create_new_article。';
COMMENT ON COLUMN seo_agent.keywords.priority IS            '优先级枚举：P0 / P1 / P2 / P3 / Hold。';
COMMENT ON COLUMN seo_agent.keywords.score IS               '本地规则评分（0~100），由 scorer.mjs 计算。';
COMMENT ON COLUMN seo_agent.keywords.status IS              '生命周期状态枚举：imported / analyzed / planned / queued / written / published / reviewed / hold / dropped。';
COMMENT ON COLUMN seo_agent.keywords.reason IS              '分站评分理由或人工备注。';
COMMENT ON COLUMN seo_agent.keywords.ai_review IS           'AI 复核结果 jsonb，含 confidence / needsSerpCheck 等字段。';
COMMENT ON COLUMN seo_agent.keywords.raw IS                 '原始导入行 jsonb，便于回溯字段映射。';
COMMENT ON COLUMN seo_agent.keywords.imported_at IS         '导入时间。';
COMMENT ON COLUMN seo_agent.keywords.created_at IS          '行创建时间。';
COMMENT ON COLUMN seo_agent.keywords.updated_at IS          '最近更新时间。';

-- =====================================================================
-- v1 业务表：posts
-- =====================================================================
COMMENT ON TABLE seo_agent.posts IS
  '已发布文章主档。每行代表一个已抓取或已发布的站点文章，包含原始 Markdown / HTML 与 SEO 元数据。';

COMMENT ON COLUMN seo_agent.posts.id IS              'uuid 主键。';
COMMENT ON COLUMN seo_agent.posts.site_id IS         '所属站点 → sites.id，ON DELETE CASCADE。';
COMMENT ON COLUMN seo_agent.posts.external_id IS     '外部系统的 ID，例如 WordPress post_id。';
COMMENT ON COLUMN seo_agent.posts.title IS           '文章标题。';
COMMENT ON COLUMN seo_agent.posts.slug IS            'URL slug。';
COMMENT ON COLUMN seo_agent.posts.url IS             '文章完整 URL。';
COMMENT ON COLUMN seo_agent.posts.status IS          '文章状态（来自外部系统，例如 publish / draft / pending）。';
COMMENT ON COLUMN seo_agent.posts.author IS          '作者名。';
COMMENT ON COLUMN seo_agent.posts.category_id IS     '外部系统的分类 ID。';
COMMENT ON COLUMN seo_agent.posts.language_code IS   '文章语言。';
COMMENT ON COLUMN seo_agent.posts.market IS          '目标市场。';
COMMENT ON COLUMN seo_agent.posts.primary_keyword_id IS '对应主关键词 → keywords.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.posts.primary_keyword IS  '冗余存储主关键词文本，便于不连表查询。';
COMMENT ON COLUMN seo_agent.posts.topic_cluster IS    '主题集群。';
COMMENT ON COLUMN seo_agent.posts.page_type IS       '页面类型。';
COMMENT ON COLUMN seo_agent.posts.content_format IS  '正文格式枚举：markdown / html / mixed / unknown。';
COMMENT ON COLUMN seo_agent.posts.content_md IS      'Markdown 正文。';
COMMENT ON COLUMN seo_agent.posts.content_html IS    'HTML 正文（保留以适配 Gutenberg / Shopify 等）。';
COMMENT ON COLUMN seo_agent.posts.excerpt IS         '摘要。';
COMMENT ON COLUMN seo_agent.posts.meta_title IS      'SEO title。';
COMMENT ON COLUMN seo_agent.posts.meta_description IS'SEO meta description。';
COMMENT ON COLUMN seo_agent.posts.meta_keywords IS   'SEO keywords jsonb 数组。';
COMMENT ON COLUMN seo_agent.posts.cover_url IS       '封面图 URL。';
COMMENT ON COLUMN seo_agent.posts.published_at IS    '外部发布时间。';
COMMENT ON COLUMN seo_agent.posts.modified_at IS     '外部最近修改时间。';
COMMENT ON COLUMN seo_agent.posts.fetched_at IS      '本地抓取时间。';
COMMENT ON COLUMN seo_agent.posts.source IS          '来源标识，例如 wp_api / shopify / manual。';
COMMENT ON COLUMN seo_agent.posts.raw IS             '原始抓取数据 jsonb。';
COMMENT ON COLUMN seo_agent.posts.created_at IS      '行创建时间。';
COMMENT ON COLUMN seo_agent.posts.updated_at IS      '最近更新时间。';

-- =====================================================================
-- v1 业务表：serp_snapshots
-- =====================================================================
COMMENT ON TABLE seo_agent.serp_snapshots IS
  'Google SERP Top 10 一次性快照。每行代表一次对指定关键词 + locale 的 SERP 抓取结果，用于本地决策时不再二次依赖外部。';

COMMENT ON COLUMN seo_agent.serp_snapshots.id IS                  'uuid 主键。';
COMMENT ON COLUMN seo_agent.serp_snapshots.keyword_id IS          '对应关键词 → keywords.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.serp_snapshots.keyword IS             '冗余存储关键词原文。';
COMMENT ON COLUMN seo_agent.serp_snapshots.normalized_keyword IS  '规范化关键词，generated 列。';
COMMENT ON COLUMN seo_agent.serp_snapshots.engine IS              '搜索引擎，默认 google。';
COMMENT ON COLUMN seo_agent.serp_snapshots.google_gl IS           'Google SERP gl 参数。';
COMMENT ON COLUMN seo_agent.serp_snapshots.google_hl IS           'Google SERP hl 参数。';
COMMENT ON COLUMN seo_agent.serp_snapshots.location IS            '地点全称，例如 "United States"。';
COMMENT ON COLUMN seo_agent.serp_snapshots.requested_at IS        '请求时间。';
COMMENT ON COLUMN seo_agent.serp_snapshots.top_result_count IS     'Top 10 结果数量。';
COMMENT ON COLUMN seo_agent.serp_snapshots.organic_results IS     '自然结果 jsonb 数组。';
COMMENT ON COLUMN seo_agent.serp_snapshots.related_questions IS   'People Also Ask jsonb 数组。';
COMMENT ON COLUMN seo_agent.serp_snapshots.related_searches IS    '相关搜索 jsonb 数组。';
COMMENT ON COLUMN seo_agent.serp_snapshots.page_type_summary IS   '页面类型总结，例如"前 10 中 6 个集合页、3 个评测、1 个博客"。';
COMMENT ON COLUMN seo_agent.serp_snapshots.intent_summary IS      '意图总结。';
COMMENT ON COLUMN seo_agent.serp_snapshots.competitor_gaps IS     '竞品缺口分析 jsonb 数组。';
COMMENT ON COLUMN seo_agent.serp_snapshots.raw IS                 '原始 SERP 响应 jsonb。';
COMMENT ON COLUMN seo_agent.serp_snapshots.created_at IS          '行创建时间。';

-- =====================================================================
-- v1 业务表：tasks
-- =====================================================================
COMMENT ON TABLE seo_agent.tasks IS
  '任务事件表。每行代表一个 AI 任务或人工任务（new_article / update_article / publish / review 等）的执行实例。';

COMMENT ON COLUMN seo_agent.tasks.id IS               'uuid 主键。';
COMMENT ON COLUMN seo_agent.tasks.task_type IS        '任务类型枚举：keyword_review / serp_check / new_article / update_article / internal_link / publish / review / product_extract。';
COMMENT ON COLUMN seo_agent.tasks.status IS           '任务状态枚举：queued / running / blocked / failed / done / canceled / skipped。';
COMMENT ON COLUMN seo_agent.tasks.priority IS         '优先级枚举：P0 / P1 / P2 / P3 / Hold。';
COMMENT ON COLUMN seo_agent.tasks.score IS            '关联关键词的本地规则分（冗余，便于不连表查询）。';
COMMENT ON COLUMN seo_agent.tasks.site_id IS          '关联站点 → sites.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.tasks.keyword_id IS       '关联关键词 → keywords.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.tasks.post_id IS          '关联已发布文章 → posts.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.tasks.article_id IS       '关联生成稿件 → articles.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.tasks.serp_snapshot_id IS '关联 SERP 快照 → serp_snapshots.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.tasks.target_url IS       '任务目标 URL，例如要发布的页面或要抓取的接口。';
COMMENT ON COLUMN seo_agent.tasks.title IS            '任务标题，便于人眼扫读。';
COMMENT ON COLUMN seo_agent.tasks.payload IS          '任务参数 jsonb，例如 prompt、模型配置。';
COMMENT ON COLUMN seo_agent.tasks.required_data IS    '执行该任务所需的硬门槛数据，例如 serp_check、site_profile。';
COMMENT ON COLUMN seo_agent.tasks.decision IS         '任务最终决策 jsonb，例如 score / chosen_site / reason。';
COMMENT ON COLUMN seo_agent.tasks.logs IS             '执行过程日志 jsonb 数组。';
COMMENT ON COLUMN seo_agent.tasks.error_message IS    '失败时的错误信息。';
COMMENT ON COLUMN seo_agent.tasks.run_after IS        '最早可执行时间（用于节流/排队）。';
COMMENT ON COLUMN seo_agent.tasks.started_at IS       '实际开始时间。';
COMMENT ON COLUMN seo_agent.tasks.finished_at IS      '实际结束时间（成功或失败均记）。';
COMMENT ON COLUMN seo_agent.tasks.created_at IS       '行创建时间。';
COMMENT ON COLUMN seo_agent.tasks.updated_at IS       '最近更新时间。';

-- =====================================================================
-- v1 业务表：articles
-- =====================================================================
COMMENT ON TABLE seo_agent.articles IS
  '生成稿件主档。每行代表一次由 AI 生成的、待人工审核或已发布的文章版本，包含 brief、prompt、正文与各类结构化字段。';

COMMENT ON COLUMN seo_agent.articles.id IS                  'uuid 主键。';
COMMENT ON COLUMN seo_agent.articles.task_id IS             '生成该稿件的任务 → tasks.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.articles.site_id IS             '目标站点 → sites.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.articles.keyword_id IS          '对应关键词 → keywords.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.articles.serp_snapshot_id IS    '引用 SERP 快照 → serp_snapshots.id，ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.articles.title IS               '文章标题。';
COMMENT ON COLUMN seo_agent.articles.slug IS                'URL slug。';
COMMENT ON COLUMN seo_agent.articles.target_url IS          '目标 URL。';
COMMENT ON COLUMN seo_agent.articles.status IS              '稿件状态枚举：draft / generated / approved / published / failed / archived。';
COMMENT ON COLUMN seo_agent.articles.language_code IS       '文章语言。';
COMMENT ON COLUMN seo_agent.articles.market IS              '目标市场。';
COMMENT ON COLUMN seo_agent.articles.brief_md IS            '本地生成的 brief（Markdown）。';
COMMENT ON COLUMN seo_agent.articles.prompt_text IS         '实际下发给 AI 的 prompt 文本。';
COMMENT ON COLUMN seo_agent.articles.content_md IS          'Markdown 正文。';
COMMENT ON COLUMN seo_agent.articles.content_html IS        'HTML 正文。';
COMMENT ON COLUMN seo_agent.articles.article_parts IS       '正文分块结构 jsonb，例如 Title/MetaTitle/MetaDescription/H1/Body/References 等。';
COMMENT ON COLUMN seo_agent.articles.meta_title IS          'SEO title。';
COMMENT ON COLUMN seo_agent.articles.meta_description IS    'SEO meta description。';
COMMENT ON COLUMN seo_agent.articles.primary_keyword IS     '主关键词文本（冗余）。';
COMMENT ON COLUMN seo_agent.articles.secondary_keywords IS  '副关键词 text[]。';
COMMENT ON COLUMN seo_agent.articles.internal_link_plan IS  '内链计划 jsonb，包含目标资产、状态、是否允许正文落地。';
COMMENT ON COLUMN seo_agent.articles.image_plan IS          '图片位置计划 jsonb，遵循 seo-standard.json imagePlacements。';
COMMENT ON COLUMN seo_agent.articles.references_plan IS     'References 计划 jsonb，含 trigger / sources。';
COMMENT ON COLUMN seo_agent.articles.qa_checklist IS        '上线前 QA 自检清单 jsonb。';
COMMENT ON COLUMN seo_agent.articles.generation_provider IS '生成供应商标识，例如 openai / deepseek / claude。';
COMMENT ON COLUMN seo_agent.articles.generation_model IS    '生成模型名称，例如 gpt-4.1-mini / deepseek-chat。';
COMMENT ON COLUMN seo_agent.articles.published_post_id IS   '发布到外部系统后的 post id。';
COMMENT ON COLUMN seo_agent.articles.published_url IS       '发布后的 URL。';
COMMENT ON COLUMN seo_agent.articles.published_at IS        '发布时间。';
COMMENT ON COLUMN seo_agent.articles.saved_path IS          '本地落盘路径（Markdown 文件）。';
COMMENT ON COLUMN seo_agent.articles.log_path IS            '生成日志本地路径。';
COMMENT ON COLUMN seo_agent.articles.raw_ai_response IS     'AI 原始响应 jsonb，便于复盘与重放。';
COMMENT ON COLUMN seo_agent.articles.created_at IS          '行创建时间。';
COMMENT ON COLUMN seo_agent.articles.updated_at IS          '最近更新时间。';

-- =====================================================================
-- v2 规则引擎表：rule_sets
-- =====================================================================
COMMENT ON TABLE seo_agent.rule_sets IS
  '规则版本化容器，承载 seo-standard.json 整文件快照。同一 family 最多 1 行 active（由 rule_sets_one_active_per_name 局部唯一索引保证）；业务键为 (name, version)，bigint 自增 PK 仅在内部使用。';

COMMENT ON COLUMN seo_agent.rule_sets.id                IS 'bigint 自增主键，GENERATED ALWAYS AS IDENTITY；仅供内部 FK 使用，外部请用 (name, version) 自然键。';
COMMENT ON COLUMN seo_agent.rule_sets.name              IS '规则族名，例如 seo-standard。';
COMMENT ON COLUMN seo_agent.rule_sets.version           IS '版本字符串，SemVer 或 YYYY-MM-DD，在 (name, version) 内唯一。';
COMMENT ON COLUMN seo_agent.rule_sets.source            IS '来源枚举：file（JSON 文件导入）/ db（界面编辑）/ api（程序化写入）。';
COMMENT ON COLUMN seo_agent.rule_sets.payload           IS '整文件 JSON 对象，结构与 workflows/seo-standard.json 兼容。';
COMMENT ON COLUMN seo_agent.rule_sets.is_active         IS '当前是否激活；同 family 最多 1 行 true（部分唯一索引）。';
COMMENT ON COLUMN seo_agent.rule_sets.effective_at      IS '该版本开始生效的时间（<= now() 才视为生效）。';
COMMENT ON COLUMN seo_agent.rule_sets.created_by        IS '写入者标识，例如 admin / seed:db/scripts/seed_rule_baseline.mjs。';
COMMENT ON COLUMN seo_agent.rule_sets.notes             IS '本次版本变更说明，便于审计。';
COMMENT ON COLUMN seo_agent.rule_sets.parent_version_id IS '可选的版本谱系指针，从该版本 fork 而来；ON DELETE SET NULL。';
COMMENT ON COLUMN seo_agent.rule_sets.created_at        IS '行创建时间。';
COMMENT ON COLUMN seo_agent.rule_sets.updated_at        IS '最近更新时间，由 set_updated_at 触发器维护。';

-- =====================================================================
-- v2 规则引擎表：rule_field_map
-- =====================================================================
COMMENT ON TABLE seo_agent.rule_field_map IS
  '反向索引：JSON 规则键 ↔ 代码硬编码位置。在硬编码迁移期写入；消费方为前端规则编辑面板与审计工具。业务唯一键 (rule_set_id, rule_layer, rule_key)。';

COMMENT ON COLUMN seo_agent.rule_field_map.id            IS 'bigint 自增主键，仅内部使用；业务查询用 (rule_set_id, rule_layer, rule_key)。';
COMMENT ON COLUMN seo_agent.rule_field_map.rule_set_id   IS '所属规则版本 → rule_sets.id，ON DELETE CASCADE；删版本会同步清掉其字段映射。';
COMMENT ON COLUMN seo_agent.rule_field_map.rule_layer    IS '逻辑层枚举：classifier / scorer / content_plan / assets / locale / workflow / output_format / references / anchor / images / global_plan。';
COMMENT ON COLUMN seo_agent.rule_field_map.rule_key      IS 'payload 内部点路径，小写、下划线分段、点分隔。';
COMMENT ON COLUMN seo_agent.rule_field_map.display_name  IS '人类可读名，供前端面板展示。';
COMMENT ON COLUMN seo_agent.rule_field_map.value_type    IS '声明的 JSON 值类型：number / string / boolean / array / object；由 check_default_value_type 触发器校验 default_value。';
COMMENT ON COLUMN seo_agent.rule_field_map.default_value IS 'payload 缺该键时的回退默认值。';
COMMENT ON COLUMN seo_agent.rule_field_map.source_file   IS '原始硬编码所在文件路径（相对仓库根）。';
COMMENT ON COLUMN seo_agent.rule_field_map.source_line   IS '原始硬编码所在行号（1-based，可空）。';
COMMENT ON COLUMN seo_agent.rule_field_map.is_migrated   IS 'true = 对应硬编码已移除并改为读 payload；false = 仍硬编码在代码里。';
COMMENT ON COLUMN seo_agent.rule_field_map.notes         IS '迁移备注，例如"v0.3 已删除 hardcoded coefficient"。';
COMMENT ON COLUMN seo_agent.rule_field_map.created_at    IS '行创建时间。';
COMMENT ON COLUMN seo_agent.rule_field_map.updated_at    IS '最近更新时间。';

-- =====================================================================
-- v2 规则引擎表：rule_audit_log
-- =====================================================================
COMMENT ON TABLE seo_agent.rule_audit_log IS
  '规则生命周期审计日志。仅 INSERT，永不 UPDATE。每次规则版本激活 / 停用 / 创建 / 回滚都必须留痕。';

COMMENT ON COLUMN seo_agent.rule_audit_log.id            IS 'bigint 自增主键。';
COMMENT ON COLUMN seo_agent.rule_audit_log.rule_set_id   IS '被审计的规则版本 → rule_sets.id，ON DELETE RESTRICT；删除版本必须先处理审计。';
COMMENT ON COLUMN seo_agent.rule_audit_log.action        IS '生命周期动作枚举：activate / deactivate / create / rollback / supersede。';
COMMENT ON COLUMN seo_agent.rule_audit_log.actor         IS '操作者标识（人或自动化阶段名）。';
COMMENT ON COLUMN seo_agent.rule_audit_log.reason        IS '操作理由自由文本。';
COMMENT ON COLUMN seo_agent.rule_audit_log.snapshot_diff IS '与上一版本（或首次创建时的 baseline）的差异 jsonb。';
COMMENT ON COLUMN seo_agent.rule_audit_log.created_at    IS '事件时间。';

-- =====================================================================
-- v2 视图
-- =====================================================================
COMMENT ON VIEW seo_agent.v_active_rule_set IS
  '当前生效的活跃规则版本视图（is_active=true 且 effective_at <= now()）。FastAPI 引擎层热路径首选入口。';

COMMENT ON VIEW seo_agent.v_active_rule_field_map IS
  '当前活跃规则版本下的字段映射视图。前端规则编辑面板的数据源。';