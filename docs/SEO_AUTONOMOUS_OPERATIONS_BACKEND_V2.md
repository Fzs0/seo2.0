# SEO 自主运营后端 V2

本版本定位为“人工审批执行版”，不是无审批生产自动化。全局身份认证和
business scope 授权完成前，生产真实写适配器必须保持禁用。

## 安全边界

- 仅支持 PostgreSQL 17；应用启动和 CI 发布门禁都会检查主版本。
- 默认 Action adapter 返回稳定阻塞结果，不执行远端写入。
- 文章和基础 SEO 字段只有在能力快照声明可写、预览哈希和补丁哈希均获审批时
  才能交给显式注入的 adapter。
- URL、slug、canonical、redirect、删除、价格、库存、Variant、分类成员关系
  及未声明字段始终禁止。
- 回滚和无审批自动执行仍返回明确的禁止原因。

## Strategy Run

Run 使用现有 `seo_agent.tasks.payload` 持久化，不增加数据库结构。支持创建、
开始、查询、事件、取消、重试和人工收口。阶段输出、事件、站点清单、能力快照和
覆盖矩阵均持久化，重启后从未完成阶段继续，已完成阶段不会重复执行。

Run 在 `queued` 状态支持
`POST /strategy-runs/{run_id}/run-local-options`。Codex 可先读取站点、产品、
内容、GSC、GA4、SERP 和公开资料，自主决定新写、更新、On-page、Hold 或配置修复，
再提交本轮选题。提交内容要求当前证据、用户意图和决策理由；更新文章还必须携带
现有目标身份。后端校验 business/site/目标引用和幂等键后，将选题保存在当前 Run，
不新增表或字段。

```text
queued
→ discovering_sites
→ checking_capabilities
→ gathering_evidence
→ planning
→ refreshing_evidence → replanning
→ awaiting_approval
→ executing
→ verifying
→ observing
→ completed | partial | blocked | failed | canceled
```

`all_sites` 枚举业务下全部启用站点；`selected_sites` 也把所有请求站点作为
一个批次交给规划器，禁止按站点重复生成计划并互相覆盖。完成前同时校验站点数量
和 site_id 集合；任一发现站点缺少
`execute | awaiting_approval | hold | configuration_repair | failed` 决策时，
Run 不得完成。

## Strategy Plan V2

每次 Run 只生成一个带 `strategy_run_id` 的正式计划。计划保存全部合格选项，
并将其明确分类为：

- `execute_now`：本批创建 Action；
- `deferred`：保留为后续波次，记录延后原因和重新评估条件；
- `hold`：证据、风险或配置门禁未通过，不创建 Strategy/Action。

`action_budget` 字段为兼容旧 API 而保留，实际语义是防止异常批量写入的
`safety_action_ceiling`。超过安全上限的合格选项只能转为 `deferred`，不得丢弃，
也不得改变其研究结论。站点级上限遵循相同规则。

关键词和候选记录只作为研究来源：正式 Strategy 的 `candidate_id`、`keyword_id`
均可为空。候选记录本身不能创建 Action。Action 必须同时精确引用当前
`Run → Plan → execute_now Strategy`，且 business、site、action type 全部一致。
当 Run 已提交 run-local options 时，这批当前研究选题是该 Run 的唯一正式规划
输入；旧审计候选、关键词库和持久候选不能参与排名、替换提交选题或自动获得
`execute_now`。提交的 `execute_now/deferred/hold/configuration_repair` 会保留，
但风险、站点配置、能力快照和安全上限仍可将危险动作降级，不会被绕过。
重新规划只替换同一 `strategy_run_id` 的旧计划，不影响同业务的其他 Run，因此
业务和站点数量增长不会造成计划互相覆盖。

## Site Capabilities

能力阶段调用本地 Site Capabilities 服务并保存完整快照。快照包含读写/审批政策、
允许字段、已知副作用、连接器健康、数据新鲜度、配置问题、规范域名，以及忽略
刷新时间后计算的 `capability_snapshot_hash`。

未声明能力、异常连接器和未允许字段默认禁止。能力政策变化会改变哈希，使旧审批
失效并要求重新 preview。

## Action 闭环

```text
planned → previewed → approved → executing → verifying
        → completed | blocked | failed
```

审批绑定 `snapshot_hash + patch_hash + capability_snapshot_hash`。执行保存 Token、
领取时间、租约到期时间、尝试次数和心跳。平台比较
`approved_patch / submitted_patch / remote readback`，对 HTML、空值和声明的只读
字段做规范化，并输出逐字段 `expected / submitted / actual / match`。

关键字段不一致时返回 `readback_mismatch`，创建 P1 异常，Run 只能进入 partial
或 failed，并且不创建正向观察。一致时，平台按 action 幂等创建第 7、14、28、
56 天观察计划。

租约超时后 `/recover` 先做远端只读分类：

- `confirmed_not_applied`：允许新 Token 重试；
- `confirmed_applied`：补写本地完成状态；
- `partially_applied`：阻塞并创建异常；
- `unknown`：禁止自动重试。

旧 Token 或过期 Token 不能提交结果。

