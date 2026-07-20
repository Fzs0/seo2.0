# SEO Workbench 项目进度

更新时间：2026-07-20（Asia/Shanghai）

> 新窗口先阅读本文，再检查 `git status --short`。工作区有大量已有修改，禁止 `git reset --hard`、批量回滚或覆盖无关文件。
>
> 维护约定：跨模块、数据库结构、核心策略链路或发布行为的大改动，必须同步更新本文；设计草案、开发中和已上线状态必须明确区分，不能覆盖既有历史。

2026-07-18 测试数据重置：已清空 `seo_agent.tasks`、`seo_agent.articles`、`seo_agent.keywords` 与 `seo_agent.serp_snapshots`，用于重新验证 Semrush 主题导入和新策略流程；未删除远端文章。2026-07-19 用户已手动导入 Strategy Builder 真实文件；最近一次只读核对为 `keywords=1,274`、`tasks=224`、`articles=0`、`serp_snapshots=10`、`posts=135`、`post_analyses=292`。策略候选、今日计划和执行任务仍为 0；`tasks` 主要包含页面簇 AI 历史和 107 条内容诊断记录，不能等同于待执行策略。

## 0. 当前结论

| 环节 | 当前状态 | 结论 |
|---|---|---|
| Strategy Builder 导入与页面簇校验 | 已实现并使用真实文件验证 | 1,274 个关键词、159 个页面簇已入库 |
| 页面簇 AI 分站 | 已执行 | 120 个可用簇完成分析；96 个已分站、24 个 Hold，39 个复核簇未进入 AI |
| 页面簇关联文章库存与策略候选 | 代码已验证，正式策略数据已清空 | 需要重新扫描并从页面生成候选 |
| 今日计划、人工审核与执行前重验证 | 代码已验证 | 当前没有待审核或待执行策略 |
| QA、幂等发布和远端回读 | 代码已验证 | 本轮真实新文/旧文发布尚未验收；外站写入继续要求人工审核 |
| T+0 基线与 T+7/14/28/56/90 效果观察 | 代码已验证 | 尚无本轮真实策略和跨时间效果样本 |
| 主站电商 SEO 内容分层 | 已实现只读 V1 | 可查看产品页/分类页/支持文章职责；主站文章候选和生文尚未接入 |
| 当前运行环境 | 未部署本轮最新代码 | 用户需通过根目录 `start-backend.bat` 手动重启 |

当前最近的可执行动作只有一个：手动重启后，在“今日策略”重新扫描并生成候选，先人工批准 1 条低风险策略，核对策略、执行、发布和 `strategy_effect` 记录。真实验收完成前，不能把本轮代码描述为已上线闭环。

## 0.1 跨窗口恢复点（2026-07-20）

当前主线已从“先做主站文章”收敛为“先建立主站商业页面归属，再生成支持文章”：

```text
四类主站索引已导入
  → 主站内容页查看产品页/分类页/文章库存
  → GSC / SERP 关键词归属到产品页或分类页
  → 发现商业页面覆盖缺口
  → 生成绑定转化 URL 的支持文章候选
  → Brief → 大纲 → 草稿 → 人工审核 → 发布
```

- `exdivo` 是主站，当前 `strategy_enabled=false`；不要为了写主站文章打开普通博客策略。
- `products / collections / pages / posts` 四类索引已经支持一次选择并合并；URL 清单全量去重保留，页面诊断暂不阻塞内容规划。
- 站点索引更新已支持覆盖旧库存：单文件上传默认替换旧索引；多文件选择时首个文件覆盖、后续文件合并；支持 GZip `.gz`。当前只保存解压后的 URL/页面结构，不保留原始压缩文件。
- 新增左侧“主站内容”页面和 `GET /api/v1/sites/{site_id}/main-content`，当前只读展示页面职责，不写数据库、不生成文章、不发布。
- 最新本地验证：项目根目录 `tests/` 为 `223 passed`；前端 TypeScript/Vite build 通过；Python compileall 通过；未启动/停止/重启后端。
- 最近一次数据库只读事实仍以 2026-07-19 记录为准：`keywords=1,274`、`tasks=224`、`articles=0`、`serp_snapshots=10`、`posts=135`、`post_analyses=292`；本轮未重新读取数据库。
- 用户下窗口首先手动通过根目录 `start-backend.bat` 重启，再打开“主站内容”验收 `exdivo` 的四类页面数量和职责分层。

Google GSC/GA4 OAuth 的 `RS256` 依赖已修复：现有 `.venv` 已安装 `cryptography==44.0.2`，本地签名自检和 `pip check` 通过；当前后端进程仍需用户通过 `start-backend.bat` 手动重启后才会加载。

