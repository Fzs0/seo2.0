# AI 主导 SEO 正式链路验收

- 验收日期：2026-07-31（Asia/Shanghai）
- 目标文档：`docs/AI_LED_SEO_STRATEGY_GOAL_2026-07-31.md`
- 验收范围：后端正式 Interface、持久化、调度、统一 Action、观察、旧链路退役、
  Skill 合同和本机运行态
- 明确暂缓：地区化实时 SERP、Computer Use 和 SEMrush GUI 真实采集。恢复前必须
  明确目标地区、语言、设备和采集网络；本轮不混用不同地区结果
- 远程变更：0；本轮真实连接器验收全部为 GET/只读回读

## 1. 最终正式链路

```text
Scope Discovery / Evidence Snapshot
→ AI Research Portfolio
→ AI Proposed Actions
→ Backend Safety Review
→ Zero Action Review
→ Formal Plan
→ Unified Action
→ Preview / Approval / Execute
→ Independent Readback
→ Observation
→ AI Strategy Learning
```

正式入口：

- `POST /api/v1/strategy-runs/{run_id}/research-portfolio`
- `POST /api/v1/strategy-runs/{run_id}/proposed-actions`
- `POST /api/v1/strategy-runs/{run_id}/zero-action-review`

旧 `POST /api/v1/strategy-runs/{run_id}/run-local-options` 已从 OpenAPI 删除，运行态
不存在该路径。候选池只保留历史只读查询。

## 2. 完成定义逐项审计

| # | 目标要求 | 结果 | 直接证据 |
|---:|---|---|---|
| 1 | 无候选池完成研究和正式策略 | 通过 | Research/Proposal Interface 与 keywordless PG17 回归 |
| 2 | AI 唯一编辑决策者 | 通过 | Orchestrator 保留 proposal sequence，不评分、不替换主题 |
| 3 | 冲突精确到 URL/对象/意图 | 通过 | scope key、Formal Strategy/Action/Observation 精确锁测试 |
| 4 | 零动作必须复审 | 通过 | Zero Action Review PG17 补证恢复测试 |
| 5 | 研究不足返回补证 | 通过 | `research_revision_required` 与稳定原因码 |
| 6 | Deferred 完整保存 | 通过 | PG17 10 个合格动作、3 执行、7 Deferred |
| 7 | Configuration Repair 保型 | 通过 | Interface 与 PG17 持久化回归 |
| 8 | 全活动流量站点有结果 | 通过 | 精确 site_id 集合覆盖；disabled 活动站形成修复 |
| 9 | 候选池退出新策略/Action | 通过 | 新 Interface 拒绝 `candidate_id`；旧生成服务和路由删除 |
| 10 | 关键词库可选且无决策权 | 通过 | 核心 Interface 拒绝 `keyword_id`，只允许 evidence reference |
| 11 | 未来来源通过 Evidence Adapter | 通过 | `EvidenceAdapter` Protocol 与固定快照 Adapter |
| 12 | GUI 证据来源合同 | 条件通过 | Schema 已覆盖来源/市场/语言/设备/窗口/工件；真实 GUI 按产品决定暂缓 |
| 13 | 多来源冲突解释或补证 | 通过 | unresolved conflict 自动 Deferred |
| 14 | 新业务/平台不改决策主流程 | 通过 | 新站覆盖测试；平台差异仅在 Adapter/Capability |
| 15 | Unified Action 完整闭环 | 通过 | 文章与 On-page PG17 lineage/readback/observation |
| 16 | PG17、全量、并发、真实只读 | 通过 | 29 项 PG17；580 项离线；四平台真实 GET |
| 17 | 旧临时决策旁路删除 | 通过 | run-local route、旧 strategy decision service 均删除 |
| 18 | 文档、异常、效果数据一致 | 通过 | API、V2、Goal、Summary、Progress 和 Skill 已同步 |

## 3. 34 个验收场景覆盖

### AI 自主性（1–4）

- 无 candidate/keyword ID 的正式文章策略通过。
- 后端不按 volume、KD、score 或固定权重重选主题。
- 同站可表达文章、On-page、Hold 和 Configuration Repair。
- Proposed Action 中出现 `candidate_id` 或核心 `keyword_id` 会被稳定拒绝。

### 冷却和冲突（5–9）

- 锁身份按规范 URL、远端对象或站内意图构造。
- 同 URL/意图在途时 Deferred；同站不同 URL/意图可继续。
- Formal Strategy、Unified Action 和 Observation 同时参与精确锁。
- 越界站点返回 `SITE_OUT_OF_SCOPE`，不静默删除。
- 低置信度或未解释的证据冲突进入 Deferred/补证，而不是整站 Hold。

### 研究质量（10–18）

- 每站 Research Portfolio 必须覆盖五类动作评估。
- 非硬阻塞全量 Hold 要求第二证据通道和站点级证据。
- 采集失败、empty、partial 和 limitations 均可保留。
- 通用 Evidence Adapter 支持 API、数据库、固定快照和未来 GUI 来源。
- 地区化真实 SERP/SEMrush GUI 真实采集按用户决定暂缓；当前禁止混用地区。

### 调度和持久化（19–24）