正式 Action 的 `/execute` 和 `/recover` 在 Action 到达
`completed | blocked | failed | canceled` 后，会通过同一生命周期协调接口自动
推动父级 Run。只要当前 Run 的全部 Action 已终止，Run 会在同一次请求中依次完成
`executing → verifying → observing → completed | partial | failed`，不再要求客户端
额外调用第二次 `/start`。多个 Action 并发完成时使用数据库比较更新并从最新状态
重试，不会因一次状态竞争把已成功 Action 留在未收口 Run 中。

`POST /strategy-runs/{run_id}/reconcile` 是带 `Idempotency-Key` 的人工恢复入口，
只读取已持久化 Action 结果并推进 Run，不执行远端写入。它不是正常流程的必要步骤。

文章动作在生成正文或上传图片前还必须通过统一 Preflight。Preflight 绑定正式
计划、Strategy 和能力快照，签发 30 分钟 Token；生成上下文和媒体上传都会再次
检查 Token、正式血缘和当前能力哈希。图片上传使用独立幂等回执，网络超时会记录
为 `unknown_remote_state` 并禁止盲目重传。

## API 契约

Run、Action 和 Capabilities 成功及失败统一返回：

```json
{
  "ok": true,
  "request_id": "request-id",
  "data": {},
  "error": null
}
```

写接口要求 `Idempotency-Key`（Run 创建和重试在请求体中携带）。校验错误也使用
稳定错误码，不依赖错误文本判断 HTTP 状态。

## 唯一执行链与旧链退役

外站 SEO 写入只允许走：

```text
Strategy Run → current research → run-local options
→ Formal Plan → execute_now Strategy
→ Strategy Action preview → approve → execute → readback
→ observation → automatic Run reconciliation
```

旧候选池手工选中、旧计划 PUT、旧 Strategy review/execute/cancel/stop、
旧 on-page 写路由、旧 automation run-once/clear-queue/settings 均已移除。
应用启动时也不再创建旧自动领取 worker。候选、关键词、历史 Strategy 和效果记录
只保留只读查询价值，不能直接创建或执行 Action。

文章 Action 会直接创建带 `strategy_action_id` 的内部执行台账，不再调用旧审核函数，
也不会产生可被其他 worker 竞争领取的 queued 执行任务。`dry_run` Run 的 Action
不能执行远端写入，也不能用 `dry_run=false` 上传媒体。

## PostgreSQL 17 发布门禁

仅可指向数据库名含 `test`、`temp` 或 `tmp` 的一次性数据库：

```powershell
$env:SEO_PG17_TEST_DSN = "postgresql://seo:password@127.0.0.1:5432/seo_v2_test"
.\scripts\test-pg17-release-gate.ps1
```

该门禁执行全量 migration（含 033/034）、031/032 重复执行验证、Hold 多连接
并发、Token 超时接管、最新审计批次与正式计划血缘门禁、相同幂等键并发创建，
以及 OEMApps/Shopify On-page 无网络写入的
`Run → Plan → Action → approve → execute → readback → Observation → completed`
正式链路。缺少连接时命令硬失败。

## On-page 统一 Action

`on_page_fix` 仅表示 AI 的编辑决策。Run-local 提交后，Formal Plan 将其转换为
`homepage_seo`、`product_seo`、`category_seo` 或 `product_image_alt`，并保存
本地资产、远端对象、公开 URL、页面类型、连接器和预计字段。提交阶段会使用现有
数据库回查同一 `business_id + site_id` 下的真实对象，不接受跨站或冲突身份。

统一 Adapter Router 只做精确路由，不根据域名猜测，也不提供平台兜底：

| 平台 | 首页 | 产品 | 分类 |
| --- | --- | --- | --- |
| OEMApps | `homepage_seo` | `product_seo` / `product_image_alt` | `category_seo` |
| Shopify | Hold | `product_seo` | Hold |
| WordPress | 不创建页面 Action；文章 TDK 继续 `update_article` | 同左 | 同左 |
| Custom OpenAPI | 仅在未来明确声明字段、写接口和独立回读后注册；当前 Hold | 同左 | 同左 |

能力快照包含具体 Action 的字段白名单、副作用、读/写/独立回读标志及 Adapter
标识和版本，并进入 snapshot hash。审批同时绑定能力、before、patch、目标和
Adapter 身份。OEMApps 产品/图片 ALT、分类和首页分别要求对应副作用确认。
执行只发送一次远端写入，随后独立回读；不一致创建一个 P1 且不创建正向观察。
确认未写入的恢复也会清除旧审批，必须重新 Preview。

成功动作建立页面级 T+0、7、14、28、56、90 天观察计划，保存页面类型、
目标 URL、前后哈希、GSC 页面 baseline、GA4 landing-page baseline 和下一检查时间。

## 当前限制

- `/start` 是同步、可恢复的显式执行入口，尚未提供独立常驻队列 worker。
- Custom OpenAPI 当前仍只有只读产品契约，因此未注册 On-page 写 Adapter。
- Shopify 第一阶段只开放产品 SEO；首页和分类准确 Hold。
- WordPress 的页面元数据继续作为文章 `update_article`，不声明商品、分类或首页能力。
- 全局认证和 business scope 授权仍是开放生产门禁。
- 恢复分类的远端只读调用当前仍可能占用动作事务锁；这是安全但保守的实现。
- 真实回滚和无审批自动执行继续阻塞。