2026-07-20 策略执行 SQL 类型错误已修复：执行前重验证中同时作为 UUID 和 JSONB 文本使用的 `analysis_batch_id`、`current_execution_id` 已显式转为 `text` 参与 JSON 字段比较；相关 62 项回归通过。
2026-07-20 策略重试与 URL 修复已完成本地验证：`has_keyword` 现在按词序匹配正文/H1/标题并接受连字符等标点变体；同一执行任务重试会复活原 `strategy_effect`，不会新增观察记录；新文 slug 改用主关键词并限制为 70 个字符；同步远端文章时会回写关联文章的 `published_url`/slug 和效果观察目标 URL。尚未重启后端或执行真实数据库/远端验收。
2026-07-20 效果观察重复显示修复已完成本地验证：失败未发布的观察记录现在会写入 `inconclusive` 并在效果列表隐藏；同一执行任务按 `execution_task_id` 去重，重试优先复用未取消记录或复活原记录。尚未清理数据库历史记录，页面查询会先隐藏和去重。
2026-07-20 主站索引导入已扩展为多文件合并：站点页可一次选择 `products / collections / pages / posts` 等 XML/TXT 索引，后端全量保留去重后的 URL 清单，并将页面诊断限制在安全扫描上限内；页面读取失败、title、description、H1 仍只作为后续诊断项，尚未修复。
2026-07-20 站点地图更新闭环已补齐：`/sites/{site_id}/index-scan` 默认覆盖旧索引，支持 `replace=false` 追加合并；页面提供本地上传入口，自动识别并解压 GZip。未启动后端，尚未执行真实上传验收。
2026-07-20 站点地图 URL 抓取因 Cloudflare Challenge 对后端请求返回 403，已移除 URL 抓取入口，保留本地上传覆盖方案。
2026-07-20 已将主站业务知识与多渠道自动策略闭环方案，以及迁移前检查清单归档到 `TXT/main-site-business-knowledge-strategy-2026-07-20/` 和 `TXT/migration-2026-07-20/`；本次仅建立迁移检查点，尚未部署或执行真实数据库迁移。
2026-07-20 已补充压缩包迁移手册 `TXT/migration-2026-07-20/SEO2_MIGRATION_GUIDE_2026-07-20.md` 和可直接交给 MiniMax 的整体迁移提示词 `TXT/migration-2026-07-20/MINIMAX_PROJECT_MIGRATION_PROMPT_2026-07-20.md`；真实密钥、数据库备份和 exports 数据仍需单独转移。
2026-07-20 已按 Google/Shopify 电商 SEO 资料增加主站内容分层 V1：新增只读“主站内容”页面和 `/sites/{site_id}/main-content` 接口，把产品页/分类页定义为商业承接层，把博客定义为支持层；暂不自动生成或发布主站文章，普通博客策略开关保持关闭。

## 1. 当前主线

闭环基础以 [strategy-closed-loop-2026-07-17.md](TXT/strategy-close-loop-2026-07-17/strategy-closed-loop-2026-07-17.md) 为准；策略治理和后续自治方向以 [STRATEGY_POLICY_V1_DRAFT.md](docs/STRATEGY_POLICY_V1_DRAFT.md) 草案为准：

```text
当前业务全部启用站点扫描
  → 文章结构化分析
  → Semrush / GSC / GA4 / SERP 证据汇总
  → 生成完整策略候选池
  → 人工配置今日动作数量和站点配额
  → 风险分级与人工审核
  → 审核通过后自动生文、更新或发布
  → 保存执行结果
  → 7 / 14 / 28 / 56 / 90 天复盘
  → 下一轮策略调整
```

当前所有外站写入不能跳过人工审核。未来数据积累后，AI 可在业务预算、风险和权限边界内自主配置每日动作并执行低中风险任务；删除、合并、301、slug、canonical、noindex 和跨站调整等高风险操作继续要求人工逐条确认。

关键词主线已纠正：不再在“生成今日策略”前批量分析并分配全部关键词。统一流程按来源证据能力分流，最终汇合为页面簇；完整定义见 [KEYWORD_IMPORT_PIPELINE_V1.md](docs/KEYWORD_IMPORT_PIPELINE_V1.md)。Strategy Builder 专用解析、只读预览、人工确认写入、文件内 TOP 10 页面簇边界校验、按簇 AI 站点分配及同站文章库存关联已实现并通过真实数据只读预演；页面手动验收和第一批策略执行尚未进行。Topic 列表、基础关键词和 GSC 来源分支目前只完成设计，不能描述为已实现。

相关方案文档：

- `TXT/strategy-close-loop-2026-07-17/strategy-closed-loop-2026-07-17.md`
- `TXT/content-fetching-2026-07-17/article-content-fetching.md`
- `TXT/blog-sites-and-strategy-tracking-2026-07-17/blog-sites-and-strategy-tracking-2026-07-17.md`
- `TXT/page-cluster-inventory-2026-07-19/page-cluster-inventory-strategy-2026-07-19.md`
- `docs/STRATEGY_POLICY_V1_DRAFT.md`（候选池、今日计划、稳定身份、28 天更新冷却和效果观察 V1 已实施；新证据提前解锁与自治仍是草案）
- `docs/KEYWORD_IMPORT_PIPELINE_V1.md`（来源能力分流与页面簇校验 V1）

