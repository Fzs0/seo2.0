# API 接口文档

> 基于 2026-07-15 工作区源码静态扫描生成。本文覆盖项目中显式声明的 83 个 HTTP 接口：主服务 69 个、独立知识服务 14 个。FastAPI 自动生成的 `/docs`、`/redoc` 和 `/openapi.json` 未计入接口总数。

## 1. 服务概览

| 服务 | 默认地址 | 路由入口 | 交互式文档 |
| --- | --- | --- | --- |
| SEO Workbench 主服务 | `http://127.0.0.1:8000` | `app/main.py` | `http://127.0.0.1:8000/docs` |
| Local Knowledge 独立知识服务 | `http://127.0.0.1:8010` | `knowledge/backend/app/main.py` | `http://127.0.0.1:8010/docs` |

两个服务当前都没有入站鉴权。知识服务被明确设计为仅绑定本机；主服务启动脚本使用 `0.0.0.0`，因此不应在未增加鉴权和网络访问控制前暴露到不可信网络。

主前端可用 `VITE_API_BASE_URL` 覆盖主服务地址，知识前端可用 `VITE_KNOWLEDGE_API_URL` 覆盖知识服务地址。除 `/metrics` 返回 Prometheus 文本外，请求体和业务响应均使用 JSON；当前没有 SSE、WebSocket、流式响应、文件下载或 multipart 接口。时间字段通常为 ISO 8601 字符串，数据库标识通常为 UUID 字符串。

### 主服务通用行为

- 可在请求中传入 `X-Request-ID`；未传时服务自动生成，并在响应头中回写。
- 参数校验失败返回 `422 application/problem+json`，字段包括 `type`、`title`、`status`、`detail`、`instance`。
- 路由主动抛出的 HTTP 错误使用 FastAPI 默认格式：`{"detail": "..."}`。
- 未处理异常返回 `500 application/problem+json`。
- 标为“外部调用”的接口依赖相应 API Key、站点连接器或网络可用性。

### 知识服务通用行为

- 请求模型禁止额外字段。
- 校验/无效输入返回 `400`，资源不存在返回 `404`，状态冲突返回 `409`，数据库不可用返回 `503`；响应格式为 `{"detail": ...}`。
- FastAPI 生成的 OpenAPI 可能仍把模型校验错误标成 422，但全局异常处理器的实际运行时状态码是 400。
- 服务无认证且包含抓取、导入、审核等写操作，只能在可信的本机环境使用。

## 2. 主服务接口

所有 `/api/v1/...` 路由的基础地址为 `http://127.0.0.1:8000`。

### 2.1 基础设施

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| GET | `/api/health` | 无 | `{ok, name, env}`；进程级健康检查，不检查数据库。 |
| GET | `/metrics` | 无 | Prometheus exposition 文本。 |

