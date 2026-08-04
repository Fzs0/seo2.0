# SEO Workbench 项目进度

2026-08-03 AI 持续实验与 Hold 收敛已完成代码实现：非硬阻塞站点不再允许以普通
全量 Hold 完成 Run，Zero Action Review 返回 `SAFE_EXPERIMENT_REQUIRED`，要求继续
研究其他文章、新主题、产品页、分类页或 On-page。冷却仍只限制精确 URL/主题；
GSC/GA4 数据少、关键词缺失和一般编辑不确定性不能作为整站硬阻塞。Research
Portfolio 与 Proposed Action 新增兼容的 `evidence_level=high|medium|low|unsafe`，
低证据安全动作可以执行，`unsafe` 不得进入正式计划。整站硬阻塞只接受
`CAPABILITY_MISSING`、`SITE_DISABLED`、`SITE_CONFIGURATION_INCOMPLETE` 三类结构化
原因；旧 `all_hold_review_passed` 和 `STRATEGY_STAGNATION` 路径已退役。验证结果：
主项目全量 `624 passed, 32 skipped`，PG17 门禁 `30 passed`，Skill 合同、日志自检、
官方快速验证、本地操作边界均通过；本地后端已加载当前源码，PID `30560`，
`source_drift=false`。本轮未执行业务站远程写入。详细规则见
`docs/AI_LED_SEO_CONTINUOUS_EXPERIMENT_POLICY_2026-08-03.md`。

2026-08-01 AI Hold 判断已收紧为 URL/主题级：Research Portfolio 不再接受泛化
动作类别冒充机会；每个机会必须绑定具体 URL/对象或 `intent_key + topic_cluster`，
并记录逐项结果。即使组合中其他站点存在可执行动作，后端仍会独立审查每个 Hold
站点；存在未调度的合格机会、未覆盖现有页面/新主题、未穷尽机会 ID，或把目标
冷却扩大为整站冷却时，统一返回 `research_revision_required`。契约版本为
`ai-led-strategy-v2`，无数据库 migration。详细说明见
`docs/AI_URL_TOPIC_COOLDOWN_DECISION_CONTRACT_2026-08-01.md`。

更新时间：2026-07-31（Asia/Shanghai）

2026-07-30 AI On-page 统一 Action 已完成后端接入：AI 仍以 `on_page_fix`
提交编辑决策，Formal Plan 转换为具体首页、产品、分类或图片 ALT Action；新增
精确 `(connector_type, action_type)` Router、OEMApps 首页/产品/分类 Adapter
和 Shopify 产品 SEO Adapter。Run-local 会从现有数据库校验业务、站点、本地
资产、远端 ID、公开 URL 与连接器归属；审批绑定能力、before、patch、目标、
Adapter 版本及平台副作用确认；写后独立回读，不一致只生成一次 P1 且不创建
正向观察。成功动作建立 T+0/7/14/28/56/90 天页面级观察。旧 workflow
On-page 旁路保持退役，前端未修改，仍为只读看板。Custom OpenAPI 未声明安全
写入和独立回读时继续 Hold；Shopify 首页/分类继续 Hold；WordPress TDK 继续
走 `update_article`。本轮没有连接生产数据库，也没有执行任何真实远端写入。

