# 知识系统项目进度与新项目交接

> 状态日期：2026-07-14（Asia/Shanghai）  
> 目的：原项目所在电脑已经搬走，`Y:` / `\\1A23456\seo2.0` 永久离线。本文用于在一个全新项目中继续完成知识系统。

## 1. 一句话状态

知识系统的产品方向、页面原型和独立 PostgreSQL 知识库已经建立并验证；原项目的后端接口与 React 页面尚未落盘。下一步应在新项目中复用当前数据库或迁移数据库，然后实现“资料导入 → 知识候选 → 人工审核 → 检索 → Agent 使用”的完整闭环。

## 2. 项目目标

建立一个可持续积累、审核和调用的 Agent 知识系统。第一批知识可以来自 Ahrefs SEO 博客，后续扩展到：

- SEO 文章与官方指南；
- 海外社媒爆款文案；
- Reddit、Quora 等论坛案例；
- 爆款视频脚本、Hook、节奏和画面模式；
- 轮播图、信息图和图文内容方法；
- 自有 GSC、GA4、发布结果和业务案例。

系统不应只是保存原文。目标数据流是：

```text
来源/文章
  → 文档清洗与去重
  → 提炼为结构化知识候选 Claim
  → 人工批准或拒绝
  → 按渠道/市场/语言/任务检索
  → 形成带来源的 KnowledgePack
  → 注入文章、社媒、论坛、视频和图文 Agent
  → 记录使用结果并形成效果闭环
```

## 3. 已完成事项

### 3.1 页面产品原型

已生成可交互 HTML 原型，包含：

- 知识系统总览 KPI；
- 资料来源管理；
- Ahrefs 等文章手动添加表单；
- SEO、Social、Forum、Video、Visual 渠道分类；
- 知识卡审核队列；
- Playbook 展示；
- 检索实验室与 KnowledgePack 预览；
- Agent 大脑状态、知识缺口和处理管线。

原型文件：

- `knowledge-system-workbench.html`：Codex 内嵌可视化 fragment；
- `knowledge-system-prototype-standalone.html`：可单独打开的完整 HTML。

注意：原型是前端交互演示，尚未连接数据库或真实接口。

### 3.2 独立知识库数据库

已真实创建并运行：

| 项目 | 当前值 |
|---|---|
| Docker 容器 | `knowledge-postgres` |
| 镜像 | `postgres:17-alpine`，当前服务端版本 17.10 |
| 状态 | healthy |
| Host | `127.0.0.1` |
| Port | `5434` |
| Database | `knowledge_system` |
| Username | `knowledge` |
| Password | 不写入可提交文档；直接复用前应从本机安全配置取得，并在迁移首日轮换 |
| Schema | `knowledge` |
| Volume | `seo20-knowledge-db-data` |
| Restart policy | `unless-stopped` |
| 端口暴露 | 只绑定本机 `127.0.0.1:5434` |
| 当前部署方式 | 直接创建的 Docker 容器，不受 Compose 管理 |
| 当前网络 | Docker 默认 `bridge` |

异步 Python 连接串：

```text
postgresql+asyncpg://knowledge:${KNOWLEDGE_DB_PASSWORD}@127.0.0.1:5434/knowledge_system
```

普通 PostgreSQL 连接串：

```text
postgresql://knowledge:${KNOWLEDGE_DB_PASSWORD}@127.0.0.1:5434/knowledge_system
```

这是本地开发凭据。迁移到共享、测试或生产环境前必须更换密码。

### 3.3 数据库表

已建立以下业务表：

| 表 | 用途 |
|---|---|
| `knowledge.sources` | 来源、渠道、域名、类型、授权确认和状态 |
| `knowledge.documents` | 文章/文档正文、URL、语言、市场、内容哈希和全文索引 |
| `knowledge.claims` | 可被 Agent 使用的结构化知识候选、审核状态和建议动作 |
| `knowledge.evidence` | Claim 对应的证据摘录和原文位置 |
| `knowledge.usages` | 知识被哪个生成阶段、查询或任务使用及其结果 |
| `knowledge.schema_migrations` | 当前 schema 初始化版本 |

当前迁移记录：`001_init`。

当前数据量：

```text
sources=0
documents=0
claims=0
evidence=0
usages=0
```

因此现在是一个真正干净的知识库，可以直接作为新项目基础库。

### 3.4 已完成数据库直接 SQL smoke test

以下验证通过 `psql` 直接操作数据库完成，不代表后端 import/review/retrieve 接口或 React 页面已经实现：