### 2.2 工作流标准与导入分析

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/workflow/standard` | 无 | 当前标准配置、版本、来源和健康状态。 |
| POST | `/api/v1/workflow/standard/reload` | 无 | 从数据库重载标准配置，返回 `reloaded: true`。 |
| POST | `/api/v1/workflow/standard` | `StandardBody` | 保存标准配置到 `workflows/seo-standard.json` 并更新内存；缺少 `standard` 时仍返回 200 和 `{error}`。 |
| GET | `/api/v1/workflow/sample` | 无 | 示例项目、关键词、站点和标准版本。 |
| POST | `/api/v1/workflow/analyze` | `AnalyzeBody` | 本地分析关键词，返回 `{keywords, sites, standardVersion}`。 |
| POST | `/api/v1/workflow/import-csv` | `CsvBody` | 解析并初筛 CSV；可选写库，返回关键词、保存数、批次 ID 和初筛统计。 |
| POST | `/api/v1/workflow/import-file` | `FileBody` | 导入文本或 Base64 文件；可选写库，返回内容同 CSV 导入并附文件名。 |
| POST | `/api/v1/workflow/semrush-strategy/preview` | `FileBody`（`.xlsx`，`contentBase64`） | 只读取 `Keywords` sheet，返回 Topic/Page 页面簇汇总、Pillar/Sub、意图、Volume/KD、TOP 10 覆盖率和异常；不写数据库、不启动 AI。 |
| POST | `/api/v1/workflow/semrush-strategy/import` | `SemrushStrategyImportBody`（`.xlsx`、`contentBase64`、`businessId`、`market`、`confirmed=true`） | 校验启用业务、市场与文件 Database 后写入关键词池，保留 Topic/Page/Page type、页面簇角色和 TOP 10；不启动 AI、分站或策略。 |

### 2.3 AI 关键词分析、策略与自动化

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/workflow/keywords/ai-analyze` | `KeywordAiAnalyzeBody` | 创建 AI 分析任务，返回运行 ID 和状态。 |
| GET | `/api/v1/workflow/keywords/ai-analyze/{run_id}` | 路径：`run_id` | 查询任务进度/结果；不存在返回 404。 |
| POST | `/api/v1/workflow/keywords/ai-analyze/{run_id}/cancel` | 路径：`run_id` | 取消任务；不存在返回 404。 |
| POST | `/api/v1/workflow/strategies/generate` | `StrategyGenerateBody` | 从候选关键词生成策略任务。 |
| GET | `/api/v1/workflow/strategies` | 查询：`status=pending`、`limit=50`、`site_id`、`search`、`strategy_type`、`priority`、`evidence_level` | 返回 `{items}`。 |
| POST | `/api/v1/workflow/strategies/{task_id}/review` | 路径：`task_id`；`StrategyReviewBody` | 批准或拒绝策略；任务不存在返回 404。 |
| POST | `/api/v1/workflow/strategies/{task_id}/execute` | 路径：`task_id` | 执行已审核策略；无效状态返回 400。 |
| POST | `/api/v1/workflow/strategies/{task_id}/cancel` | 路径：`task_id` | 取消策略；无效状态返回 400。 |
| POST | `/api/v1/workflow/automation/run-once` | 无 | 按当前配置手动运行一轮自动化。 |
| POST | `/api/v1/workflow/automation/clear-queue` | 无 | 清空可清理的策略/执行队列；冲突返回 409。 |
| GET | `/api/v1/workflow/automation/status` | 无 | 当前自动化配置、执行状态和统计。 |
| POST | `/api/v1/workflow/automation/settings` | `AutomationSettingsBody` | 更新根目录 `.env` 中的自动化设置；返回 `restart_required: true`。 |

### 2.4 Brief、Prompt 与文章生成

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/workflow/brief` | `BriefBody` | 生成 Brief；AI 已配置时增强，失败或未配置时回退本地结果。 |
| POST | `/api/v1/workflow/prompt` | `PromptBody` | 返回 `{brief, locale, articleBriefTemplate, prompt}`。 |
| POST | `/api/v1/workflow/article-generate` | `ArticleGenerateBody` | 根据关键词生成并保存文章；无效关键词/状态返回 400。 |
| POST | `/api/v1/workflow/article-pipeline` | `ArticleGenerateBody` | 执行文章生成流水线；业务失败可能以 200 和 `status: failed` 返回。 |
| POST | `/api/v1/workflow/mock-article` | `MockArticleBody` | AI 已配置时生成文章，否则返回本地 Markdown 模板和 `ai-not-configured`。 |
| POST | `/api/v1/database/sync-workspace` | `SyncWorkspaceBody` | 将快照中的站点和关键词 upsert 到数据库，返回写入数量。 |

### 2.5 站点

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/sites` | 查询：`market`、`language_code`、`site_type`、`limit=100`（1..500） | `{items, total}`。 |
| GET | `/api/v1/sites/{site_id}` | 路径：`site_id` | 单个站点；不存在返回 404。 |
| POST | `/api/v1/sites` | `SiteUpsertBody` | 新增或更新站点；校验失败返回 400。 |
| POST | `/api/v1/sites/{site_id}/index-scan` | 原始 XML/TXT 请求体；`x-filename` | 导入主站 Sitemap/URL 清单并合并页面库存。 |
| GET | `/api/v1/sites/{site_id}/main-content` | 路径：`site_id` | 只读返回主站产品页、分类页、支持文章的职责分层和电商内容规则；非主站返回 400。 |
| POST | `/api/v1/sites/sync-config` | 无 | 从项目配置同步站点到数据库。 |
| DELETE | `/api/v1/sites/{site_id}` | 路径：`site_id` | `{deleted: true}`；不存在返回 404。 |
| GET | `/api/v1/sites/{site_id}/connector` | 路径：`site_id` | 检测该站点发布连接器，返回站点信息和连接结果；会发起外部调用。 |
| POST | `/api/v1/sites/{site_id}/posts/sync` | 路径：`site_id`；可选 `SyncPostsBody` | 从一个外部站点同步已有文章。 |