> 新窗口先阅读本文，再检查 `git status --short`。工作区有大量已有修改，禁止 `git reset --hard`、批量回滚或覆盖无关文件。
>
> 维护约定：跨模块、数据库结构、核心策略链路或发布行为的大改动，必须同步更新本文；设计草案、开发中和已上线状态必须明确区分，不能覆盖既有历史。
>
> 内容运营日志约定：每次文章生成或旧文更新必须同时保存一份总运行日志和每篇文章一份独立日志。独立日志至少包含目标用户、搜索意图、SERP/GSC/GA4/产品 API/权威来源依据、文章结构理由、重要编辑取舍、更新前后全文结构、精确模型、发布任务和效果观察。完整规范见 `logs/content-run-log-standard.md`；日志记录可审计依据，不记录或伪造模型私有思维链。

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
| 自定义商品数据连接器 | 后端已实现并完成本地验证 | 通用连接器保持只读；支持配置、样例预览、真实请求测试、版本、激活、分页同步和 SEO 缺口审计；尚未制作前端配置页 |
| 自建站 OEMApps 商品接口 | 后端已实现，待站点配置验收 | 所有自建站共用内置读取/修改协议，每站只配置 Token；SEO 修改要求预览、快照确认、单商品 PUT、审计和回读；Shopify 暂缓 |
| SEO 自主运营后端 V2 | 本机人工审批执行版已实现 | PostgreSQL 17、完整 Strategy Run、全站覆盖、能力快照、统一 Action、执行租约、平台回读、异常脱敏和效果观察已接入；本机回环操作员模式可用，共享或对外部署仍受认证与业务授权门禁阻塞 |
| 正式策略执行链 | 代码与 PG17 门禁已收口 | Action 执行/恢复到终态后自动推动父级 Run 完成验证、观察和收口；`/reconcile` 仅作为幂等人工兜底，不再依赖第二次 `/start` |
| 内容审计长任务 | 已改为异步可轮询 | POST 快速返回批次，同业务进行中任务自动复用；客户端超时不再等同于审计失败 |
| Strategy Run 启动 | SQL 根因已修复，待受控重放 | PG17 参数类型、同键重放、冲突检测和失败标记清理已通过真实数据库测试；原三个 queued Run 尚未重放 |
| 当前运行环境 | 本轮源码已加载并通过指纹校验 | 本地 8000 后端健康，`source_drift=false`；后续源码变化必须重启 |

当前最近的安全动作是：先对 HealthyOxy、Avinoti、Exdivo 现有三个 queued Strategy Run 做一次受控重放，只检查事件、站点覆盖矩阵、能力快照和 Action 预览，不批准或执行远端写入。三站回读通过后，再选择 1 条低风险动作进行人工审批验收。真实回读完成前，不能把 V2 描述为全自动生产上线。

### 2026-07-31 文章 Action 审批发布契约修复

- 修复 `_ensure_approved_execution` 与 `publish_service._approved_execution`
  的审批枚举错配：正式 Strategy Action 现在沿用发布链既有的
  `review_status=approved`，并通过 `approval_source=strategy_action` 保留审批来源。
  Run、Plan、Action、审批、执行、发布和回读顺序均未改变。
- 新增真实 PostgreSQL 17 回归测试，直接执行审批台账创建后再调用现有发布门禁；
  修复前稳定返回 `None`，修复后能按相同 Action/Run 身份回读审批。
- 验证：定向回归 `44 passed`；主项目全量 `625 passed, 26 skipped`；
  PG17 发布门禁 `24 passed`；Python 编译和 `git diff --check` 通过。PG17 使用
  一次性测试库，未连接生产数据库、未执行远端文章或页面写入。
- 本机后端已重启并加载当前工作区源码，PID `29928`，`source_drift=false`。
- 用户已明确授权本机回环操作员边界；`execute-seo-strategy` 升级为合同 1.7，
  只有在 `env=local`、源码无漂移、精确 business/site 范围和完整 Action 门禁
  均通过时，才不会因共享部署认证尚未完成而统一 Hold。对外部署门禁未放宽。
- Skill 新增确定性本机边界验证器，实际回读当前监听仅为
  `127.0.0.1:8000`、PID 与健康接口一致、`env=local`、`source_drift=false`；
  使用错误端口的负向测试被稳定拒绝。Skill 合同、日志自检和官方快速验证均通过。

### 2026-07-31 AI 主导策略正式链路收口

- 新增 `AutonomousStrategyOrchestrator`，正式入口统一为逐站
  Research Portfolio、AI Proposed Actions 和 Zero Action Review；后端只做范围、
  精确目标、能力、受保护字段、风险、冲突、幂等和安全容量门禁，不再替 AI 选题。
- 删除 `run-local-options` 生产路由和旧候选转计划逻辑；候选池仅保留历史只读查询。
  新 Research、Strategy 和 Action 拒绝 `candidate_id`，`keyword_id` 仅能作为通用
  evidence reference。