已实际验证：

1. 容器健康检查通过；
2. `knowledge_system` 可用 `knowledge` 账号连接；
3. schema 和全部表存在；
4. documents 和 claims 的 GIN 全文索引存在；
5. 测试写入 source、document、pending claim 和 evidence 成功；
6. pending claim 检索结果为 0；
7. 改为 approved 后检索结果为 1；
8. 测试事务回滚后测试数据为 0；
9. 原业务数据库 `seo_workbench` 中不存在 `knowledge` schema；
10. 知识库和业务库使用不同 volume，数据物理隔离。

## 4. 核心数据模型

### 4.1 sources

关键字段：

- `id`：UUID；
- `name`：例如 `Ahrefs Blog`；
- `channel`：例如 `seo`、`social`、`forum`、`video`、`visual`；
- `source_type`：第一版为 `manual`；
- `base_url`、`domain`；
- `rights_confirmed`：是否确认有权保存/使用该资料；
- `status`：`active` / `disabled`；
- `metadata`；
- `created_at`、`updated_at`。

同一 `lower(name) + channel` 唯一。

### 4.2 documents

关键字段：

- `source_id`；
- `canonical_url`；
- `title`；
- `content_type`；
- `language_code`、`market`；
- `author`、`published_at`；
- `raw_content`；
- `content_hash`：SHA-256，用于幂等去重；
- `status`；
- `search_vector`：PostgreSQL `simple` 全文索引；
- `metadata` 和时间字段。

同一来源的 `source_id + content_hash` 唯一；非空 canonical URL 也有唯一索引。

### 4.3 claims

关键字段：

- `document_id`；
- `channel`、`topic`、`knowledge_type`；
- `statement`：单一、可执行或可验证的知识结论；
- `conditions`、`exceptions`；
- `recommended_action`；
- `confidence`；
- `review_status`：`pending` / `approved` / `rejected`；
- `review_note`、`reviewed_by`、`reviewed_at`；
- `search_vector`；
- `metadata` 和时间字段。

只有 `approved` Claim 可以进入生产检索。

### 4.4 evidence

每条 Claim 可对应一条或多条证据：

- `claim_id`；
- `document_id`；
- `excerpt`：必要的短摘录；
- `locator`：例如 `paragraph:3` 或章节名。

检索结果必须带回来源、文档和 evidence，不能只返回没有出处的结论。

### 4.5 usages

用于记录知识使用闭环：

- `claim_id`；
- `stage`：brief、article、social、forum、video 等；
- `query`；
- `context`：使用时的知识快照和过滤条件；
- `result`：后续任务结果或效果数据。

## 5. 已确定的架构决策

### 5.1 知识库必须与业务库隔离

不要把动态知识塞进已有 RuleStore 或业务数据库。规则是少量、版本化、全局约束；知识资料有来源、时效、审核、冲突、检索和引用生命周期。混合后会让缓存、审核和回放失控。

新项目应保持两个独立连接：

```text
业务数据库连接 → 站点、关键词、任务、文章
知识数据库连接 → 来源、文档、Claim、Evidence、Usage
```

两个数据库之间不建立 FK，不伪装成一个事务。业务 ID 只作为外部引用或快照保存。

### 5.2 第一版不自动抓取任意 URL

第一版页面只接受：

- URL 元数据；
- 用户手动粘贴正文或 Markdown；
- 用户确认有权使用内容。

服务端和浏览器都不主动抓取任意 URL，避免 SSRF、CORS、版权和网站条款风险。后续如果要自动同步 Ahrefs，应该新增受控来源 Adapter、白名单、速率限制和授权策略，而不是开放任意 URL 抓取。

### 5.3 审核优先于自动化

建议第一版采用确定性候选提炼：

- 按正文段落切分；
- 清理 Markdown 前缀；
- 去重；
- 最多生成 8 条 pending Claim；
- 保存原段落为 evidence；
- locator 使用 `paragraph:n`。

后续可增加 AI 提炼，但 AI 产出的 Claim 仍必须是 pending，不能绕过人工审核。

### 5.4 第一版不引入 pgvector

优先使用：

- channel、market、language、review status 精确过滤；
- PostgreSQL 全文索引；
- `ILIKE` 字面匹配 fallback；
- 来源可信度、证据和时效排序。

只有真实检索评估证明这些能力不足时，再引入 embedding 和 pgvector。不要在数据还是 0 条时提前增加向量系统复杂度。

### 5.5 外部内容永远是不可信数据