### 2.6 关键词

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/keywords` | 查询：`status`、`priority`、`assigned_site_id`、`min_score`、`market`、`search`、`ai_analyzed`、`intent`、`serp_feature`、`limit=50`（1..500）、`offset=0` | 分页关键词结果。 |
| GET | `/api/v1/keywords/analysis-queue` | 查询：`limit=100`（1..200） | 待 AI 分析队列及统计。该静态路径先于 `/{keyword_id}` 注册。 |
| GET | `/api/v1/keywords/{keyword_id}` | 路径：`keyword_id` | 单个关键词；不存在返回 404。 |

### 2.7 文章、外部文章与发布

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/articles/save` | `ArticleSaveBody` | 保存/更新文章；`title` 必填。 |
| GET | `/api/v1/articles` | 查询：`site_id`、`keyword_id`、`status`、`limit=50`（1..200）、`offset=0` | 分页文章结果。 |
| GET | `/api/v1/articles/{article_id}` | 路径：`article_id` | 单篇文章；不存在返回 404。 |
| GET | `/api/v1/posts` | 查询：`site_id`、`limit=50`（1..200）、`offset=0` | 已从外部站点同步的文章。 |
| POST | `/api/v1/posts/sync-all` | 可选 `SyncPostsBody` | 同步全部已配置站点，每站默认最多 100 篇。 |
| POST | `/api/v1/publish` | `PublishBody` | 创建/执行发布任务；默认 `dry_run=true`，发布参数错误返回 400。 |
| GET | `/api/v1/publish/{task_id}` | 路径：`task_id` | 发布任务状态；不存在返回 404。 |