- 调度保留所有合格动作；`action_budget` 仅作为失控保护，容量外动作进入 Deferred。
  每站当前波次最多一个远程写 Action，调用方不能通过更高站点配额扩大上限。
- 精确 scope lock 同时覆盖 Formal Strategy、Unified Action 和 Observation；同站
  不同 URL/意图互不阻塞。越界 site_id 返回 `SITE_OUT_OF_SCOPE`，不再静默丢弃。
- Zero Action Review 最初支持证据充分的全量 Hold；该行为已被 2026-08-03 的
  `SAFE_EXPERIMENT_REQUIRED` 规则替代，`STRATEGY_STAGNATION` 同步退役。
- `execute-seo-strategy` 已升级为合同 1.9，运行日志改为 Research Portfolio/研究
  方向合同，候选池不再是新 Run 的研究来源。Skill 合同、日志自检和官方快速验证通过。
- 产品决定暂缓地区化实时 SERP/SEMrush GUI 自动采集；在目标地区、语言、设备和
  采集网络明确前不混用不同地区结果。本轮继续支持统一 Evidence Adapter 和固定快照。
- 验证：主项目离线全量 `580 passed, 31 skipped`；PostgreSQL 17.10 发布门禁
  `29 passed`；Skill 三项验证全部通过；未执行生产远端写入。
- 真实只读回读：OEMApps、WordPress、Shopify、Custom OpenAPI 均找到目标文章。
  验收期间发现并修复 WordPress/Custom OpenAPI 运行时凭据未装配及 OEMApps
  Adapter 优先级问题。一次诊断命令曾把本机配置凭据带入工具输出，已记录为 P1；
  必须轮换相关 WordPress Application Password 和博客 Open API Key 后复验。

### 2026-07-30 正式 Strategy 生命周期收口

- 正式生产路由现统一调用 Action 生命周期协调接口：Action 执行或恢复到终态后，
  自动从已持久化 Action 结果推动父级 Run 的 `executing → verifying → observing`
  及最终状态，不再要求客户端额外调用第二次 `/start`。
- 增加 `POST /strategy-runs/{run_id}/reconcile` 幂等人工恢复入口；该入口只做数据库
  状态收口，不调用远端写接口。多个 Action 同时结束时，CAS 冲突会重新读取最新
  Run 状态并有限重试。
- PG17 无网络写入端到端门禁已覆盖
  `Run → Formal Plan → Action → preview → approve → execute → readback
  → Observation → Run completed`，并核对观察任务只创建 1 条。
- 本轮验证：主项目 `593 passed, 19 skipped`；PG17 发布门禁 `17 passed`；
  前端测试 `11 passed`，生产构建通过；修改文件 Python 编译通过。PG17 使用独立
  临时测试库，未连接生产数据库、未执行生产 migration、未调用真实远端写入。
- 本地 8000 后端已通过项目规定的 `start-backend.bat restart` 加载本轮源码；
  健康接口为 `ok=true`、`source_drift=false`，运行态 OpenAPI 为 7 个 Run 路径、
  11 个 Action 路径、旧执行旁路 0。启动脚本本身以前台方式驻留，Codex 调用会
  显示超时，但后端进程和健康检查均正常。

### 2026-07-26 主项目稳定化检查点