Ahrefs、论坛、社媒、视频转录中的文本不得作为系统指令执行。写入 Agent prompt 时，应放入明确的数据区块，限制长度，并保留来源和 Claim ID。外部内容不能覆盖系统规则、站点范围、市场、语言和人工审核门禁。

## 6. 目标页面

新项目中的“知识系统”页面应按照已有 HTML 原型实现，并以三个主要工作区形成闭环。

### 6.1 资料库

- 总览 KPI：来源、文档、pending / approved / rejected Claim；
- 手动导入表单；
- 来源列表；
- 文档列表和 Claim 数；
- 重复导入提示；
- 明确提示 URL 只作元数据，不会自动抓取；
- 必须勾选授权确认。

导入字段：

```text
source_name
canonical_url
title
channel
content_type
language_code
market
author（可选）
published_at（可选）
raw_content
rights_confirmed
```

### 6.2 知识审核

- 展示 pending Claim；
- 展示来源、文章标题、URL、证据摘录和 locator；
- 展示适用条件、例外、建议动作和置信度；
- 支持批准、拒绝和审核备注；
- 单条操作有 loading；
- rejected Claim 永远不进入检索。

### 6.3 检索实验室

- 输入 query；
- 选择 channel、market、language；
- 默认 limit=5；
- 只返回 approved Claim；
- 展示命中原因、score、来源、文档和 evidence；
- 展示最终会发给 Agent 的 KnowledgePack。

原型中的 Overview、Playbook 和 Agent 大脑状态可以保留为顶部 KPI 或后续标签，但第一版交付优先完成上述三个工作区。

## 7. 目标后端 Interface

建议路由：

```text
GET  /api/v1/knowledge/health
GET  /api/v1/knowledge/overview
GET  /api/v1/knowledge/sources
GET  /api/v1/knowledge/documents
GET  /api/v1/knowledge/claims?review_status=pending
POST /api/v1/knowledge/documents/import
POST /api/v1/knowledge/claims/{claim_id}/review
POST /api/v1/knowledge/retrieve
```

### 7.1 import

请求体使用第 6.1 节字段。

成功响应建议：

```json
{
  "created": true,
  "duplicate": false,
  "source": {},
  "document": {},
  "claims_created": 6
}
```

约束：

- `rights_confirmed` 必须为 true；
- URL 仅校验并保存，不发网络请求；
- source 按 `lower(name) + channel` upsert；
- document 按 content hash 幂等；
- document、claims、evidence 在一个知识库事务中写入；
- 重复文档不得重复生成 Claim。

### 7.2 review

请求示例：

```json
{
  "decision": "approved",
  "reviewer": "local-user",
  "note": "Evidence checked"
}
```

decision 只允许 `approved` 或 `rejected`。

### 7.3 retrieve

请求示例：

```json
{
  "query": "how to match search intent",
  "channel": "seo",
  "market": "US",
  "language_code": "en",
  "limit": 5
}
```

响应中每条 item 必须包含：

```text
claim
source
document
evidence[]
score
```

### 7.4 错误语义

- `400`：无效输入、未确认授权、空正文；
- `404`：Claim 或文档不存在；
- `409`：不可接受的重复/状态冲突；
- `503`：知识数据库不可达或 schema 未初始化。

## 8. 新项目建议目录

如果继续使用原技术栈（FastAPI + SQLAlchemy AsyncSession + React + TypeScript + Vite），建议：

```text
docker-compose.knowledge.yml
db/
  knowledge/
    migrations/
      001_init.sql
app/
  core/
    knowledge_database.py
  services/
    knowledge_service.py
  api/v1/
    knowledge.py
frontend/src/
  types/
    knowledge.ts
  hooks/
    useKnowledge.ts
  pages/
    RulesPage.tsx   # 或改名 KnowledgePage.tsx
docs/
  architecture.md
  integration-guide.md
  operator-runbook.md
  handoff.md
```

`knowledge_service.py` 应作为深 Module：调用方只理解 import、review、retrieve 等少量 Interface；采集、清洗、去重、候选提炼、全文检索、权限门禁和 usage 记录藏在内部。

新项目如果采用其他后端框架，也应保留上述 Interface 和数据库边界，不必照搬 Python 文件名。

## 9. 当前未完成事项

以下内容只完成了设计，未写入原项目：