### 2.8 产品、SERP、图片与站点探测

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/products` | 查询：`site_id`、`status`、`category`、`search`、`min_price`、`max_price`、`limit=50`（1..500）、`offset=0` | 分页产品结果。 |
| GET | `/api/v1/products/by-external/{external_id}` | 路径：`external_id`；可选查询 `site_id` | 按外部 ID 查询；不存在返回 404。连接器数据建议传 `site_id` 消除不同来源的 ID 歧义。该路径先于 `/{product_id}` 注册。 |
| GET | `/api/v1/products/{product_id}` | 路径：整数 `product_id` | 按内部 ID 查询；不存在返回 404。 |
| POST | `/api/v1/site-snapshot` | `SiteSnapshotBody` | 并发探测输入的 API，返回 `{siteResults, total}`；会发起外部调用。 |
| POST | `/api/v1/serpapi` | `SerpApiBody` | 查询 Google SERP；默认 `gl=us`、`hl=en`，需要 SerpApi 配置。 |
| POST | `/api/v1/images/search` | `ImageSearchBody` | 从 Pexels/Unsplash/Pixabay 等图片提供商搜索，需对应 Key。 |

#### 2.8.1 自定义商品数据连接器

自定义连接器 V1 是只读商品导入入口。配置由请求模板、精确域名白名单、分页规则、响应成功条件和字段映射组成；支持 GET/POST、受限 JSONPath 与常用类型转换。敏感值通过 `${secret:名称}` 占位，并使用 `CONNECTOR_SECRET_KEY` 加密保存，接口响应不返回密文或明文。配置须先测试通过，再激活和同步。自建站统一使用 OEMApps 预设，每个站点只需要关联 `site_id` 并配置不同的 Token；Shopify 暂不接入该预设。

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/connectors/preview` | `{config, response, limit?}` | 使用粘贴的 JSON 样例验证映射，不发起外部请求；返回标准商品、映射错误和 SEO 缺口。 |
| POST | `/api/v1/connectors/test-request` | `{config, secrets?, limit?}` | 测试未保存配置的真实只读请求；响应不回显 secret 或原始上游记录。 |
| POST | `/api/v1/connectors` | `{name, site_id?, config, secrets?}` | 保存草稿及版本；secret 加密保存。 |
| POST | `/api/v1/connectors/oemapps` | `{site_id, token}` | 创建或轮换自建站 OEMApps 连接器；列表地址、分页和商品字段映射均使用内置预设。 |
| GET | `/api/v1/connectors` | 无 | 连接器列表。 |
| GET / PUT | `/api/v1/connectors/{connector_id}` | PUT：`{name?, site_id?, config?, secrets?}` | 查看脱敏配置或创建新草稿版本。 |
| POST | `/api/v1/connectors/{connector_id}/test` | 无 | 使用已保存 secret 测试当前版本；成功后记录响应结构指纹并标记已验证。 |
| POST | `/api/v1/connectors/{connector_id}/activate` | 无 | 仅激活当前已验证版本。 |
| POST | `/api/v1/connectors/{connector_id}/sync-products` | 无 | 拉取全部受限分页并按 `(source_connector_id, external_id)` 幂等写入产品表，同时保存 SEO 审计结果。 |
| GET | `/api/v1/connectors/{connector_id}/versions` | 无 | 查看历史版本及验证状态。 |
| GET | `/api/v1/connectors/{connector_id}/runs` | 无 | 查看最近测试/同步运行记录。 |
| POST | `/api/v1/connectors/{connector_id}/products/{product_id}/seo-update/preview` | SEO patch | 实时读取商品详情，返回 TDK/图片 ALT 差异、商品快照哈希和 variant ID；不写外部站点。 |
| POST | `/api/v1/connectors/{connector_id}/products/{product_id}/seo-update/execute` | SEO patch、`expected_snapshot_hash`、`confirm_variant_recreation=true` | 快照未变化时将批准字段合并进完整商品结构并执行单商品 PUT，随后回读验证；保存更新审计。 |
| GET | `/api/v1/connectors/{connector_id}/seo-update-runs` | `limit?` | 查看 OEMApps 商品 SEO 写回记录和 variant ID 前后变化。 |

网络保护包括：仅允许 HTTPS 443、逐次校验跳转目标、DNS 解析结果必须全部为公网地址、精确主机白名单、超时和响应大小限制、JSON 内容类型校验。通用自定义连接器保持只读；只有固定域名的 OEMApps 适配器提供显式 SEO 写回，且要求连接器已激活、单商品预览、快照匹配、variant 重建确认、完整审计和写后回读。