## 2. 当前真实状态

### 站点与文章

- 当前有 8 个活跃站点，本地 `posts` 共 135 条，其中 3 条为 `remote_missing`。
- 2026-07-19 最近一次只读核对为 `tasks=224`、`articles=0`、`keywords=1,274`、`serp_snapshots=10`；策略候选、计划和执行记录为 0，远端文章没有删除，`posts=135`、`post_analyses=292` 保留。
- 独立 knowledge PostgreSQL 已通过 `knowledge/docker-compose.yml` 在本机 `127.0.0.1:5434` 启动，使用独立 volume `knowledge-system-postgres-data`；此前 `knowledge` schema 下的表已清空，目前仅重新创建 Claim-only 表 `knowledge.claims`，并从 `exports/knowledge_claims_2026-07-18` 导入 153 条记录（approved 107、pending 6、rejected 40）。sources/documents/evidence/usages 及其他知识系统表当前不存在，主业务侧尚未接入新的 Claim 检索/API。
- `exdivo` 是主站，`is_main=true`；站点知识画像已清空。
- 主站暂不参与自动关键词策略、自动生文和自动发布，后续单独审查产品页、分类页和博客页。
- 主站内容 V1 已能读取索引库存并区分产品页、分类页、支持文章和其他页面；下一步才是将 GSC/SERP 词簇映射到商业页面，再生成绑定转化 URL 的文章候选。
- 自动内容策略只允许 `site_type=blog` 或 `site_type=wp`。
- 站点已增加 `business_id` 和 `strategy_enabled`：当前 `exdivo` 业务包含 6 个启用的博客/WP 站点；主站属于 `exdivo` 但关闭策略；`HealthyOxy Shopify` 无业务归属且关闭策略。策略范围按业务和开关判断，不再用站点类型代替业务归属。

### 文章读取

- WP REST 正文读取正常，Rank Math / Yoast TDK 已统一映射。
- 自定义博客站列表接口缺正文时，会请求公开文章 HTML 补齐正文和 TDK。
- Shopify 已映射 GraphQL `body` / `summary`。
- 个别远端 404 页面仍需标记为“远端失效”，不能当作普通正文缺失。

### SERP 与竞争文章

- SERP API 快照已持久化，包含关键词、自然结果、相关问题、相关搜索和原始响应。
- 已根据 SERP URL 抓取竞争页面 HTML，并提取标题层级、字数、段落、列表、表格、图片、内外链和 FAQ 信号。
- 竞争特征目前主要保存在 SERP 快照 JSON 中，尚未形成独立的长期结构化分析表。
- 竞争差距已接入内容审查 AI，但还没有完全进入全局策略的统一排序链路。

### 策略与执行

- 当前策略、候选、计划、执行和发布任务数据已全部清空；下面描述的是已经实现并验证过的能力，不代表数据库中仍有现成任务。下一轮必须重新扫描、生成候选和计划。