- 本轮明确排除 `knowledge/`，只验证主服务、主前端和社媒发布子项目。
- Trendprairie 文章运行时已复用激活 OEMApps 连接器中的加密 Token；真实同步成功抓取并保存 3 篇文章，Token 未写回站点明文配置。
- 文章公开 URL 规则已集中到单一解析模块：OEMApps 主站使用 `/blogs/{slug}` canonical，通用内容 OpenAPI 使用配置模板，已知内容站缺配置时回退 `/blog/{slug}`。历史 62 篇缺 URL 内容站文章已回填，当前数据库 `posts=157`、缺 URL 为 0。
- 旧文效果观察在公开 URL 改变或初始基线没有目标 URL时会把 `baseline_valid` 标记为 false，后续结论固定为 `inconclusive`；新文章的零流量基线保持有效。
- SERP 失败已结构化区分额度、认证、限流和供应商故障；失败结果不再作为缓存、竞争证据或“0 条自然结果”，批量扫描遇到终止型失败立即停止。当前 SerpApi 外部额度仍为 0，预计 2026-08-04 恢复；恢复前不应执行依赖实时 SERP 的正式扫描。
- 历史无错误类型的 SERP 失败由 `030_classify_legacy_serp_failures.sql` 标记为 `legacy_unclassified`，只修复告警语义，不伪造历史根因。
- 后端健康接口新增进程、Git revision、启动时/当前源码指纹和 `source_drift`；`backend-control.ps1` 会拒绝把漂移进程当作当前版本。
- 默认 `pytest` 发现范围固定为主项目和共享社媒 Python 契约，排除 `knowledge/`、`.codex_tmp/`、`outputs/` 等本地副本，避免重复收集。
- 完整验证：Python `397 passed`；主前端 `11 passed` 且生产构建通过；社媒前端 `4 passed` 且生产构建通过；两个执行器分别 `2 passed`、`22 passed`，两个浏览器扩展各 `1 passed`；Python compileall、Git diff 检查通过。运行中主后端健康，OpenAPI 为 138 个路径、151 个操作，站点、文章库存和本地文章接口冒烟均返回 200。

### 2026-07-27 GA4 hostname 隔离与 Exdivo 历史清理

- 根因确认：Exdivo Property `534571138` 同时接收 `exdivo.com`、复制站和 `*.jcysaas.cn` 后台流量；旧本地总览为 11,502 sessions / 24,622 pageviews，并非主站单域数据。
- `GoogleSource` 新增统一 GA4 hostname 白名单；Exdivo 显式允许 `exdivo.com`、`www.exdivo.com`，Avinoti 显式允许 `avinoti.shop`、`www.avinoti.shop`。其他来源未显式配置时回退为各自 GSC 主域名及 `www`，不会再读取整个 Property。
- `GA4Client` 的总览、渠道和落地页请求全部强制带 `hostName inListFilter`；超过 100,000 行时拒绝截断保存。
- GA4 同步改为先完整取得三类报告，再在一个事务中按站点和日期范围替换两张本地表；落地页失败或异常为空时不删除旧数据。
- 清理前已创建 `seo_agent.ga4_session_daily_backup_20260727_exdivo` 与 `seo_agent.ga4_landing_page_daily_backup_20260727_exdivo`。2026-05-01 至 2026-07-26 重建后主站总览为 39 行、5,870 sessions、5,465 users、13,661 pageviews；有效落地页 1,483 行。第二次重跑结果一致。
- Avinoti 同期只检测到 `avinoti.shop` 流量，没有发现 `avinoti.jcysaas.cn` 或其他 hostname 污染；白名单已用于后续防护，未重写其历史。
- 后端已重启，`source_drift=false`；公开 sources API 回读两站白名单正确，数据库健康和 sites API 均通过。
- 完整主项目 Python 回归：`397 passed`；compileall 与 `git diff --check` 通过。

### 2026-07-28 SEO 自主运营后端 V2 与异常修复