### 2.9 Google Analytics / Search Console

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/analytics/sources` | 无 | 脱敏后的 Google 数据源 `{items, total, configError}`，不暴露 SA 私钥。 |
| GET | `/api/v1/analytics/sources/by-site/{site_id}` | 路径：`site_id` | `{site, source, configured, recentSyncLog}`；站点不存在返回 404。 |
| POST | `/api/v1/analytics/sync` | `AnalyticsSyncBody` | 手动同步单个 source、单个站点或全部 source；会调用 Google API。 |
| GET | `/api/v1/analytics/sync-log` | 查询：`site_id`、`source_id`、`limit=50`（1..500） | `{items, total}`，按最新开始时间倒序。 |
| GET | `/api/v1/analytics/dashboard/{site_id}` | 路径：`site_id` | 站点元信息、GSC/GA4 28 天汇总、7 天趋势和最近同步记录。 |
| GET | `/api/v1/analytics/gsc/queries` | 查询：必填 `site_id`；`days=28`（1..90）、`limit=100`（1..500）、`min_impressions=0` | `{items, total, days}`。当前实现固定读取 28 天视图并返回 `days: 28`。 |
| GET | `/api/v1/analytics/gsc/opportunities` | 查询：必填 `site_id`；`days=28`、`min_impressions=100`、`max_position=20`、`limit=20` | 高展示且接近首页的关键词及筛选条件。当前实现固定读取 28 天视图。 |
| GET | `/api/v1/analytics/gsc/pages` | 查询：必填 `site_id`；`limit=50`（1..500） | GSC 页面级 28 天聚合。 |
| GET | `/api/v1/analytics/gsc/breakdown` | 查询：必填 `site_id`；`dimension=device`（`device`/`country`）、`days=28`、`limit=20` | 按设备或国家聚合；维度非法返回 400。 |
| GET | `/api/v1/analytics/ga4/overview` | 查询：必填 `site_id`；`days=28`（1..90） | `{summary, trend, days}`。当前汇总与趋势固定使用 28 天。 |
| GET | `/api/v1/analytics/ga4/channels` | 查询：必填 `site_id`；`days=28`（1..90） | 渠道聚合。当前实现固定使用 28 天。 |
| GET | `/api/v1/analytics/ga4/landing-pages` | 查询：必填 `site_id`；`days=28`（1..90）、`limit=50`（1..500） | 落地页聚合。 |
| GET | `/api/v1/analytics/ping` | 无 | 对每个数据源执行 GSC 和 GA4 鉴权/探活，会调用 Google API。 |

### 2.10 管理接口

管理路由前缀为 `/admin`，当前同样没有鉴权。

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| POST | `/admin/reload` | 无 | 从数据库重载规则，返回版本和生效时间。 |
| GET | `/admin/rule-sets/active` | 无 | 当前激活规则集；没有激活项时返回 404。 |
| GET | `/admin/rule-sets` | 无 | 最近 50 个规则集，返回数组。 |
| POST | `/admin/rule-sets` | `RuleSetBody` | 保存规则集，可选择立即激活；业务校验失败返回 400。 |
| GET | `/admin/health` | 无 | 数据库可达性和规则存储状态；数据库失败也以 200 返回并在 `ok/db_error` 中表达。 |

## 3. 主服务请求模型

`?` 表示可选；未特别说明的对象字段允许任意 JSON 结构。

| 模型 | 字段 |
| --- | --- |
| `StandardBody` | `standard?: object` |
| `AnalyzeBody` | `keywords: object[] = []`，`project?: object` |
| `CsvBody` | `csv: string = ""`，`project?: object`，`save: boolean = false`，`filename?: string` |
| `FileBody` | `filename: string = ""`，`contentBase64?: string`，`contentText?: string`，`project?: object`，`save: boolean = false` |
| `KeywordAiAnalyzeBody` | `keywordIds: string[] = []`，`limit: integer = 10`，`opportunityType?: string` |
| `StrategyGenerateBody` | `siteId?: string`，`limit: integer = 20`，`minImpressions: integer = 20` |
| `StrategyReviewBody` | `approved: boolean` |
| `AutomationSettingsBody` | `enabled: boolean = false`，`intervalSeconds: integer = 3600`（60..604800），`batchSize: integer = 1`（1..5），`minImpressions: integer = 20`（0..1000000） |
| `BriefBody` | `keyword?: object`，`project?: object`，`aiStage?: object` |
| `PromptBody` | `keyword?: object`，`project?: object`，`briefOverride?: string`，`brief?: string` |
| `ArticleGenerateBody` | `keywordId: string` |
| `MockArticleBody` | `keyword?: object`，`project?: object`，`brief?: string`，`prompt?: string` |
| `SyncWorkspaceBody` | `snapshot: object = {}` |
| `SyncPostsBody` | `limit: integer = 100` |
| `SiteSnapshotBody` | `apis: object[] = []` |
| `SerpApiBody` | `keyword: string`，`gl?: string`，`hl?: string` |
| `ImageSearchBody` | `provider: string = "pexels"`，`query: string`，`per_page: integer = 10`，`page: integer = 1` |
| `PublishBody` | `article_id: string`，`site_id?: string`，`dry_run: boolean = true`，`actor?: string` |
| `AnalyticsSyncBody` | 三选一：`sourceId: string`、`siteId: string` 或 `all: true`；可附 `daysBack?: integer`、`skipGsc?: boolean`、`skipGa4?: boolean` |
| `RuleSetBody` | `name: string`，`version: string`，`source: string = "api"`，`payload: object`，`notes?: string`，`set_active: boolean = false`，`actor?: string` |

### `SiteUpsertBody`

除布尔值默认 `false` 外，字段均可选。兼容 snake_case 与 camelCase：

- 标识与基本信息：`site_key/siteKey`、`name`、`site_type/siteType`、`domain`。
- 地址：`base_url/baseUrl`、`api_base_url/apiBaseUrl`。
- 地区语言：`market`、`language_code/languageCode`、`google_gl/googleGl`、`google_hl/googleHl`、`semrush_database/semrushDatabase`。
- 内容规则：`content_role/contentRole`、`content_scope/contentScope`、`is_main/isMain`、`allow_external_links/allowExternalLinks`。
- 连接配置：`publish_config/publishConfig`、`api_config/apiConfig`。
- 其他：`status`、`notes`、`raw`。

### `ArticleSaveBody`

仅 `title: string` 必填。其他字段可选并兼容以下 snake_case/camelCase 别名：

- 关联：`task_id/taskId`、`site_id/siteId`、`keyword_id/keywordId`、`serp_snapshot_id/serpSnapshotId`。
- 内容：`slug`、`target_url/targetUrl`、`status`、`language_code/languageCode`、`market`、`brief_md/briefMd`、`prompt_text/promptText`、`content_md/contentMd`、`content_html/contentHtml`、`article_parts/articleParts`。
- SEO：`meta_title/metaTitle`、`meta_description/metaDescription`、`primary_keyword/primaryKeyword`、`secondary_keywords/secondaryKeywords`。
- 计划与检查：`internal_link_plan/internalLinkPlan`、`image_plan/imagePlan`、`references_plan/referencesPlan`、`qa_checklist/qaChecklist`。
- 生成元数据：`generation_provider/generationProvider`、`generation_model/generationModel`、`raw_ai_response/rawAiResponse`。

## 4. 知识服务接口

统一前缀：`http://127.0.0.1:8010/api/v1/knowledge`。

