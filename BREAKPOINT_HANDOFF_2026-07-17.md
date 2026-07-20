# SEO Workbench 断点接力文档

更新时间：2026-07-19（Asia/Shanghai）  
用途：上下文不足、进程中断或新任务接手时，从这里继续，不要重新推演已经确认的方案。

## 1. 新任务读取顺序

1. 阅读本文件。
2. 阅读 `PROJECT_PROGRESS.md`。
3. 阅读 `docs/STRATEGY_POLICY_V1_DRAFT.md`。
4. 执行 `git status --short`，保留工作区所有既有修改。
5. 默认不要扫描或修改 `knowledge/`。

## 2. 当前目标

当前主线已经从“逐关键词分站”调整为“Semrush 页面簇驱动策略”：

```text
Semrush Keyword Strategy Builder 导出
→ 本地筛选并保留 Topic / Page / Page type
→ 用文件内 TOP 10 URL 校验页面簇
→ AI 按页面簇分配站点
→ 关联当前业务全部启用站点的文章库存和诊断
→ 保存完整候选池
→ 人工配置今日动作总数和站点配额
→ 风险分级与人工审核
→ 执行并回读验证
→ 7 / 14 / 28 / 56 / 90 天复盘
→ 策略记忆参与下一轮决策
```

“4”只是当前今日动作额度，不是分析数量上限。终极形态是 AI 在业务目标、数据质量、风险和预算边界内自主配置动作；高风险动作永久保留人工确认。

## 3. 最新断点：2026-07-18

- 当前 `seo_agent.tasks=2`（一条页面簇 AI 完成任务、一条取消任务）、`articles=0`、`keywords=1,274`、`serp_snapshots=0`；用户已手动导入 Strategy Builder 真实文件。
- 未删除远端文章；`seo_agent.posts` 135 条、文章分析版本 `seo_agent.post_analyses` 292 条保留。
- 真实适配样本：`E:\Keywords\vape_pages_2026-06-25-US.xlsx`，`Keywords` sheet 共 1,274 条关键词、159 个 `Page` 页面簇、8 个 `Topic`、8 个 Pillar page、151 个 Sub page。
- 当前通用导入器不能直接使用该文件：`Page` 会被误当 URL，Semrush 页面簇会被通用聚类覆盖，TOP 10 URL 只保留在 `raw`。专用 Strategy Builder 入口已经实现。
- 关键词页支持“只读预览 → 页面簇本地校验 → 选择业务和市场 → 人工确认导入”；确认只写关键词池，不启动 AI、分站或策略。校验流程见 `docs/KEYWORD_IMPORT_PIPELINE_V1.md`。
- 页面簇校验 V1 已用真实文件验证：159 簇中已验证 72、暂可用 48、桥接复核 12、拆簇复核 27；120 簇可进入下一阶段按簇 AI，39 簇需先复核。
- 当前 1,274 条记录已回填页面簇校验、中心词和簇内角色；120 个可用簇已完成按簇 AI，96 个分配站点、24 个 Hold；39 个复核簇未进入 AI。同簇多站、跨市场分配和 Semrush 源字段偏差均为 0。
- 页面簇库存关联 V1 已实现但尚未页面验收：96 个已分站簇只按代表词处理一次，只匹配同站库存；真实数据库只读预演为新写 56、Hold 40（潜在蚕食 3、跨簇边界 2、弱匹配复核 25、正文覆盖复核 10），13 篇独立文章更新中 2 篇因涉及页面簇 Hold 被隔离，最终保留 11 篇，合计 107 个候选。预演没有写入任务或执行策略。方案见 `TXT/page-cluster-inventory-2026-07-19/page-cluster-inventory-strategy-2026-07-19.md`。
- 第一批新策略执行前必须完成策略指纹、执行前基线和 7 / 14 / 28 / 56 / 90 天效果快照；否则新的测试仍无法评价。
- Claims 知识库接入暂缓，不阻塞当前关键词主线。
- 后端只能由用户通过根目录 `start-backend.bat` 启动或重启；Codex 不得直接启动、停止或重启后端。