- 独立提交 `0b45aed` 建立“安全人工审批执行版”后端：应用启动强制 PostgreSQL 17；Strategy Run 持久化阶段、事件、证据快照和全站覆盖矩阵；Site Capabilities 声明读写、审批、允许字段、副作用和连接器健康；统一 Action 保存 preview、审批哈希、执行租约、心跳、提交补丁、平台回读差异和效果观察。
- Strategy Run 状态禁止从 planning 直接完成。`all_sites` 和 `selected_sites` 均逐站决策，发现站点集合与决策集合不一致时不得完成；可执行决策必须创建统一 Action 并携带该站点完整能力快照。
- Action 审批绑定 `snapshot_hash + patch_hash + capability_snapshot_hash`。缺少能力声明、快照过期、字段越权、连接器异常或关键字段回读不一致时均阻塞；回读不一致创建 P1 异常且不创建正向观察。
- 执行租约支持 Token、领取时间、到期时间、尝试次数和心跳。超时后必须先远端只读分类为未应用、已应用、部分应用或未知；旧 Token 和过期 Token 不能提交结果。
- 平台在回读一致后按 Action 幂等创建第 7、14、28、56 天观察计划。URL、slug、canonical、redirect、删除、价格、库存、Variant、分类成员关系和未声明字段继续禁止。
- 内容审计超过客户端 240 秒的问题已修复：`POST /api/v1/workflow/content-audit/scan` 立即返回持久化批次和 `poll_url`，`GET /api/v1/workflow/content-audit/scans/{batch_id}` 查询状态；同业务并发请求通过 advisory lock 复用一个 queued/running 批次。Exdivo 批次 `3917a44c-26e3-4ac0-be18-04904ac61753` 已确认完成，ANOM-001 标记为 resolved。
- Strategy Run 启动 500 的根因已在真实 PG17 精确复现并修复：`jsonb_build_object` 的 key/value 与 JSONB 查询参数均显式转换为 text；同一幂等键重放原操作，改绑其他操作返回冲突，业务阶段失败时清理已领取标记。ANOM-002 当前为 mitigated/pending controlled replay。
- PostgreSQL 17.10 发布门禁实际结果为 `9 passed`，覆盖全量 migration、031/032 双跑、Hold 多连接并发、Hold Token 超时接管、最新审计批次审批门禁、Action 并发幂等、Strategy Run 启动幂等和内容审计并发复用。普通全量回归为 `536 passed, 9 skipped`；PG17 项由独立门禁强制执行，缺少连接时门禁硬失败。
- 本地 8000 后端已重启并加载当前源码，健康接口为 `source_drift=false`。本次未运行真实策略、未批准 Action、未调用生产 PUT，也未执行生产数据库迁移。
- 当前限制：生产真实写 adapter 在全局认证和 business scope 授权完成前保持禁用；真实回滚和无审批自动执行仍阻塞；内容审计后台任务目前是进程内任务，进程崩溃后批次可见但尚无独立 worker 自动接管；2026-07-28 两项异常修复尚未形成 Git 提交。
- 2026-07-28 发布异常第一轮源码修复完成：OEMApps 将 `1 / "1" / publish / published` 在连接器边界归一化为 `published` 并保留 `raw_status`；Shopify 已支持按 article handle 查重，创建 mutation 单次尝试，异常后只读回读恢复而不盲目重试写入；公开文章 URL 统一使用站点业务域，Exdivo、Avinoti、HealthyOxy 的 article、execution、publish task、effect task 四处一致性合同测试已通过。Shopify 图片上传列入第二轮，真实三站回读仍待用户验收。
- 本轮发布异常验证：相关定向回归 `124 passed`，主项目普通全量回归 `546 passed, 9 skipped`；PostgreSQL `17.10` 独立发布门禁 `9 passed`。本地 8000 后端已通过 `start-backend.bat` 启动，健康接口 `source_drift=false`；未执行真实远端写入或 Strategy Run 重放。
- 后端真实运行态复验补丁已完成：Strategy Run payload 时间统一严格解析；Run 规划覆盖严格收敛到启用站点发现快照；Shopify 查重使用 Blog+handle、cursor 分页和统一 `news` 默认值；创建异常返回四态远端事实；新增四处 URL 事务协调和站点级幂等修复接口。
- 正式接口重放结果：Exdivo Run `a3aaa0e6-ba1f-495c-93c3-a3569ada1212` 为 `awaiting_approval`（6/6），Avinoti Run `a060e8c5-40f8-4bdb-8895-895f34fdc8ec` 为 `awaiting_approval`（1/1），HealthyOxy Run `f5088787-d162-4a80-a4d4-0d0ae7bdbad5` 为 `blocked`（1/1）。三站相关 article/execution/publish/effect 共检查 45 条任务，不一致 0、内部域名 0；幂等复跑 changed=0。
- 本轮最终验证为普通全量 `556 passed, 13 skipped`、PG17 门禁 `13 passed`。HealthyOxy 真实只读 Shopify handle 查询返回既有 Article/Blog GID 和业务公开 URL；未批准 Action、未执行远端写入。数据库迁移：无。