- `strategy_service` 已改为直接消费当前业务最新全站诊断，不再从 `keywords.ai_review.strategy` 重建候选；关键词池仍提供相关性、意图和站点分配证据，但不应继续作为每日策略的手动前置步骤。
- 现有文章诊断和新文机会都会进入候选池；新文没有正文是正常状态，不再因此强制 Hold。旧文更新可以没有 `keyword_id`，但必须锁定真实 `post_id` 和最新 `post_analysis`。
- 没有确认站点知识的启用站点仍会被分析，其候选明确进入 Hold，不会被静默丢弃，也不会为凑配额跨站点移动关键词。
- 站点知识生成已移除“已分配关键词”这一循环证据；站点范围只能由站点配置、已发布文章、产品和索引扫描推导，候选关键词不能反向扩张 `in_scope_topics`。
- 扫描和策略生成必须显式指定 `businessId`；关键词 AI 分析也按业务隔离，不再全局清理分配或取消任务。
- 重新进行关键词 AI 分析时不再改写任何 `review` 记录；已保存的内容诊断、候选、计划和审核历史保持不变。
- 策略生成会保存当前业务全部合格候选，不再把分析结果截断为 4 条；“4”现在只是今日计划的默认动作预算，人工可以设置总预算、各站点配额并勾选候选，预算允许为 0。
- 分析批次、完整候选池和今日行动计划已复用 `seo_agent.tasks` 持久化，并分别使用 `payload.kind=strategy_analysis_batch / strategy_candidate / strategy_plan`；进入执行链的条目继续使用既有 `seo_strategy`，因此没有新增表或迁移。
- 候选与今日计划按 `business_id` 隔离。生成前必须存在当前业务的新全站扫描批次；旧的未绑定业务扫描不会被复用，缺少有效扫描时接口返回 HTTP 400，且不会清空旧计划或写入空批次。
- 已执行候选不可再次进入计划；审核执行前会重新检查今日计划、分析批次、关键词业务归属、站点开关和更新文章的最新分析证据。
- 审核还会核对策略分析引用的源扫描 ID 和扫描时间；只要重新运行全站扫描，旧计划就不能继续审批执行，必须重新生成候选。
- 策略默认需要人工选择；选择后在同一接口中完成审核并精确启动该策略，页面刷新不会中断后台任务。
- 旧 `/workflow/article-generate` 和 `/workflow/article-pipeline` 正式生文旁路已关闭；没有经过候选池、今日计划和人工审核的关键词 AI 结果不能直接生文。
- `content_audit_batch` 和每条 `content_audit` 现在每次扫描新增不可变记录，不再覆盖旧策略引用的诊断证据。
- 运行中任务只有点击“停止执行”才会取消；进入外站发布阶段后拒绝停止，避免远端已发布、本地却标记取消。
- 后台执行器每 30 秒领取已批准任务，执行文章生成并按配置自动发布。
- `strategy_policy_v1` 的业务归属、站点策略开关、完整候选池、今日行动计划、人工配额、稳定策略身份、执行前证据重验证、重复/冷却锁和效果追踪 V1 已实现。策略效果复用 `seo_agent.tasks` 保存 T+0 基线、发布结果及 T+7/14/28/56/90 检查点；新证据提前解锁、风险授权和逐步自治仍未实施。
- 正式发布仅允许来自人工批准的 `seo_strategy` 执行链；发布前按远端 ID/slug 做幂等复用，发布后回读核对远端 ID、标题、URL/slug 和公开状态。文章页只保留只读发布预检，不能绕过今日策略直接正式发布。
- 已完成一次真实 WP 自动新文发布测试：`vapetopline` 远端文章 ID `302` 发布成功。
- WP 与自定义博客均已接入按远端文章 ID 更新；博客使用 `PUT /api/open/v1/posts/{id}` 和 Bearer Open API Key，尚未做真实旧文内容写入测试。

### 当前操作界面

- 前端默认进入“今日策略”，日常操作固定为：`扫描站点 → 生成候选 → 保存今日计划 → 审核执行 → 效果观察`。
- 完整操作为：`导入 Semrush 主题文件 → 预览并确认页面簇 → 按页面簇分配站点 → 扫描站点 → 生成候选 → 人工配置预算与配额 → 审核执行 → 效果观察`。关键词页不再要求对几万行关键词逐条 AI 分析。
- 当前通用导入器仍不适用于 Keyword Strategy Builder：`Page` 会被误当成 URL，`Topic` 会被当成宽泛聚类，TOP 10 竞争 URL 仅停留在 `raw`。关键词页已新增独立专用适配、只读预览和人工确认导入；正式关键词池只能通过该专用入口写入此类文件。
- 主导航已从 12 个入口缩减为 5 个：今日策略、关键词、文章、数据复盘、站点。
- 总控台、机会引擎、执行管线、风险治理、规则配置、SERP 洞察和同步状态已从产品导航及加载链路移除；相关后端能力和历史数据未删除。
- 全站扫描的 AI 复核明细继续保存在数据库并进入候选生成，不再作为一整块重复结果铺在主页面。
- 手动“执行整个队列”“检查并执行队列”“清空队列”和重复的右侧监控已移除；审核通过后继续由现有后台执行器运行，停止/移除单个任务仍保留。
- 站点配额、候选筛选和长篇证据默认折叠，需要时再展开。
- 本地后端运行约束：只能通过根目录 `start-backend.bat` 启动或重启；Codex 不得直接执行 `uvicorn` 、`Start-Process` 或终止后端进程。
- 远端文章被删除或改为不公开后，必须先做站点库存对账，将本地 `posts` 标记为失效并阻止旧文候选；不能只删远端记录。

### 已移除的旧流程

- 内容编排页面、内容排期接口和旧排期数据已移除。
- 旧排期任务、日志和排期标记已清理；已发布文章保留。
- 不要恢复旧的“按日期批量编排文章”流程。

## 3. 当前接口链路