- 10 个合格动作、容量 3 的 PG17 结果为 3 Execute Now、7 Deferred、0 丢失。
- 每站当前波次最多一个远程写；调用方更高 quota 会被夹紧为 1。
- Hold、Deferred、Configuration Repair 不创建远程 Action。
- 每个 Execute Now Strategy 最多一个 Action。
- 相同幂等键同输入回放；不同输入返回稳定冲突。
- 连续两次实质相同的全量 Hold 创建 `STRATEGY_STAGNATION` 并要求新证据。

### 扩站（25–28）

- 覆盖以精确 site_id 集合校验，不以数量近似。
- 新站只增加 inventory/capability/adapter，不增加 AI 决策分支。
- OEMApps、Shopify、WordPress、Custom OpenAPI 均完成真实远端只读文章回读。
- 一个站点能力缺失只形成该站修复/阻塞，不阻止其他站点。

### 执行和观察（29–34）

- 正式 Plan/Strategy/Action/能力预检先于生成和媒体上传。
- 独立回读覆盖正文、SEO、媒体、日期、公开 URL 所需字段合同。
- readback mismatch 创建 P1 且不创建正向 Observation。
- 远端不确定禁止自动重试；先走只读恢复。
- Observation 检查点为 0/7/14/28/56/90 天。
- Skill 要求下一轮读取 Effect/Observation 并写入新的 Research Portfolio。

## 4. 自动化验证结果

```text
主项目离线全量：
580 passed, 31 skipped

PostgreSQL 17.10 发布门禁：
29 passed

Publisher/运行时凭据组合回归：
68 passed

Skill：
validate_contract.py                 通过
validate_run_logs.py --self-test     通过
quick_validate.py                    通过
local operator boundary              ok=true
```

PG17 门禁覆盖：

- Run/Research/Proposal/Plan/Strategy/Action/Observation 血缘；
- 同目标并发；
- 同幂等键并发；
- Deferred 完整保存；
- Configuration Repair 保型；
- 零动作补证恢复和策略停滞；
- Action 租约、超时、旧 Token、事务回滚；
- 回读不一致与远端状态不确定。

## 5. 本机运行态

```text
health_endpoint: /api/health
health: true
env: local
listener: 127.0.0.1:8000
source_drift: false
OpenAPI paths: 158
research_portfolio: true
proposed_actions: true
zero_action_review: true
retired_run_local: false
```

说明：本应用的健康检查正式路径是 `/api/health`，不是 `/health`。使用后者会稳定
返回 404，但不代表后端服务异常。

真实远端只读回读：

| 平台 | 结果 |
|---|---|
| OEMApps | 找到已发布远端文章 |
| WordPress | 找到已发布远端文章 |
| Shopify | 找到远端文章及稳定 GID |
| Custom OpenAPI | 找到已发布远端文章 |

## 6. 本轮发现并修复的问题

1. WordPress 数据库站点记录缺少运行时凭据，导致只读 lookup 500。现在从既有本机
   WordPress 配置只在内存中装配，不回写数据库或公开响应。
2. Custom OpenAPI 博客存在同样的运行时装配缺口，已使用相同内存合并方式修复。
3. 通用 `custom_openapi` 标签一度先于 OEMApps 特征匹配，导致 OEMApps Token 未加载。
   已调整 Adapter 优先级并增加回归。
4. 旧 `strategy_decision_service` 无生产调用但仍保留固定分数 candidate 逻辑，已删除。

## 7. 安全异常与外部行动

排查配置时，一次本地搜索命令将配置文件中的凭据值带入工具输出。未执行远程写入，
未把凭据写入代码、测试、数据库或本文，但应把本次输出视为凭据暴露风险。

- 严重级别：P1
- 状态：risk_accepted_temporarily（凭据尚未轮换）
- 影响范围：现有 WordPress Application Password 和博客 Open API Key
- 用户决定：允许先使用现有凭据完成正式链路验收，目标完成后再轮换
- 后续行动：站点侧轮换相关凭据，并同步更新本机配置
- 轮换后验证：确认旧凭据已撤销；使用新凭据重新执行四平台只读回读

在轮换完成前，不建议把相关本机配置或任务输出分享给不受信任的人员。用户已明确
接受该临时风险，因此轮换不再阻塞本目标的完成，但风险没有被技术性消除。

用户授权后使用现有凭据重新执行真实远端只读回读，结果如下：

| 平台 | 验收对象 | 结果 |
|---|---|---|
| OEMApps | Avinoti 文章 `2626471` | 找到，`published` |
| WordPress | vapes2000 文章 `1869` | 找到，`publish` |
| Shopify | HealthyOxy 文章 `gid://shopify/Article/573653745777` | 找到，公开 URL 正确 |
| Custom OpenAPI | vapes1999 文章 `22` | 找到，`published` |

以上调用均通过本机 `GET /api/v1/sites/{site_id}/articles/lookup` 发起；没有执行远程
PUT、POST、发布、媒体上传或数据库写入。

## 8. 结论

AI 主导正式链路的代码、接口、持久化、并发、安全调度、Zero Action Review、
Unified Action、观察、旧链路退役、Skill 与四平台只读运行态均已达到目标文档要求。
前端继续只作为看板。地区化实时搜索采集按用户决定暂缓；恢复时只新增 Evidence
Adapter，不改正式决策主流程。

当前唯一外部安全收尾项是轮换本轮诊断输出涉及的凭据。轮换完成后再执行四平台
只读回读，即可关闭该 P1。用户已决定将其放在本目标完成之后处理，并接受轮换前的
临时风险；该事项保持可见，但不再阻塞本目标完成。