2026-07-21 自定义商品数据连接器后端 V1 已完成：新增只读 HTTPS 请求模板、域名白名单、分页、响应校验、字段映射、secret 加密、版本验证/激活、运行记录和产品幂等同步；产品同步后同时保存 TDK、canonical、图片 ALT 等 SEO 审计结果。ExDivo 提供的真实 104 条响应样例已全部成功映射，发现 49 条缺少 meta title、49 条缺少 meta description、461 张图片缺少 ALT。数据库迁移已应用到本地 `pg-workbench`，尚未重启 8000 端口进程；正式保存 token 前还需在 `.env` 配置 `CONNECTOR_SECRET_KEY`。当前仅负责安全读取、标准化和识别缺口，AI 修正与向上游写回仍未实现。

自定义接口后续对接已保存为待办：`TXT/custom-connector-todo-2026-07-21/CUSTOM_CONNECTOR_TODO.md`。收到新站点接口资料后，从样例预览和真实只读测试继续，不需要重新设计后端连接器。

2026-07-21 Avinoti Bruno 接口已真实测试：商品列表接口可用，共 26 个商品；全部缺少 Meta Title、Meta Description 和 Meta Keywords，87 张图片缺 ALT。编辑失败根因已确认是把 variant ID 当成商品 ID、Bruno 实际发送空 Body，以及该接口要求完整商品结构。同值完整 PUT 已成功且未改变业务内容，但平台更新了商品时间并重建 4 个 variant ID，因此 Avinoti 写回暂列为高风险，需先确认 SKU ID 变化影响并补齐逐商品快照、人工批准和回读校验；当前尚未接入批量 AI SEO 写回。

2026-07-21 自建站 OEMApps 商品协议已抽成统一后端适配器：新增 `{site_id, token}` 预设配置入口，固定 `openapi.oemapps.com` 商品列表、分页、详情和完整 PUT 结构，不再要求逐站配置字段映射；新增 SEO 修改预览/执行/运行记录接口，执行必须确认快照与 variant 重建风险，并保存前后快照和写后校验。Avinoti 真实读取再次验证为 26/26 映射成功、0 错误且不回显 Token；本地尚无 Avinoti `site_id`，`CONNECTOR_SECRET_KEY` 也未配置，因此未保存或激活正式连接器。

2026-07-21 ExDivo OEMApps 连接器已正式落库并激活：站点 ID `24aa6361-9b34-4db8-8b22-c1324cac7c5c`，连接器 ID `ca557e1f-bd47-4590-b2ef-58cb366cee0f`；104 个商品已全部同步到 `seo_agent.products`，0 条拒绝。数据库审计为 49 个缺 Meta Title、49 个缺 Meta Description、461 张图片缺 ALT。连接器加密密钥已保存到 Windows 用户环境变量，Token 已加密入库且未出现在测试响应中；8000 端口进程仍需用户通过 `start-backend.bat` 重启以加载新代码和环境。

2026-07-21 ExDivo 产品分类接口已接入 OEMApps 适配器：`GET /collections/list`、`GET /collections/{id}` 和完整 `PUT /collections/{id}` 已真实验证。12 个专辑已同步到 `seo_agent.product_collections`，其中 10 个缺 Meta Title、10 个缺 Meta Description，共校验 100 条专辑商品关系。编辑接口使用只有 1 个成员的 `ALIBARBAR` 做同值 PUT，内容和成员保持不变，仅更新时间变化。由于读取接口不暴露成员 `is_top`，正式 SEO 写回要求预览快照、成员数量一致、显式确认 `is_top=0`、单专辑执行、审计和写后回读。

2026-07-22 OEMApps 首页 SEO 接口已接入：`GET/PUT /seoplans` 在 Avinoti 上完成真实同值测试，平台同时容忍响应外壳和纯数据，但适配器固定只提交 `meta_title`、`meta_descript`、`meta_keywords` 三个字段。ExDivo 首页 TDK 已只读同步到 `seo_agent.site_home_seo`，当前 Title、Description、Keywords 均完整。写回接口要求先预览、匹配快照、`confirm=true`、审计和写后回读。