```text
POST /api/v1/workflow/content-audit/scan  body.businessId 必填
  → 扫描文章、补 SERP、AI 复核内容候选

POST /api/v1/workflow/strategies/generate  body.businessId 必填
  → 保存业务分析批次和完整候选池，并按今日预算生成计划

GET /api/v1/workflow/strategies/candidates?business_id=...
  → 分页查看完整候选池，Hold 单独展示且不可选择

GET /api/v1/workflow/strategies/plan?business_id=...
PUT /api/v1/workflow/strategies/plan
  → 查看或保存今日动作预算、站点配额和人工勾选结果

GET /api/v1/workflow/strategies?status=pending
  → 查看待审核策略

POST /api/v1/workflow/strategies/{task_id}/review
  → executeNow=true 时人工批准并立即启动指定策略；否则仅批准或拒绝

POST /api/v1/workflow/strategies/{task_id}/stop
  → 主动停止当前进程中的运行任务

GET /api/v1/workflow/strategies/effects?business_id=...
  → 查看 T+0 基线、观察检查点、冷却期和当前效果结论

GET /api/v1/workflow/semrush-strategy/ai-analyze/latest?business_id=...
  → 页面刷新后恢复当前业务仍在运行的页面簇 AI 任务

GET /api/v1/sites/{site_id}/main-content
  → 只读查看主站产品页、分类页、支持文章和内容规则

后台 automation_service
  → 自动领取已批准的执行任务

POST /api/v1/publish
  → 新文发布；传入 update_post_id 时按站点连接器更新旧文
```

## 4. 已完成的基础能力

- 原始关键词导入、本地初筛、通用 AI 分析、通用主题聚类和站点匹配；Semrush Strategy Builder 专用页面簇解析、预览和人工确认写入已完成。
- 站点业务归属、策略开关、全站文章同步和正文/TDK 读取。
- 主站多文件索引合并、URL 库存保存和产品页/分类页/支持文章职责分层只读页面。
- 文章结构化分析、SERP 快照、竞争页面结构解析和内容审查 AI。
- 以最新全站诊断为入口的完整候选池、今日计划、人工配额、审核和执行前重验证。
- 站点知识、竞争差距、推荐动作和内链计划已注入文章大纲与正文 Prompt。
- QA、策略任务、执行任务、发布任务和 WP/自定义博客远端 ID 更新链路。
- 前端今日策略页面、人工选择/立即执行、刷新恢复和无用旧编排模块清理。

## 5. 按闭环文档尚未完成

### P0：主站电商内容规划与生文（当前下一条主线）

已完成：按 Google/Shopify 电商 SEO 资料建立产品页、分类页、支持文章的职责分层；主站内容页面只读读取四类索引，并保存规则文档 `docs/MAIN_SITE_ECOMMERCE_SEO_V1.md`。

尚未完成：将 GSC/SERP 查询映射到真实产品页或分类页，识别一个搜索意图的唯一主承接页，生成文章候选并绑定 `conversion_target_url`；之后复用现有 Brief、大纲、文章、QA 和人工审核链路。主站产品事实、价格、库存、配送和限制声明必须来自真实站点/产品数据；不得把主站直接接入普通博客自动策略。

### P0：Semrush Strategy Builder 导入、页面簇校验与按簇 AI（已完成真实数据验证，待页面验收）

已完成：专用解析器按 `Keywords` sheet 映射并保留 `Topic / Page / Page type / Keyword / Intent / TOP 10`；新增只读预览、独立确认导入和已导入批次幂等整理。页面簇校验 V1 输出 `validated / provisional / bridge_review / split_review`，不删除词或自动拆簇。真实文件为 1,274 条关键词、159 个页面簇：已验证 72、暂可用 48、桥接复核 12、拆簇复核 27。2026-07-19 已对 120 个可用簇完成一次按簇 AI：96 个簇分配到同市场/语种的启用站点，24 个整簇 Hold；39 个复核簇没有进入 AI。同簇多站为 0，跨市场分配为 0，Semrush 源 `Topic / Page / Page type` 偏差为 0。AI 只写 `ai_review`、站点、优先级和状态，不覆盖源字段。关键词页已增加启动、进度轮询和停止操作。规则已保存并激活为 `seo-standard 0.2.5`、`keywordImportPolicy 1.0.0`；代码改动仍需用户通过 `start-backend.bat` 手动重启后才能由当前后端进程使用。

2026-07-19 已实现页面簇库存关联 V1：只处理 96 个已分站可用簇，每簇只判断一次；只匹配同站 108 篇可用文章，复用 URL、TOP10、主关键词、Meta、标题和低频核心词证据。0 个可靠/弱匹配生成一条新文候选，1 个可靠匹配且有诊断缺口生成更新候选，多文章、跨簇冲突、弱匹配或缺少正文覆盖证据均进入 Hold；AI 只能维持本地动作或降级 Hold。真实数据库只读预演结果为页面簇新写 56、页面簇 Hold 40（潜在蚕食 3、跨簇边界 2、弱匹配复核 25、正文覆盖复核 10）；13 篇独立文章技术更新中有 2 篇涉及页面簇 Hold 并被隔离，最终保留 11 篇，合计 107 个候选。没有写入 tasks、没有执行策略、没有生成或发布文章。代码需由用户通过 `start-backend.bat` 手动重启后再页面验收。