| 方法 | 路径 | 请求 | 响应/说明 |
| --- | --- | --- | --- |
| GET | `/health` | 无 | `{status: "ok", database: "reachable", schema: "initialized"}`；数据库或 schema 异常返回 503，此时 `detail` 是包含状态信息的对象。 |
| GET | `/overview` | 无 | source/document/usage 总数和 pending/approved/rejected Claim 数。 |
| GET | `/sources` | 查询：`limit=100`（1..200）、`offset=0` | `{items: Source[]}`。 |
| GET | `/documents` | 查询：`limit=100`（1..200）、`offset=0` | `{items: Document[]}`。 |
| GET | `/claims` | 查询：`review_status?`（`pending`/`approved`/`rejected`）、`limit=100`（1..200）、`offset=0` | `{items: ClaimListItem[]}`。 |
| POST | `/documents/import` | `ImportDocumentRequest` | 导入用户提供的正文，提取 pending Claims；`rights_confirmed` 必须为 true，重复内容不会重复创建。 |
| POST | `/documents/import-url` | `ImportUrlRequest` | 安全抓取一个公开 HTTP(S) 页面并导入；`rights_confirmed` 必须为 true，且受 SSRF、重定向、超时和大小限制保护。 |
| POST | `/batch/preview` | `BatchSourceSpec` | 发现并预览 Sitemap/RSS/Atom 中符合范围的文章；会发起外部请求，但不写数据库，且 `rights_confirmed` 必须为 true。 |
| POST | `/batch/runs` | `BatchSourceSpec` | 创建持久化批量抓取任务；`rights_confirmed` 必须为 true。 |
| GET | `/batch/runs/{run_id}` | 路径：UUID `run_id` | 批次状态、计数、警告和时间。 |
| GET | `/batch/runs/{run_id}/items` | 路径：UUID `run_id` | `{items: BatchRunItem[]}`。 |
| POST | `/batch/runs/{run_id}/cancel` | 路径：UUID `run_id` | 请求取消批次并返回最新批次状态。 |
| POST | `/claims/{claim_id}/review` | 路径：UUID `claim_id`；`ReviewClaimRequest` | 批准或拒绝 Claim，返回含来源、文档和证据的 Claim。 |
| POST | `/retrieve` | `RetrieveRequest` | 只检索 approved Claims，返回 `items` 和可直接交给 Agent 的 `knowledge_pack`；每个命中会写入一条 usage 记录。 |

