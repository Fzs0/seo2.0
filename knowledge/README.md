# 独立知识系统

这是一个本地优先、与 SEO Workbench 业务库物理隔离的知识与市场信号系统。它既支持单篇 URL、手动正文和 Sitemap/RSS/Atom 批量文章，也支持以平台无关格式保存帖子、评论、回复、评价和视频评论等市场信号；文章进入 Claim 审核流水线，市场信号进入独立的信号层，为后续需求洞察、趋势分析和写作输入提供证据。单篇导入可在 AI 不可用时回退到确定性提取；批量任务则保留失败并重试，不会静默降级。

截至 2026-07-18，本机已创建独立知识 PostgreSQL；`knowledge.claims` 已从 `exports/knowledge_claims_2026-07-18` 导入 153 条 Claim（approved 107、pending 6、rejected 40）。当前仅保留这张 Claim-only 表，sources、documents、evidence、usages 及其他知识系统表已清空；主库误建的 `seo_agent.knowledge_claims` 已删除。

## 当前本地端口

| 服务 | 地址 | 约束 |
|---|---|---|
| PostgreSQL | `127.0.0.1:5434` | 仅本机 |
| 知识 API | `http://127.0.0.1:8010` | 未加认证，不得绑定公网 |
| 知识前端 | `http://127.0.0.1:5174` | 仅本机 |

## 当前数据库

当前 `knowledge-postgres` 由本目录 Compose 管理，绑定 `127.0.0.1:5434`，使用新 volume `knowledge-system-postgres-data`。启动前配置 `.env`，然后运行 `docker compose --env-file .env up -d postgres`。不要删除 volume，也不要绑定公网地址。

## 启动应用

要求：Python 3.11+、Node.js 20+，以及正在运行且 schema 已初始化的 PostgreSQL。

```powershell
Set-Location Y:\knowledge
Copy-Item .env.example .env
# 在本机编辑 .env，填写真实数据库密码和 AI Key；不要提交或粘贴到文档中。

python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Set-Location frontend
npm install
Set-Location ..
```

分别打开两个 PowerShell 窗口：

```powershell
Y:\knowledge\start-backend.ps1
Y:\knowledge\start-frontend.ps1
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/v1/knowledge/health
Start-Process http://127.0.0.1:5174
```

后端和前端脚本只读取本目录的 `knowledge\.env`，不会读取 `Y:\.env`。环境中已显式设置的变量优先于 `.env`。

### 配置 AI 提取

CloseAI 使用 OpenAI-compatible 接口。`.env` 中的相关配置如下（真实 Key 只填写在本机 `.env`）：

```dotenv
KNOWLEDGE_AI_BASE_URL=https://api.openai-proxy.org/v1
KNOWLEDGE_AI_API_KEY=你的本机密钥
KNOWLEDGE_AI_MODEL=gpt-5-mini
KNOWLEDGE_AI_TIMEOUT_SECONDS=90
KNOWLEDGE_AI_MAX_ATTEMPTS=2
```

`KNOWLEDGE_AI_API_KEY` 只由后端进程读取，禁止写入 `VITE_*` 变量、前端代码、日志、文档或 Git。Windows 记事本可能自动追加 `.txt`；请在资源管理器中开启“文件扩展名”并确认文件确实是 `Y:\knowledge\.env`，不是 `.env.txt`。修改配置后需要重启后端。