2026-07-19 已在“今日策略”页面接入当前业务策略清空操作：清空会取消并隐藏当前候选批次、今日计划和未执行策略，页面立即刷新为空状态；诊断快照、关键词、文章库存、SERP、文章分析及已有执行历史均保留。后端拒绝在仍有运行任务时清空，页面使用原生二次确认。该功能已实现，待用户手动重启后页面验收。

尚未完成：39 个复核簇的 SerpApi 补查/人工拆簇、页面手动验收，以及真实策略的跨 7 至 90 天观察验证。稳定策略指纹、执行前效果基线和长期效果回流代码已完成本地验证，但尚未由用户重启后跑第一批真实策略。Topic 列表、基础关键词和 GSC 分支仍是设计。

### 已完成：每篇文章的本地结构化分析

需要稳定保存：

```text
标题 / Meta / 主关键词 / 字数 / H1-H6 / FAQ
内链 / 外链 / 图片 / 发布时间 / 修改时间 / 正文来源
```

已通过 `post_analyses` 按内容哈希幂等保存；正文或 SEO 元数据变化时生成新版本，重复同步只刷新同一版本。

### P0：竞争文章结构分析正式入库

把竞争页面特征从 SERP 原始 JSON 中抽成可查询记录，并与关键词、SERP 快照和策略关联。AI 不应每次重复读取整篇竞争正文。

### P0：策略全过程记录

需要完整串起：

```text
输入证据 → AI 判断 → 人工审核 → 执行动作 → 发布结果 → 效果结果
```

全过程关联 V1 已实现，复用 `seo_agent.tasks` 的 `payload.kind=strategy_effect` 保存稳定身份、T+0 基线、发布结果、检查点、当前结论和冷却截止时间，不新增迁移。效果检查每小时独立运行，只读 GSC/GA4 后写本地观察记录，不领取策略、不触发外站写入。真实跨 7 至 90 天效果尚无时间样本，不能描述为线上已验证。

### P0：策略记忆与治理规则

设计草案见 `docs/STRATEGY_POLICY_V1_DRAFT.md`。业务归属、策略开关、业务分析批次、完整候选池、今日行动计划、人工配额、跨批次稳定策略指纹、规则版本、28 天更新冷却、同意图新文永久重复锁和效果记录已实现，并复用现有任务历史；仍需补风险等级、新证据提前解锁、后续策略关联和 AI 历史摘要。

当前已完成业务范围隔离、“分析池与今日动作计划分离”和效果观察 V1；更长期的策略记忆、因果实验和自治治理仍停留在设计阶段。

### P1：真实旧文更新测试

选择一个 WP 或自定义博客文章，生成更新内容后写回原远端 ID，并验证原 URL / slug、本地文章、发布任务和策略结果均正确。

### P1：发布后复盘

发布后至少检查第 7、14、28、56 天；新页面、低样本和站点级变化延长到第 90 天。读取 GSC 展示、点击、排名、CTR，以及 GA4 落地页访问和参与度。无数据时标记 `inconclusive`，不能直接判定失败。

### P2：AI 受约束自主规划与执行

等策略效果数据积累后，让 AI 根据历史成功率、业务目标、站点产能、风险和成本自行配置每日动作数量与站点分配，并自动执行授权范围内的低中风险动作。L3 高风险操作永久保留人工确认；当前所有外站写入继续人工审核。

## 6. 最近验证结果

> 以下大部分为 2026-07-18 数据重置前的历史验证证据。当前策略候选、计划和执行数据已清空且 `articles=0`，不能把历史策略任务数当作现状。

