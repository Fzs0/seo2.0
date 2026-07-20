# 独立知识系统第一阶段交接

> 状态日期：2026-07-16（Asia/Shanghai）

## 已固化的基础

- 独立目录：`Y:\knowledge`，不依赖旧业务库迁移链。
- 当前迁移：不可变的 `001_init.sql`、`002_batch_ingestion.sql`、`003_claim_quality_gate.sql`、`004_market_signals.sql`、`005_signal_crawl_jobs.sql`；`004/005` 已于 2026-07-16 应用到当前手工数据库。
- `004` 应用前备份：`data/backups/knowledge_system-before-market-signals-20260716-120659.dump`（123,621 bytes，SHA-256：`E69E32A8852702E1B844A64020629005814C7CC3DA8F06A42DB8DAE2B384DA7E`）。
- `005` 应用前备份：`data/backups/knowledge_system-before-signal-crawler-20260716-144547.dump`（128,810 bytes，SHA-256：`D5004C2B7F60883AB70197A64270710A45C5E88A576D6AB49BCB6C03594EFBFD`）。
- SQL SHA-256：`BE908BD2A1A39EBE0134DBBBEF4D78C0F1D42D2D893682A7977F16906F6EEE8B`。
- 可复现 Compose：PostgreSQL 17、loopback `5434`、healthcheck、新独立 volume。
- 环境模板和 PowerShell 启动脚本：API `8010`，前端 `5174`。
- 文档：架构、后台/Agent 集成、数据库运维和共享盘并发规则。

## 当前数据库事实

本机已有 `knowledge-postgres`，2026-07-15 检查为 healthy。它是手工创建的容器，使用 `seo20-knowledge-db-data`，并把 Codex 临时目录中的原始 `001_init.sql` 只读挂载到初始化目录。它不受 `Y:\knowledge\docker-compose.yml` 管理。

不要直接 Compose 接管。新 Compose 使用 `knowledge-system-postgres-data`，且会与旧容器在名称/端口上冲突。安全切换必须先备份、停旧容器并改名，详见 `docs/operator-runbook.md`。

`003_claim_quality_gate` 已在 2026-07-15 应用到当前手工容器。用户随后明确要求清空知识业务数据；清空前备份为 `data/backups/knowledge_system-before-clear-20260715-192933.dump`（259,827 bytes，目录已忽略，不提交 Git）。随后 `004_market_signals` 和 `005_signal_crawl_jobs` 已于 2026-07-16 应用；当前 sources、documents、claims、evidence、usages、批量任务、质量审计、market_signals 和 signal crawl jobs/runs 均为 0，迁移记录为 `001/002/003/004/005`。

清空前的历史质量 dry-run 结果不再作为当前数据使用；备份中保留原始数据，且当时没有执行 AI apply。重新采集应从新的来源/文章开始，并重新观察 `claim-quality-v2` 的 keep、reject、uncertain 分布。

## 第一阶段 Interface

```text
GET  /api/v1/knowledge/health
GET  /api/v1/knowledge/overview
GET  /api/v1/knowledge/sources
GET  /api/v1/knowledge/documents
GET  /api/v1/knowledge/claims?review_status=pending
GET  /api/v1/knowledge/claims?review_status=rejected&quality_status=reject
POST /api/v1/knowledge/documents/import
POST /api/v1/knowledge/documents/import-url
POST /api/v1/knowledge/batch/preview
POST /api/v1/knowledge/batch/runs
GET  /api/v1/knowledge/batch/runs/{run_id}
GET  /api/v1/knowledge/batch/runs/{run_id}/items
POST /api/v1/knowledge/batch/runs/{run_id}/cancel
POST /api/v1/knowledge/quality/runs
GET  /api/v1/knowledge/quality/runs/latest
GET  /api/v1/knowledge/quality/runs/{run_id}
GET  /api/v1/knowledge/quality/runs/{run_id}/items
POST /api/v1/knowledge/quality/runs/{run_id}/apply
POST /api/v1/knowledge/quality/runs/{run_id}/cancel
POST /api/v1/knowledge/claims/{claim_id}/quality/restore
POST /api/v1/knowledge/claims/{claim_id}/review
POST /api/v1/knowledge/retrieve
POST /api/v1/knowledge/signals/import
GET  /api/v1/knowledge/signals
GET  /api/v1/knowledge/signals/overview
```