启动后先调用 health，再导入一篇短文：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/v1/knowledge/health | ConvertTo-Json -Depth 8
```

`/health` 只用于确认 API、数据库和 schema 正常。真正的提取方式看导入响应：`extraction_method` 为 `ai` 或 `deterministic_fallback`，`model` 是成功调用的模型名，`warning` 是无敏感信息的回退原因；重复导入时这三个字段为 `null`。若发生回退，先检查文件名、变量名、模型可用性和后端日志，不要把密钥打印出来。

## 第一阶段接口

```text
GET  /api/v1/knowledge/health
GET  /api/v1/knowledge/overview
GET  /api/v1/knowledge/sources
GET  /api/v1/knowledge/documents
GET  /api/v1/knowledge/claims?review_status=pending
GET  /api/v1/knowledge/claims?review_status=rejected&quality_status=reject
POST /api/v1/knowledge/signals/import
GET  /api/v1/knowledge/signals
GET  /api/v1/knowledge/signals/overview
POST /api/v1/knowledge/signals/crawl/preview
POST /api/v1/knowledge/signals/crawl/jobs
GET  /api/v1/knowledge/signals/crawl/jobs
POST /api/v1/knowledge/signals/crawl/jobs/{job_id}/run
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
```

后续 SEO 后台或 Agent 应通过 `POST /api/v1/knowledge/retrieve` 调用，不直接连接知识数据库。调用和安全约束见 [集成指南](docs/integration-guide.md)。

## 固定边界

- `DATABASE_URL` 属于业务数据库；`KNOWLEDGE_DATABASE_URL` 只属于知识数据库。二者不建 FK，也不伪装成跨库事务。
- 单篇 URL 导入只抓用户提交的一页；批量导入先只读预览，再按确认的时间范围与上限创建持久化任务。首批通用发现 Adapter 为 Sitemap 和 RSS/Atom，不包含任何厂商特判。
- 市场信号与长文档分开保存；信号统一支持帖子、评论、回复、评价等内容类型，平台标识放在信号自身的 `platform` 字段，平台差异留在 `metadata` 和未来 Adapter 内，不把 Reddit 或其他平台字段扩散到 Claim 模型。
- 所有网络访问都只允许公开 HTTP(S) 标准端口；本机、私网、保留地址、超限内容和过多重定向会被拒绝，重定向逐跳复验。
- 公开页面采集器只接受用户配置的搜索 URL 模板和帖子 URL 特征，按关键词串行、低速抓取，读取 robots.txt；不处理登录墙、验证码、私有内容或绕过反爬限制。
- 提取 AI 每篇允许返回 0–4 条候选；Evidence 必须能在原文中定位，校验失败的候选不会入库。
- 独立质量 AI 按整篇文档一次评审。只有低效用（不高于 0.25）、高评审置信度（不低于 0.90）且命中安全理由的明显噪声可被拒绝；冲突、弱证据和其他不确定内容保留给人工。AI 永远不能自动批准 Claim。
- 对历史卡片先执行 dry-run；dry-run 只写机器分类和追加式审计，不改变 `review_status`。显式 apply 后的 AI 拒绝可逐条恢复。
- 单篇导入的 AI 调用失败时可回退到确定性提取并将质量错误留给人工；批量导入要求提取和质量预审都成功，否则重试并显式标记失败。
- 只有 `approved` Claim 可以进入检索；`pending` 和 `rejected` 永不注入 Agent。
- 外部正文是不可信数据，只能进入明确的数据块，不能覆盖系统指令。
- 当前不引入 pgvector；先使用结构化过滤、PostgreSQL 全文检索和字面匹配。
- 不执行 `docker compose down -v`、`docker rm -v` 或删除任何知识库 volume。

## 共享 `Y:` 盘

另一台电脑只修改 `knowledge` 之外的目录时，文件内容通常不会直接冲突，但仍共享根目录元数据和 Git 状态。不要在两台电脑同时执行根仓库的提交、切分支、批量格式化或清理。若两台电脑都要改 `knowledge`，必须先分配文件所有权；迁移只能由一个人/Agent 写和执行。更完整的并发规则见 [架构说明](docs/architecture.md#共享-y-盘并发规则)。

## 文档

- [架构说明](docs/architecture.md)
- [后台与 Agent 集成指南](docs/integration-guide.md)
- [当前对接就绪度与实施说明](docs/integration-readiness.md)
- [2026 第 29 周周报](WEEKLY_REPORT_2026-W29.md)
- [市场信号与海外品牌洞察](docs/market-signals.md)
- [数据库运维手册](docs/operator-runbook.md)
- [阶段交接](docs/handoff.md)

迁移位于 `db/migrations/`。`004_market_signals` 和 `005_signal_crawl_jobs` 已于 2026-07-16 应用到当前手工数据库；`005` 应用前备份为 `data/backups/knowledge_system-before-signal-crawler-20260716-144547.dump`（128,810 bytes，SHA-256：`D5004C2B7F60883AB70197A64270710A45C5E88A576D6AB49BCB6C03594EFBFD`）。`001_init.sql` 是已验证来源文件的逐字节副本，SHA-256 为 `BE908BD2A1A39EBE0134DBBBEF4D78C0F1D42D2D893682A7977F16906F6EEE8B`，不得改写。
