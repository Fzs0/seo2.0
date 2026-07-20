# 项目进度：品牌发现力知识系统

> 更新日期：2026-07-18（Asia/Shanghai）  
> 状态：Docker 知识 PostgreSQL 已创建，处于空库初始化与本机 Beta 对接阶段

## 一句话定位

这是一个独立于 SEO Workbench 业务库的“品牌发现力知识服务”：负责采集、整理、审核和维护可追溯知识；其他系统只消费已批准的 Claim，不需要接触导入、审核和数据库内部细节。

## 当前运行状态

| 项目 | 当前值 |
|---|---:|
| API 健康状态 | 尚未启动/待检查 |
| 来源数 | 0 |
| 当前文档数 | 0 |
| 已批准 Claim | 0 |
| 待审核 Claim | 0 |
| 已拒绝 Claim | 0 |
| KnowledgePack 使用审计 | 0 次 |

### 当前来源

| 来源 | 渠道 | 文档 | Claim |
|---|---|---:|---:|
| 暂无 | — | 0 | 0 |

> 本机新库尚未导入另一台电脑的数据；历史数据快照保留在交接文档中，不作为当前运行事实。

## 已完成能力

### 知识流水线

- 独立 PostgreSQL schema、独立迁移链与独立连接串；不与业务数据库建立外键或跨库事务。
- 支持单篇 URL、手动正文、Sitemap / RSS / Atom 批量发现与持久化 Worker。
- 文档按内容哈希去重；同一 URL 的内容变化会创建 revision，默认只使用最新版本。
- Claim 具备结论、适用条件、例外、建议动作、置信度、来源和 Evidence 定位。
- 只有 `approved` Claim 能进入 `POST /api/v1/knowledge/retrieve`；pending 和 rejected 不会被 Agent 消费。
- 已批准 Claim 支持人工拒绝，能纠正误批准知识。
- 检索不再按 market 硬过滤；market 仅保留为文档适用范围元数据。

### 质量与安全

- AI 提取最多生成 0–4 条候选，Evidence 必须能在原文中定位。
- AI 质量门禁只允许自动拒绝明显噪声，永远不能自动批准 Claim。
- dry-run / apply / restore 均保留质量审计；人工拒绝不能被 AI restore 覆盖。
- URL 抓取具备 SSRF、重定向、超时、内容大小与公开 HTTP(S) 边界保护。
- 外部内容被视为不可信数据，知识注入 Agent 时必须以数据块而不是指令处理。

### 市场信号层

- 原始帖子、评论、回复、评价和视频评论存入独立 `market_signals`，不自动混入知识 Claim。
- 支持手动/JSON 导入、列表、筛选、概览和定时公开页面采集任务。
- Reddit 采用 `scrapi-reddit`，受阻时浏览器回退；YouTube 支持公开搜索与评论采集。
- 当前界面已将其定位为“需求信号”，用于研究用户语言、痛点和趋势，而非直接生成策略知识。

### 前端与体验

- 工作区语义已统一为：发现力知识、需求信号、知识校准、策略检索。
- 已完成来源、文档、待审/已批准/已拒绝知识的管理和检索页面。
- 信号库筛选栏已改为按实际容器宽度自动换列，避免窄布局横向溢出。
- 已移除知识检索中的市场筛选项，检索用于查看所有可用知识。

## 已完成的知识积累

### Ahrefs

- 已完成 20 个页面的知识采集，累计 69 条 Claim。
- 已明确排除依赖某个工具筛选语法、缺乏可迁移性的技巧型结论。

### Semrush

- SEO 通用主题：从 `seo-tips` 入口完成 22 个站内页面的人工知识采集。
- 品牌发现力与 AI 搜索主题：从 `brand-positioning-is-an-ai-search-variable` 入口完成 20 个站内页面的人工知识采集。
- Semrush 目前累计 43 个页面、56 条 Claim。
- 两轮 Semrush 人工采集均未调用用户的 AI API；新增记录 metadata 标记为手工整理与人工审核。

## 当前接口边界

### 对其他系统提供知识

推荐接口：

```text
POST http://127.0.0.1:8010/api/v1/knowledge/retrieve
```

调用方传入简短的任务关键词、`channel`、`language_code` 和 `limit`，获得只含 approved Claim 的 KnowledgePack。调用方应保存完整快照、Claim ID、来源 URL 和 Evidence。

如果未来选择“数据库直连消费”，不要授予其他电脑 `claims` 原表权限。应创建一个只读视图，例如 `knowledge.approved_claims_export`，固定过滤 `review_status = 'approved'`，再授予专用账号该视图的 `SELECT` 权限。

### 当前网络边界

- PostgreSQL 当前监听 `127.0.0.1:5434`，只允许本机。
- 知识 API 当前监听 `127.0.0.1:8010`，未认证，只允许本机开发使用。
- 同 Wi-Fi 的其他电脑不能直接访问上述服务，这是当前配置的预期安全行为。
- 若需要局域网数据库消费，必须单独配置：指定 LAN IP 的 Docker 端口映射、只允许目标主机 IP 的 Windows 防火墙规则、只读数据库角色与 approved 视图。当前未执行这些变更。

## 已知限制与优先级

### P0：检索召回需要先改进

当前检索使用 PostgreSQL 全文检索和字面匹配，主要索引 Claim 的 `topic / statement / recommended_action`。它不检索 Document 标题、Evidence 和正文，也没有同义词扩展或向量检索。

结果是多词查询会趋向“所有词同时命中”，自然语言问题可能漏掉相关知识。下一步应先：

1. 把 Document 标题与 Evidence 纳入检索候选和排序；
2. 支持更合理的查询拆分或任务侧改写；
3. 建立至少 20 条真实任务的 Top-5 召回基准；
4. 基于评估结果再决定是否引入向量检索。

### P1：形成正式的知识导出契约

如果业务系统希望保存知识快照而非实时调用 API，应新增 approved Claim 的增量导出接口或数据库只读视图，并支持：

- `claim_id`、`updated_at` 与审核状态；
- 已批准知识后被拒绝时的失效同步；
- Claim、条件、例外、建议动作、来源 URL 和 Evidence 的完整导出；
- 按更新时间的增量拉取与幂等导入。

### P1：市场信号到洞察包

市场信号已能采集和保存，但尚未有“多条原始信号 → 带证据的需求洞察 → 人工确认 → Brief 输入”的中间层。该能力应在扩大平台来源前完成。

### P2：远程部署

跨机器或公网调用前，需要私网/受限反向代理、TLS、服务身份认证、导入和审核权限、审计日志、速率限制。不能仅通过把服务改为 `0.0.0.0` 完成部署。

## 下一步建议

1. 优先修复检索召回，再让 SEO Workbench 的 Brief 阶段消费 KnowledgePack。
2. 建立 20 条真实任务的检索和人工可用性基准。
3. 设计 approved Claim 的增量导出或只读视图，明确业务侧是实时读取还是定时同步。
4. 设计市场信号的洞察证据包与人工确认流程。
5. 只有出现明确跨机器需求时，再实施受限 LAN 或私网部署。

## 相关文档

- [架构说明](docs/architecture.md)
- [后台与 Agent 集成指南](docs/integration-guide.md)
- [对接就绪度与实施说明](docs/integration-readiness.md)
- [市场信号说明](docs/market-signals.md)
- [运维手册](docs/operator-runbook.md)
- [阶段交接](docs/handoff.md)