```text
当前最新回归：项目根目录 `tests/` 223 passed；前端 TypeScript/Vite build、Python compileall、git diff --check 通过。主站内容分层测试和索引合并测试均通过。
WP 自动新文发布：vapetopline 远端文章 ID 302，状态 publish
WP 远端 GET 核验：成功
相关策略 / 任务 / 发布测试：通过
最新非 knowledge 测试：178 passed
页面簇库存关联相关回归：91 passed；Python compileall、前端 TypeScript/Vite build、git diff --check 通过
前端 TypeScript 与 Vite production build：通过
Python compileall：通过
2026-07-19 当前项目根目录 `tests/` 全量回归：215 passed；Python compileall、前端 TypeScript/Vite build、git diff --check 通过。未启动后端、未调用真实远端、未写正式业务数据。仓库级无范围 `pytest` 会额外收集独立 `knowledge/tests`，其独立依赖不在当前 `.venv`，因此本次按约束不纳入验证。
更新任务 QA 失败复盘：`Skywalker Digiflavor: How To Choose Without Overthinking` 首次因 `has_meta_description` 失败；随后两次重试亦失败，真实根因是 AI Meta Description 长 162 字符，超出 160 上限。三次均未触发外站写入。
Meta Description 规范修复：生成结果在 QA 前统一清理并按词边界截断至 160 字符内，不放宽 120–160 硬门槛；对该失败文章纯内存重算为 155 字符，8 项 QA 全部通过。
WordPress 格式首次修复：发布和更新仍使用独立 `title` 字段，Gutenberg 正文转换前移除开头 Markdown H1；但首版仅在 H1 为正文第一项时生效，未覆盖 AI 在前面输出独立 YAML 代码块的情况。
WordPress 格式二次修复：真实任务已将带 YAML 元数据代码块和重复 H1 的正文更新到 vapes2000 远端文章 1798。共享解析器现在支持独立 YAML 代码块与无分隔符 `title / meta_description`；WP 发布端移除第一个 Markdown H1，不再依赖 H1 必须是正文首行。对该已保存原文纯内存验证：YAML=false、H1=false、H2=true；未自动覆盖线上文章。
WordPress 线上修复：经用户明确确认，已使用已保存正文更新 vapes2000 同一篇远端文章 1798，未新建文章、未提交 slug 修改、未重新调用 AI。WordPress 远端 GET 核验：status=publish、slug=skywalker-digiflavor、YAML=false、H1=false、H2=true。
`start-backend.bat` 生命周期控制：已增加 `start / stop / restart / status` 模式、亀00 端口占用保护、`/api/health` 检查和项目父子进程识别；已只读验证 `status` 与已运行时的 `start` 保护，未停止或重启当前后端。
今日策略页面布局复核：候选、计划、执行状态和最近结果分区；候选、计划与结果长列表限高滚动，Hold 默认折叠；1280px 页面无横向溢出且总高约由 5121px 降至 1951px，640px 窄屏无横向溢出，浏览器控制台无错误。
候选池事务冒烟：5 条候选完整保存，默认预算 4 条进入计划，事务回滚后数据库无残留
改造前基线：`exdivo` 11 条候选全部 Hold，可执行 0 条。
改造后真实扫描：6 个启用站点、110 篇文章，生成 26 条诊断（更新 14 / 新写 9 / 原始 Hold 3）；AI 复核 17 条。
改造后真实候选：26 条，可执行 10 条（新写 4 / 更新 6），Hold 16 条；其中 9 条因 `topvapes.de / vape2026.de / vapestest.de` 站点知识未确认而明确阻塞。今日预算 4，已生成 4 条待人工审核计划。
站点知识非循环验证：Prompt、evidence 和 fallback 均不再使用已分配关键词定义站点范围；新增回归测试已通过。
旧策略数据清理：4 条无 `business_id / plan_id / candidate_id` 的旧待审策略和 1 个空计划已标记 `canceled`；已执行和发布历史保留。
无当前业务扫描保护：生成接口返回 HTTP 400，候选数和今日计划均保持不变
本地页面冒烟：完整候选池、今日计划、总预算和站点配额渲染正常；控制台无错误
精简页面冒烟：默认进入今日策略、主导航 5 个入口、四步操作提示正常，刷新后新增控制台错误 0
前端生产包：主 JS 由约 294.40 kB 降至 243.98 kB
本地数据库：134 篇文章，134 篇均有结构化分析记录
业务范围：`exdivo` 启用 6 个内容站；HealthyOxy Shopify 已排除；已分配关键词业务错配 0
业务参数门槛：扫描接口缺少 `businessId` 返回 HTTP 422
博客旧文更新接口无副作用探针：Bearer 鉴权成功到达接口，不存在文章按文档返回 404
今日诊断驱动策略：`best pod vape` 已执行并发布；`best 510 vape battery` 已恢复为 P0 更新策略；另有 2 条 Hold
```

## 7. 新窗口正确开发顺序

1. 用户手动通过根目录 `start-backend.bat` 重启；打开“主站内容”，验收 `exdivo` 的产品页、分类页、文章数量和 URL 分层。
2. 实现主站 GSC/SERP 查询到产品页/分类页的关键词归属；每个搜索意图确定唯一主承接页，文章只承接信息和购买前问题。
3. 在“主站内容”增加“创建内容候选”：目标产品/分类页、主题、意图、转化 URL、内链和事实来源必须完整。
4. 复用现有 Brief → 大纲 → 文章 → QA 链路生成主站草稿；主站先只保存草稿，外站发布继续人工确认。
5. 主站文章真实验收 1 篇后，再处理页面读取失败、title、description、H1、Product/Offer 数据和筛选 URL 等技术问题。
6. 博客站策略仍按既有顺序单独验收：扫描 → 候选 → 今日计划 → 人工审核 → 执行 → 效果观察。

不要先做：旧内容编排恢复、打开主站普通 `strategy_enabled`、主站自动发布、批量改写 80 个页面 SEO 字段、无复盘数据时的 AI 全权接管。

## 8. 新窗口检查命令