- `docker-compose.knowledge.yml`；
- 项目内知识库迁移目录；
- 独立应用配置 `KNOWLEDGE_DATABASE_URL`；
- 独立 SQLAlchemy engine / session；
- 知识导入、审核、检索后端路由；
- 真实 React 知识系统页面；
- 前端 hooks 和 DTO；
- 自动化测试；
- Brief、文章、社媒、论坛、视频 Agent 的知识注入；
- Usage 与 GSC/GA4/发布效果闭环；
- 项目正式文档和部署说明。

原因：原项目共享目录起初只有只读 ACL，随后宿主电脑搬走，`Y:` 永久断开。此次对话没有把任何知识系统代码写入原项目。

## 10. 原项目背景和不要继承的历史问题

原项目技术栈：

- FastAPI；
- SQLAlchemy async + asyncpg；
- 裸 SQL，无 declarative ORM；
- React 18 + TypeScript + Vite；
- PostgreSQL 17；
- Redis 7。

原业务数据库存在迁移漂移：

- Alembic revision 只到 v6；
- raw SQL 文件已经到 008；
- 当时实际业务数据库停在 v5；
- 原 `pg-workbench` 容器不是仓库 compose 正常管理的容器，volume 也与 compose 预期不一致。

新项目不要复制这套业务迁移历史，也不要重建或复用旧 `pg-workbench`。知识库应保持独立 compose project、独立 volume、独立迁移链。