## 0.1 历史跨窗口恢复点（2026-07-20）

以下内容是 2026-07-20 的恢复快照，不代表 2026-07-26 的运行状态。当时主线从“先做主站文章”收敛为“先建立主站商业页面归属，再生成支持文章”：

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

- 2026-07-26 当前有 13 个活跃站点，本地 `posts=157`，其中 `remote_missing=3`、缺 URL 为 0。
- 2026-07-26 只读核对为 `tasks=1,435`、`articles=33`、`keywords=1,885`、`serp_snapshots=66`、`posts=157`、`post_analyses=567`。这些是库存总量，不能直接等同于待执行策略或可发布内容。
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
- SERP 成功结果才允许进入缓存和策略证据；额度、认证、限流或供应商失败不会被解释成“0 条自然结果”。当前外部额度耗尽，恢复或更换密钥前实时抓取不可用。
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

```text
2026-07-30 AI On-page 统一 Action 后端全量回归：621 passed, 25 skipped
2026-07-30 PostgreSQL 17.10 隔离发布门禁：23 passed（含 OEMApps/Shopify product_seo 完整 Run-local→Plan→Action→Observation，以及能力变化、回读不一致、缺失 Adapter 的默认禁止场景）
2026-07-30 On-page/Action/Capability/底层 writer 组合回归：109 passed
2026-07-30 前端 TypeScript + Vite production build：通过；未修改前端源码
2026-07-30 Python compileall：通过；真实远端写入：0；生产数据库迁移：0
2026-07-28 当前主项目普通全量回归：536 passed, 9 skipped（PG17 集成项在无测试 DSN 时跳过）
2026-07-28 PostgreSQL 17.10 独立发布门禁：9 passed，缺少 PG17 测试连接时硬失败
2026-07-28 内容审计与 Strategy Run 针对性组合回归：36 passed
2026-07-28 Python compileall：通过
2026-07-28 git diff --check：通过，仅 Windows CRLF 提示
2026-07-28 本地后端：健康，source_drift=false
2026-07-28 远端写入：0；未运行真实策略、未批准 Action、未执行生产迁移
```

> 以下大部分为 2026-07-18 数据重置前的历史验证证据。当前策略候选、计划和执行数据已清空且 `articles=0`，不能把历史策略任务数当作现状。

```text
2026-07-26 当前最新非 knowledge 回归：Python `397 passed`；主前端 `11 passed`、社媒前端 `4 passed`，两个执行器分别 `2 passed` 与 `22 passed`，两个浏览器扩展各 `1 passed`；两个前端生产构建、Python compileall、git diff --check 通过。运行中主后端 OpenAPI 为 138 个路径、151 个操作，核心只读接口冒烟返回 200。
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

1. 先把 2026-07-28 的内容审计和 Strategy Run 修复整理成独立提交，禁止混入当前 GA4、发布服务、YouTube 执行器、研究脚本、文章和图片改动。
2. 对现有 HealthyOxy、Avinoti、Exdivo 三个 queued Run 做受控启动：只验证状态推进、事件、能力快照、证据、全部站点覆盖和 Action 预览；不得批准或执行远端写入。
3. 三站覆盖矩阵通过后，选择 1 条允许字段明确、风险低、可独立回读的 Action 做人工审批执行验收；任何能力变化、快照变化或回读差异都必须停止。
4. 完成全局身份认证和 business scope 授权；在此之前生产真实写 adapter 保持禁用。
5. 为内容审计增加独立 worker 或启动恢复机制，处理进程崩溃后遗留的 queued/running 批次。
6. 继续主站商业页面主线：实现 GSC/SERP 查询到产品页/分类页的意图归属，再生成绑定转化 URL 的支持文章候选。

不要先做：无审批自动执行、真实批量写入、真实回滚、打开主站普通 `strategy_enabled`、批量改写页面 SEO 字段、无复盘数据时的 AI 全权接管。

## 8. 新窗口检查命令

```powershell
cd C:\Users\PC\Desktop\seo2.0
git status --short
Get-Content -Raw PROJECT_PROGRESS.md
Get-Content -Raw docs\SEO_AUTONOMOUS_OPERATIONS_BACKEND_V2.md
Get-Content -Raw logs\daily-content-strategy-2026-07-27\anomaly-report.md
Get-Content -Raw TXT\strategy-close-loop-2026-07-17\strategy-closed-loop-2026-07-17.md
Get-Content -Raw docs\MAIN_SITE_ECOMMERCE_SEO_V1.md
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/health
docker exec pg-workbench psql -U seo -d seo_workbench -X -c "SELECT (SELECT count(*) FROM seo_agent.tasks) tasks, (SELECT count(*) FROM seo_agent.articles) articles;"
.\scripts\test-pg17-release-gate.ps1
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