```powershell
cd D:\桌面\seo2.0
git status --short
Get-Content -Raw PROJECT_PROGRESS.md
Get-Content -Raw TXT\strategy-close-loop-2026-07-17\strategy-closed-loop-2026-07-17.md
Get-Content -Raw docs\MAIN_SITE_ECOMMERCE_SEO_V1.md
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/health
docker exec pg-workbench psql -U seo -d seo_workbench -X -c "SELECT (SELECT count(*) FROM seo_agent.tasks) tasks, (SELECT count(*) FROM seo_agent.articles) articles;"
Get-Item 'E:\Keywords\vape_pages_2026-06-25-US.xlsx'
```

## 9. 本周周报素材（2026-07-13 至 2026-07-17）

> 本节是截至 2026-07-17 的历史周报素材，不代表 2026-07-19 当前完成状态；当前结论以本文第 0、2、5、6 节为准。

### 本周目标

把 SEO 系统从“定时发文章”推进为“全站诊断驱动策略，人工选择后持续执行并形成可审计结果”的真实闭环。

### 本周完成

1. 完成 8 个站点的文章同步和结构化分析，当前 `posts` 134 篇、134 篇均有 `post_analyses` 记录。
2. 打通全站内容扫描、SERP 快照、竞争页面结构提取、AI 内容诊断和竞争差距字段。
3. 策略大脑开始读取最新内容诊断、站点知识、文章库存、Semrush、GSC、GA4 和 SERP 证据；缺诊断或证据冲突时自动 Hold。
4. 完成“完整候选池 + 今日计划”分层：AI 保存全部合格候选，4 只是默认今日动作预算；前端可人工配置总预算、站点配额和候选，Hold 不可执行。
5. 执行任务改为后端持续运行，刷新页面不会中断；增加人工停止及发布阶段保护。
6. 完成 WP 新文真实发布验证，`vapetopline` 远端文章 ID `302`。
7. 完成自定义博客旧文更新连接器：`PUT /api/open/v1/posts/{id}`、Bearer Open API Key、部分字段更新且不修改 slug；404 无副作用探针通过。
8. `best pod vape` 已完成策略、生成和自动发布；远端 ID `19` 的公开页面可访问。
9. 批准策略、站点知识、竞争差距、推荐动作和内链计划已显式进入大纲及正文 Prompt。
10. QA 已升级为生成和发布双重硬门槛；Meta、标题年份、FAQ、结构、关键词、长度或计划内链失败时禁止发布。
11. 清理旧内容编排和排期流程，避免旧队列继续干扰新策略中枢。
12. 完成 `strategy_policy_v1` 设计草案，明确策略身份、冷却期、新证据解锁、效果评价和 AI 上下文控制；除业务范围隔离外其余尚未实施。
13. 落地站点 `business_id` 与 `strategy_enabled`：当前 `exdivo` 业务启用 6 个内容站，主站关闭自动策略，HealthyOxy Shopify 按业务归属排除；扫描、策略和执行不再依赖 blog/WP 类型硬编码。
14. 完成远端删除对账：`vapestest.de` 外部文章 `15` 的本地记录改为 `remote_missing`，3 条排队审核任务取消，文章分析与已完成历史保留；后续完整同步会自动执行同样的软失效处理。

### 本周数据与验证

```text
当前站点：8 个 active
同步文章：134 篇
结构化分析覆盖：134 / 134
本周内容诊断任务记录：75 条（包含多次扫描）
本周策略任务记录：35 条（包含重生成、驳回和 Hold）
本周本地文章记录：15 条
本周成功 publish 任务记录：11 条（任务数，不等于唯一文章数）
最新非 knowledge 回归：175 passed
前端 TypeScript 与 Vite production build：通过
```

### 本周发现的问题

- 竞争文章结构特征仍主要保存在 SERP JSON，尚未形成独立可查询记录。
- 发布后的第 7 / 14 / 28 天 GSC、GA4 效果回流尚未实现。
- 本地文章目前只能查看和发布，尚无“保存本地 / 保存并同步线上”编辑流程。
- 发布接口缺少完整远端回读和幂等保护；一次人工重复发布曾把本地远端 ID 更新为 `20`，但远端 GET 返回 404。
- `best pod vape` 曾出现标题年份冲突，已由人工在线修正；这推动了标题年份 QA 门槛落地。

### 下周建议

1. 手动验收 Strategy Builder 预览中的四类页面簇校验结果；优先检查 39 个待复核簇。
2. 用户手动验收页面簇库存关联结果；在策略效果基线完成前，不批准第一批执行。
3. 在新一批策略执行前建立基线及 7 / 14 / 28 / 56 / 90 天效果追踪。
4. 发布或更新成功后按远端 ID 回读，验证标题、正文摘要、URL 和状态。
5. 把竞争文章结构从 SERP JSON 正式入库并进入策略排序。