本机还有 `Z:\` 指向一个旧副本，但它缺少当时最新前端文件和有效 Git 元数据，不应把它当成原项目真源。

## 11. 复用当前数据库还是迁移

### 方案 A：新项目直接使用当前知识库

适合继续在本机开发：

```text
KNOWLEDGE_DATABASE_URL=postgresql+asyncpg://knowledge:${KNOWLEDGE_DB_PASSWORD}@127.0.0.1:5434/knowledge_system
```

优点：数据库已 healthy、schema 已验证，可以立即开发。  
注意：容器目前不是由新项目 compose 管理。新项目落地后应创建 compose 配置，但不要同时启动第二个占用 5434 的容器。

当前容器还把下面的临时 Codex 路径以只读 bind mount 挂到 `/docker-entrypoint-initdb.d/001_init.sql`：

```text
C:\Users\PC\.codex\visualizations\2026\07\14\019f5e85-81c9-7003-854c-9d5f9196eb0d\knowledge-db\001_init.sql
```

在把 SQL 固化到新项目并建立可复现 Compose 之前，不要移动或删除这个文件，也不要重建容器。安全切换顺序是：先复制交付物和备份数据库，再创建新 Compose，最后验证新容器后才处理旧容器。

### 方案 B：把数据库同步到新环境

当前数据库为空，最简单的方法是直接运行 `001_init.sql`，无需 dump。

将来有真实数据后再备份：

```powershell
docker exec knowledge-postgres pg_dump -U knowledge -d knowledge_system -Fc -f /tmp/knowledge_system.dump
docker cp knowledge-postgres:/tmp/knowledge_system.dump .\knowledge_system.dump
```

在目标 PostgreSQL 创建空库后恢复：

```powershell
docker cp .\knowledge_system.dump <target-container>:/tmp/knowledge_system.dump
docker exec <target-container> pg_restore -U <target-user> -d <target-db> --clean --if-exists /tmp/knowledge_system.dump
```

对含真实数据的数据库执行 `--clean` 前必须确认目标库，避免删除错误环境的数据。

## 12. 当前数据库运维命令

查看状态：

```powershell
docker ps --filter "name=^knowledge-postgres$"
docker inspect knowledge-postgres --format "{{.State.Health.Status}}"
```

检查表：

```powershell
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT table_name FROM information_schema.tables WHERE table_schema='knowledge' ORDER BY table_name;"
```

检查数据量：

```powershell
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT (SELECT count(*) FROM knowledge.sources) sources,(SELECT count(*) FROM knowledge.documents) documents,(SELECT count(*) FROM knowledge.claims) claims,(SELECT count(*) FROM knowledge.evidence) evidence,(SELECT count(*) FROM knowledge.usages) usages;"
```

停止和重新启动：

```powershell
docker stop knowledge-postgres
docker start knowledge-postgres
```

上述 stop/start 仅适用于临时 bind mount 文件仍存在的当前容器。若路径已经变化，应先备份并用新项目 Compose 重建，不要直接尝试恢复。

不要执行以下操作，除非已经备份且明确要删除全部知识：

```text
docker rm -v knowledge-postgres
docker volume rm seo20-knowledge-db-data
docker compose down -v
```

## 13. 新项目第一阶段实施顺序

1. 把本文、`001_init.sql` 和两份 HTML 原型共四个文件复制进新项目，并核对已提供的 SHA-256；
2. 决定直接使用当前数据库，还是建立新容器并运行迁移；
3. 添加 `KNOWLEDGE_DATABASE_URL`，与业务 `DATABASE_URL` 分开；
4. 建立独立 knowledge engine/session；
5. 实现 health 和 overview；
6. 实现手动导入、去重、pending Claim 和 evidence；
7. 实现审核；
8. 实现 approved-only 检索和 usage 日志；
9. 按 HTML 原型完成资料库、审核和检索实验室；
10. 完成一次真实 Ahrefs 文章手动导入闭环；
11. 将 KnowledgePack 接入 Brief；
12. 再扩展文章、社媒、论坛、视频和图文 Agent。

## 14. 第一阶段验收标准

- 全新数据库可重复初始化；
- health 能区分“数据库不可达”和“表未初始化”；
- 一篇手动粘贴文章可生成 pending Claim；
- 相同正文重复导入不产生第二份数据；
- pending 和 rejected Claim 检索不到；
- approved Claim 可以检索到；
- 每条检索结果可追溯到 source、document 和 evidence；
- 页面能完成导入、审核、检索；
- 服务端没有自动访问用户输入的 URL；
- 外部正文不能改变系统指令；
- 数据库只对本机或受信网络开放；
- 项目包含 architecture、integration、runbook 和 handoff 文档。

## 15. 主要风险

- 版权和网站条款：默认只存授权资料，保留必要证据短摘录；
- Prompt injection：外部内容永远作为不可信数据；
- 知识污染：未经批准的 Claim 不进入生产检索；
- 过时和冲突：后续增加 review_after、来源可信度和冲突状态；
- 不可复现：Agent 任务必须保存 Claim ID 和当时 KnowledgePack 快照；
- Token 膨胀：每次只取 3–5 条高相关知识；
- 未认证接口：导入和审核接口对外开放前必须加认证；
- 本地开发密码：迁移到非本机环境必须轮换；
- 无备份：开始录入真实资料前建立定期 `pg_dump`；
- 容器被误删：不要删除 `seo20-knowledge-db-data`。

## 16. 本地交付物

本次对话产生的本地文件均位于：

```text
C:\Users\PC\.codex\visualizations\2026\07\14\019f5e85-81c9-7003-854c-9d5f9196eb0d\
```

| 文件 | SHA-256 |
|---|---|
| `KNOWLEDGE_SYSTEM_HANDOFF_2026-07-14.md`（本文） | 不内嵌自身哈希，复制后用 `Get-FileHash` 重新计算 |
| `knowledge-db\001_init.sql`（6,115 bytes） | `BE908BD2A1A39EBE0134DBBBEF4D78C0F1D42D2D893682A7977F16906F6EEE8B` |
| `knowledge-system-workbench.html`（38,875 bytes） | `0366A7E9A249E7F6372FE8D0524FD4C8EFC4F0D57FCE2ADFDEA921FB4461824C` |
| `knowledge-system-prototype-standalone.html`（80,073 bytes） | `55525B7B1409658D715FA4DBB8613B035F5AA8E65A8F5913BA55526B8FE36CF6` |

## 17. 可直接交给新项目 Agent 的任务说明

```text
请先完整阅读 KNOWLEDGE_SYSTEM_HANDOFF_2026-07-14.md 和 db/knowledge/migrations/001_init.sql。

目标：在当前新项目中实现独立知识系统，复用正在运行的 knowledge-postgres 数据库，或按迁移脚本建立等价数据库。第一阶段只允许 URL 元数据 + 手动粘贴正文，不抓取任意 URL。

必须完成：
1. 独立 KNOWLEDGE_DATABASE_URL 和数据库 session；
2. health、overview、sources、documents、claims、import、review、retrieve 接口；
3. 导入时幂等去重并生成最多 8 条 pending Claim/evidence；
4. 只有 approved Claim 可以检索；
5. 检索结果必须带 source/document/evidence；
6. 按 knowledge-system-prototype-standalone.html 实现资料库、知识审核、检索实验室；
7. 验证“导入 → pending 不可检索 → approve → 可检索”的真实闭环；
8. 补 architecture、integration-guide、operator-runbook 和 handoff 文档。

不要复用旧业务数据库迁移链，不要引入 pgvector，不要自动抓 URL，不要让外部正文覆盖系统指令，不要修改无关代码。
```