以下章节保留为已完成背景，新的开发顺序以 `PROJECT_PROGRESS.md` 第 7 节为准。

## 4. 已完成背景：业务范围锁定

已新增：

```text
seo_agent.sites.business_id text
seo_agent.sites.strategy_enabled boolean default false
```

迁移文件：`db/migrations/015_site_strategy_scope.sql`。

已经贯穿以下链路：

- 站点配置读取与保存。
- 关键词 AI 可选站点与保存前业务一致性检查。
- 策略扫描时的文章同步。
- 文章、关键词、GSC、GA4、SERP 数据读取。
- 内容扫描批次和 AI 复核记录。
- 策略生成、策略列表、人工审核。
- 真正执行前再次校验业务归属和策略开关。
- 前端业务选择、参与站点和排除站点展示。

扫描和策略生成接口现在必须显式提交：

```json
{
  "businessId": "exdivo"
}
```

缺少 `businessId` 返回 HTTP 422。业务没有启用站点时返回 HTTP 400。

已移除策略链路中的 `blog/wp + 非主站` 业务替代判断。Shopify 是否参与由 `business_id + strategy_enabled` 决定，而不是站点类型。

## 5. 当前本地数据库配置

当前业务 ID：`exdivo`。

启用策略的 6 个站点：

```text
topvapes.de
vape2026.de
vapes1999
vapes2000
vapestest.de
vapetopline
```

其他站点：

```text
exdivo：business_id=exdivo，strategy_enabled=false
HealthyOxy Shopify：business_id=NULL，strategy_enabled=false
```

当前 `vape_broad-match_us_2026-07-15.csv` 导入的 19,250 个关键词已设置 `business_id=exdivo`。已分配关键词与目标站点的业务错配数为 0。

当前计数：`tasks=2`（仅页面簇 AI 历史任务）、`articles=0`、`posts=135`、`serp_snapshots=0`、`post_analyses=292`。旧候选、计划、执行和发布任务已经删除，下一轮必须从新扫描开始。

环境配置只写入本地数据库，没有把 `exdivo` 或具体站点名单硬编码进迁移文件。

## 6. 历史主要修改文件

```text
db/migrations/015_site_strategy_scope.sql
db/migrations/001_agent_memory_schema.sql
db/README.md
app/api/v1/business.py
app/api/v1/endpoints.py
app/services/site_service.py
app/services/post_sync_service.py
app/services/content_audit_service.py
app/services/keyword_ai_service.py
app/services/strategy_service.py
frontend/src/types/domain.ts
frontend/src/hooks/useData.ts
frontend/src/pages/SitesPage.tsx
frontend/src/pages/ContentPage.tsx
tests/test_content_audit.py
tests/test_post_sync_service.py
tests/test_strategy_service.py
tests/test_task_stability.py
PROJECT_PROGRESS.md
```

工作区在本轮开始前就有大量未提交修改。禁止使用 `git reset --hard`、批量 checkout 或覆盖无关文件。

## 7. 历史验证结果

> 本节是清理前的历史验证，不代表当前数据库仍有任务或文章生成记录。

```text
主项目测试：156 passed
Python compileall：通过
前端 TypeScript + Vite production build：通过
git diff --check：通过，仅有既有换行符警告
015 migration 重复执行：通过
后端健康检查：通过
GET /api/v1/sites：正确返回 business_id / strategy_enabled
POST content-audit/scan 缺少 businessId：HTTP 422
本地数据库启用站点：6
关键词业务错配：0
```

验证命令：