### 2026-07-28 后端根因修复批次

- 修复 HealthyOxy `strategy_hold_refresh` 字段契约和 PostgreSQL aborted
  transaction 污染；GSC/GA4 改为逐来源独立事务。
- 建立 RemoteOutcome 唯一状态映射，贯通 Publish Task、Execution、Strategy
  Action、Strategy Run 和 P1 Exception；未解除不确定状态时禁止 Run retry。
- Shopify 查重升级为 blog GID + blog handle + article handle 三元身份校验。
- 新增真实 PG17 入口级门禁：三站四处 URL、一轮失败后 retry payload 恢复、
  Shopify timeout 五层落账、证据刷新事务隔离。
- 验证：`568 passed, 16 skipped`；PG17 `14 passed`。
- 真实 Run：Exdivo `ea2d267c-b6f7-4271-abbb-90bd2b9ec141` 与 Avinoti
  `2806325c-1e30-4757-900a-be98fafff9c3` 到达 `awaiting_approval`。
- HealthyOxy 的事务问题已修复，真实 GSC/GA4 刷新成功；当前被 Shopify
  connection 缺少产品只读权限及权威来源证据安全阻断，未绕过门禁。

### 2026-07-28 图片能力与效果任务状态一致性修复

- 图片能力改为以实际连接器实现为准：OEMApps 主站保留图片上传；
  普通 OpenAPI 博客不再因残留 `imageUploadPath` 而错误声明上传能力。
- Site Capability、Strategy Action generation context 与 Publisher capability
  已对齐；不支持上传的站点不再暴露 `images/image_alts` 或上传入口。
- 效果任务从 canceled 恢复及确认发布时，同时清除 payload、decision 中的
  过期 `canceled_reason`，历史审计事件继续保留。
- 新增幂等迁移 `034_clear_stale_strategy_effect_cancel_reason.sql`；本地
  `seo_workbench` 修复 4 条活动记录，活动状态残留计数为 0。
- 验证：相关模块 `121 passed`；全量 `589 passed, 17 skipped`；PG17 门禁
  `15 passed`；后端重启后 `source_drift=false`；无远端文章写入。
## 2026-07-31 AI 主导 SEO 正式链路最终验收

- 用户明确授权先使用现有 WordPress Application Password 和博客 Open API Key
  完成目标，凭据轮换延后到目标完成后执行；该风险记录为
  `risk_accepted_temporarily`，未伪装为已解决。
- 四平台真实远端只读文章回读重新通过：OEMApps `2626471`、WordPress `1869`、
  Shopify `gid://shopify/Article/573653745777`、Custom OpenAPI `22`。
- 全部验收调用均为本机 `GET /api/v1/sites/{site_id}/articles/lookup`；未执行远程
  PUT、POST、文章发布、媒体上传或数据库写入。
- 全量离线测试：`580 passed, 31 skipped`。
- PostgreSQL 17 发布门禁：`29 passed`。
- Skill 合同、日志自检、官方快速校验和本机操作边界均通过。
- 本机正式健康路径为 `/api/health`；`/health` 返回 404 不代表服务异常。
- 地区化实时 SERP、SEMrush GUI 和 Computer Use 采集继续按用户决定暂缓，不作为
  当前上线门禁。