验收闭环：一个已确认授权的公开文章 URL 自动抓取正文并生成 0–4 条候选；手动正文作为兜底；独立质量模型筛除高置信明显噪声，其余进入人工审核；相同最终 URL 或正文重复导入不重复生成 Claim；pending/rejected 检索不到；approve 后能够检索；每条结果可追溯到 Source、Document、Evidence 和质量审计。

## 已确定且不得默默改变的决策

- 业务数据库和知识数据库使用两个连接、两个迁移链和两个 volume，不建跨库 FK/事务。
- 后续 SEO 后台和 Agent 通过 retrieve API 获取 KnowledgePack，不直连知识库。
- 单篇 URL 入口只抓一页；批量入口必须先预览，并通过通用 Sitemap/RSS/Atom Adapter、时间条件和最大文章数限制范围。所有入口都禁止访问本机/私网地址。
- 提取 AI 每篇允许返回 0 条，不为数量凑卡；上限为 4 条。
- AI 质量门禁只能安全拒绝，不能批准；弱证据、观点冲突和其他 uncertain 必须留给人工。只有 approved 可检索。
- 历史卡片必须先 dry-run，显式确认后才 apply；AI 拒绝可恢复，人工拒绝不能被 restore 覆盖。
- 外部正文是不可信数据，不能覆盖系统指令。
- 当前不引入 pgvector；先用精确过滤、全文索引和字面 fallback。
- 未认证 API 仅绑定 `127.0.0.1`；对外开放前先加认证和私网边界。
- 禁止删除当前或新知识库 volume，禁止用重建 volume 代替迁移。

## 下一步顺序

1. 对现有卡片完成质量 dry-run 抽样，确认理由、阈值和误拒绝率后再决定是否 apply。
2. 分别验证一篇“有可复用方法”和一篇“无知识可提取”的文章，确认前者不超过 4 张、后者可以 0 张。
3. 完成 pending 不可检索 → approve → 可检索，以及 AI reject → restore → pending 的真实 smoke test。
4. 确认 KnowledgePack 来源字段后，再把 SEO Brief 阶段接到 retrieve API。
5. 在扩大来源前补充跨文档语义重复/冲突发现、时效生命周期和质量抽样指标。

## 共享目录交接规则

另一台电脑只写 `knowledge` 外部目录时，直接文件冲突较少，但根 Git 状态、目录移动和全局清理仍会影响这里。若要共同开发 `knowledge`，先按文件/子目录分配所有权；迁移和容器接管始终单写者；不要两台电脑同时在共享目录执行依赖安装、构建、格式化、Git 写操作或删除。

发现陌生修改时只报告并停下，不覆盖。任何需要扩大范围、移动目录、接管容器或删除数据的动作都必须重新确认。

## 未完成/后续风险

- `004_market_signals` 已在当前手工数据库应用；后续迁移仍必须先备份，并由唯一迁移写者执行。
- 当前市场信号支持平台无关 JSON/手动导入、公开 HTML 关键词采集、去重、列表和概览；Reddit 已接入 `scrapi-reddit` + Crawlee 浏览器回退，YouTube 已接入 Crawlee + `youtube-comment-downloader`。X、TikTok、Instagram 等尚未接入，也尚未生成需求洞察和写作输入。

- 当前本地 API 没有认证，只适合 loopback 开发。
- Compose 配置已落盘但尚未接管当前手工容器，这是有意保留的安全状态。
- 开始真实采集前仍需确认每个来源的授权和保存边界。
- Usage 与 SEO 任务效果回流需要后续接口设计，不能用跨库事务拼接。
- 当前重复检测主要是同文档精确归一化；跨来源语义重复、观点冲突聚类和版本关系仍待实现。
- 时间敏感 Claim 尚缺少结构化 `as_of`、复审到期和失效策略；不能仅凭置信度长期保留。
- 质量门禁需要持续记录人工推翻率、理由分布和来源/主题抽样结果，用反馈校准阈值与提示词。
- 若未来跨机器调用 API，需要先设计私网/TLS/服务认证，不能直接暴露数据库或把服务绑定公网。