```powershell
cd D:\桌面\seo2.0
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q app
npm --prefix frontend run build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

不要直接执行不带路径的 `pytest`，它会收集独立的 `knowledge/tests`；本轮验证范围是 `tests/`。

## 8. 第一阶段已完成：候选池与今日计划

已建立“完整候选池 + 人工今日配额”，解除“分析最多四条”的错误限制。

当前实现：

1. 复用 `seo_agent.tasks` 保存业务分析批次、全部候选和今日行动计划，没有新增表。
2. 候选生成不再截断为 4；默认今日预算仍为 4，可设为 0 至 200。
3. 前端分别展示“完整候选池”和“今日计划”，支持总预算、单站配额和人工勾选。
4. Hold 独立展示且不能选择；已执行候选不能再次进入计划。
5. 执行前重新验证当前今日计划、分析批次、关键词业务归属、站点开关和更新文章证据。
6. 清空操作改为按业务取消队列与计划，保留历史记录，不跨业务影响任务。

验证结果：`156 passed`、Python compileall 通过、前端 production build 通过；事务冒烟验证 5 条候选可完整保存且预算 4 条进入计划。

清理前曾完成 `exdivo` 业务级候选、计划和执行验证；2026-07-18 已清空相关任务数据。当前候选、计划和执行记录均为 0，下一次真实验证必须使用 Semrush 页面簇新流程重新生成。

### 当前页面怎么用

当前前端仍默认进入“今日策略”，现有四步操作是：

1. 扫描当前业务全部启用站点。
2. 生成或刷新完整候选池。
3. 勾选候选并保存今日计划；总预算必填，站点配额可选。
4. 在今日计划中人工审核并立即执行；刷新页面不会停止后台任务。

主导航只保留：今日策略、关键词、文章、数据复盘、站点。旧总控台、机会引擎、独立执行管线、风险/规则伪配置、手工 SERP 和独立同步状态已从产品入口移除。扫描明细仍入库并参与策略，不再重复铺满主页面。

关键词页已增加 Strategy Builder 导入预览、确认、页面簇边界校验和按簇 AI 任务操作，并移除旧逐关键词 AI 入口。页面簇库存关联和候选生成已接入全站扫描；下一步是用户重启后做页面验收，再补第一批执行前基线，不能恢复逐关键词手动 AI 分析作为每日前置步骤。

## 9. 尚未完成

- Semrush Strategy Builder 专用解析、预览、人工确认、本地 SERP 边界校验、按簇 AI 分站及文章库存关联已实现；页面验收和复核簇处理尚未完成。
- 当前任务和本地生成文章记录均为 0，需要完成适配后重新扫描并生成第一批可追踪策略。
- 跨批次稳定策略指纹、冷却、新证据解锁和历史摘要尚未接入代码。
- 风险动作字典和审批等级尚未固化到任务字段。
- 竞争文章结构仍主要保存在 SERP JSON，未独立结构化入库。
- 发布后 7 / 14 / 28 / 56 / 90 天效果回流尚未实现。
- 自定义博客真实旧文更新、执行前快照、远端回读、幂等和回滚仍待完成。
- 订单不可用时的 `business_ready / proxy_ready / engagement_only / data_broken` 数据等级尚未实现。

## 10. 已确认的业务规则

- 商业目标优先使用自然搜索净履约利润；缺成本时使用净履约收入。
- 订单数据缺失不能阻断所有策略，但 GA4 停留时间不能替代订单。
- 数据优先级：真实订单/线索 → 加购/结账/商品点击 → 互动时间等内容指标。
- 只有互动数据时只能诊断内容，不能宣称产生商业收益。
- AI 可以分析全部候选；前期具体执行数量由人工配置。
- 外站写入前期继续人工审核。
- URL、slug、canonical、noindex、跳转、删除、合并、跨站和批量动作必须人工确认。
- 页面刷新不能终止后台任务；只有用户主动点击停止才取消，进入外站发布阶段后拒绝停止。

## 11. 不要做

- 不要恢复旧内容编排和按日期批量发文流程。
- 不要把 4 条动作重新解释为全站分析上限。
- 不要用站点类型代替业务归属。
- 不要在没有效果回流时开放 AI 全权执行。
- 不要把 GA4 停留时间包装成订单或收入结果。
- 不要把设计草案写成“已经上线”。
- 不要直接用当前通用导入器写入 `vape_pages_2026-06-25-US.xlsx`，否则会丢失 Semrush 页面簇语义。
- 不要提交、删除或覆盖用户已有的无关修改。