## 5. 知识服务请求模型

所有模型都拒绝未声明的额外字段。

### `ImportDocumentRequest`

| 字段 | 类型 | 必填/默认 | 约束 |
| --- | --- | --- | --- |
| `source_name` | string | 必填 | 1..300 字符，非空白。 |
| `canonical_url` | string/null | null | 最长 2048；HTTP(S)，禁止凭据。 |
| `title` | string | 必填 | 1..1000 字符，非空白。 |
| `channel` | enum | `seo` | `seo`、`social`、`forum`、`video`、`visual`。 |
| `content_type` | string | `article` | 1..100 字符。 |
| `language_code` | string | `en` | 1..20 字符。 |
| `market` | string/null | null | 最长 50。 |
| `author` | string/null | null | 最长 300。 |
| `published_at` | datetime/null | null | ISO 8601。 |
| `raw_content` | string | 必填 | 不得为空白。 |
| `rights_confirmed` | boolean | 必填 | 必须为 `true`，确认调用方有权处理正文。 |

### `ImportUrlRequest`

| 字段 | 类型 | 必填/默认 | 约束 |
| --- | --- | --- | --- |
| `url` | string | 必填 | 公开 HTTP(S) URL，不得包含凭据。 |
| `rights_confirmed` | boolean | 必填 | 必须为 `true`。 |
| `source_name` | string/null | null | 最长 300。 |
| `channel` | enum | `seo` | 同上。 |
| `language_code` | string/null | null | 1..20。 |
| `market` | string/null | null | 最长 50。 |

### `BatchSourceSpec`

| 字段 | 类型 | 必填/默认 | 约束 |
| --- | --- | --- | --- |
| `seed_url` | string | 必填 | 公开 HTTP(S) URL。 |
| `source_name` | string | 必填 | 1..300，非空白。 |
| `discovery_mode` | enum | `auto` | `auto`、`sitemap`、`feed`。 |
| `years` | integer/null | null | 1..100；与 `date_from` 互斥。 |
| `date_from` | datetime/null | null | 与 `years` 互斥。 |
| `include_unknown_dates` | boolean | `false` | 是否纳入无日期条目。 |
| `max_articles` | integer | `100` | 1..5000。 |
| `channel` | enum | `seo` | 同上。 |
| `language_code` | string/null | null | 1..20。 |
| `market` | string/null | null | 最长 50。 |
| `rights_confirmed` | boolean | 必填 | 必须为 `true`。 |

### `ReviewClaimRequest`

| 字段 | 类型 | 必填/默认 | 约束 |
| --- | --- | --- | --- |
| `decision` | enum | 必填 | `approved` 或 `rejected`。 |
| `reviewer` | string | 必填 | 1..200，非空白。 |
| `note` | string/null | null | 最长 2000。 |

### `RetrieveRequest`

| 字段 | 类型 | 必填/默认 | 约束 |
| --- | --- | --- | --- |
| `query` | string | 必填 | 1..1000，非空白。 |
| `channel` | enum/null | null | `seo`、`social`、`forum`、`video`、`visual`。 |
| `market` | string/null | null | 最长 50。 |
| `language_code` | string/null | null | 最长 20。 |
| `limit` | integer | `5` | 1..20。 |

## 6. 知识服务核心响应对象

| 对象 | 主要字段 |
| --- | --- |
| `Source` | `id`、`name`、`channel`、`source_type`、`base_url`、`domain`、`rights_confirmed`、`status`、`metadata`、时间字段、可选计数。 |
| `Document` | `id`、`source_id`、`canonical_url`、`title`、`content_type`、语言/市场/作者/发布时间、`content_hash`、`status`、`metadata`、时间字段、可选 `source`/`claim_count`。 |
| `Claim` | `id`、`document_id`、`channel`、`topic`、`knowledge_type`、`statement`、条件/例外/建议、`confidence`、审核字段、`metadata`、时间字段。 |
| `Evidence` | `id`、`claim_id`、`document_id`、`excerpt`、`locator`、`created_at`。 |
| `ImportDocumentResponse` | `created`、`duplicate`、`source`、`document`、`claims_created`、`extraction_method`、`model`、`warning`。重复导入时后三项可为 null。 |
| `BatchPreviewResponse` | 发现/可选/未知日期/已选数量、`sample_items`、`warnings`、实际 discovery mode。 |
| `BatchRun` | `id`、状态、各状态计数、警告、错误和创建/开始/结束时间。 |
| `RetrieveResponse` | `items: RetrieveItem[]` 和 `knowledge_pack: {query, filters, items}`；每项包含 Claim、Source、Document、Evidence 与 `score`。 |

## 7. 调用示例

主服务健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

创建 AI 关键词分析任务：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/workflow/keywords/ai-analyze \
  -H "Content-Type: application/json" \
  -d '{"keywordIds":[],"limit":10}'
```

检索已审核知识：

```bash
curl -X POST http://127.0.0.1:8010/api/v1/knowledge/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query":"internal linking best practices","channel":"seo","limit":5}'
```

## 8. 源码索引

| 范围 | 源文件 |
| --- | --- |
| 应用装配、健康检查、指标 | `app/main.py` |
| 工作流、策略、自动化、内容生成 | `app/api/v1/endpoints.py` |
| 站点、关键词、文章、产品、发布 | `app/api/v1/business.py` |
| GSC / GA4 | `app/api/v1/analytics.py` |
| 管理接口 | `app/api/admin/manage.py` |
| 主服务错误和追踪约定 | `app/middleware/errors.py`、`app/middleware/trace.py` |
| 知识接口 | `knowledge/backend/app/api.py` |
| 知识请求/响应模型 | `knowledge/backend/app/schemas.py` |
| 知识服务错误映射 | `knowledge/backend/app/main.py` |

## 9. 前端集成注意事项

- 主前端和知识前端当前使用的所有路径都能在后端找到对应路由；知识前端不主动调用 `/health`，但契约测试覆盖它。
- 主前端列表普遍依赖 `{items: [...]}`；关键词分页还依赖 `total`、`limit`、`offset`。知识前端未向 sources/documents/claims 传分页参数，因此只读取默认前 100 条。
- GSC opportunities 使用 camelCase 字段 `avgPosition`、`lastSeen`，其中 `lastSeen` 可为 null；GSC pages/breakdown 则保留 snake_case `avg_position`、`last_seen`。
- 关键词 AI 状态由前端约每 1.5 秒轮询一次；知识批任务约每 3 秒轮询状态和条目。它们不是流式接口。
- `frontend/dist` 当前构建产物落后于 `frontend/src`，未包含源码中已经接入的部分新接口。本文以当前源码和路由为准，不代表旧构建产物的可用功能集合。
