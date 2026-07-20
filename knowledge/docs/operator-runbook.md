# 知识数据库运维手册

## 当前状态与防误操作

截至 2026-07-18，本机 `knowledge-postgres` 已切换为本目录 Compose 创建的新容器：

| 项目 | 值 |
|---|---|
| Image | `postgres:17-alpine` |
| Host port | `127.0.0.1:5434` |
| Database / user | `knowledge_system` / `knowledge` |
| Schema | `knowledge` |
| Volume | `knowledge-system-postgres-data` |
| Restart | `unless-stopped` |
| Compose 管理 | 是 |

当前数据库是新建空库，已执行 001–005 初始化迁移。另一台电脑的旧容器/volume 不在本机接管范围内；不要删除任何旧容器或 volume。

## 日常只读检查

```powershell
docker ps --filter "name=^knowledge-postgres$"
docker inspect knowledge-postgres --format "{{.State.Health.Status}}"
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT version, applied_at FROM knowledge.schema_migrations ORDER BY applied_at;"
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT (SELECT count(*) FROM knowledge.sources) sources,(SELECT count(*) FROM knowledge.documents) documents,(SELECT count(*) FROM knowledge.claims) claims,(SELECT count(*) FROM knowledge.evidence) evidence,(SELECT count(*) FROM knowledge.usages) usages;"
```

应用健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/v1/knowledge/health
```

完整显示 health 响应可用于确认 API、数据库和 schema 正常：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/v1/knowledge/health | ConvertTo-Json -Depth 8
```

AI 配置只放在 `Y:\knowledge\.env`，并由后端进程读取：

```dotenv
KNOWLEDGE_AI_BASE_URL=https://api.openai-proxy.org/v1
KNOWLEDGE_AI_API_KEY=你的本机密钥
KNOWLEDGE_AI_MODEL=gpt-5-mini
KNOWLEDGE_AI_TIMEOUT_SECONDS=90
KNOWLEDGE_AI_MAX_ATTEMPTS=2
```

真实 Key 不得写入前端、Git、文档、命令历史或故障截图。Windows 上开启资源管理器的“文件扩展名”，确认保存的是 `.env` 而非 `.env.txt`；修改后重启后端。不要用输出整个环境或读取 `.env` 的命令排障。

## 备份

开始录入真实资料前至少完成一次备份演练。下面命令创建 custom-format dump，不打印密码：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path $env:USERPROFILE 'knowledge-backups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
docker exec knowledge-postgres pg_dump -U knowledge -d knowledge_system -Fc -f /tmp/knowledge_system.dump
docker cp knowledge-postgres:/tmp/knowledge_system.dump (Join-Path $backupDir "knowledge_system-$stamp.dump")
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $backupDir "knowledge_system-$stamp.dump")
```

共享 `Y:` 上的副本不等于异机备份。真实数据至少保留一份在不同物理设备或受控备份位置，并记录时间、数据库版本、文件大小和 SHA-256。不要把备份文件提交到 Git。

## 清空知识业务数据

只有在明确确认要重新采集、并完成清空前备份后，才执行此操作。它清除来源、文档、Claim、Evidence、Usage、批量任务和质量审计，但保留 schema、迁移记录、代码、`.env` 和备份文件。先停止 API/Worker，再在确认容器名和数据库名后执行：

```powershell
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -v ON_ERROR_STOP=1 -c "BEGIN; TRUNCATE TABLE knowledge.claim_quality_reviews, knowledge.quality_review_items, knowledge.quality_review_runs, knowledge.crawl_items, knowledge.sync_runs, knowledge.ingestion_sources, knowledge.usages, knowledge.evidence, knowledge.claims, knowledge.documents, knowledge.sources RESTART IDENTITY CASCADE; COMMIT;"
```

执行后必须确认所有业务表为 0、`knowledge.schema_migrations` 仍包含当前迁移版本，再启动 API 并检查 `/health` 和 `/overview`。不要用 `DROP SCHEMA`、`docker compose down -v` 或删除 volume 代替清空业务数据。

## 从手工容器切换到 Compose

此操作不是日常启动步骤。需要明确维护窗口，而且只有一个操作者。

1. 停止知识 API，确保没有新写入。
2. 按上一节备份，并在容器外核对 dump 文件存在且大小合理。
3. 记录当前容器、镜像、端口、mount 和数据量。
4. 停止旧容器并改名保留回退点；不要删除它，也不要删除旧 volume。
5. 从 `.env.example` 创建本机 `.env`，设置新密码和 URL。
6. 用 Compose 启动新容器，它会在新 volume 上按文件名顺序执行 `001_init.sql`、`002_batch_ingestion.sql`、`003_claim_quality_gate.sql`、`004_market_signals.sql` 和 `005_signal_crawl_jobs.sql`。
7. 若旧库仍为空，只验证 schema，不需要 restore。若已有真实数据，严格按下一节在确认的新目标上恢复。
8. 运行 health、表计数和“导入 → 审核 → 检索”smoke test。
9. 观察稳定并再次备份后，才能另行决定旧容器/volume 生命周期；本手册不授权删除。

切换命令（完成步骤 1–3 后才执行）：

```powershell
Set-Location Y:\knowledge
docker stop knowledge-postgres
docker rename knowledge-postgres knowledge-postgres-manual-backup-20260715
docker compose --env-file .env config
docker compose --env-file .env up -d postgres
docker compose ps
```

若新容器验证失败，可先停 Compose 容器、改名，然后将旧容器改回原名并启动。不要使用 `down -v`。

## 恢复

恢复会替换目标库对象，具有破坏性。只有在以下条件全部满足时才能使用 `--clean --if-exists`：目标确认为新建/待恢复的知识库，当前写入已停止，备份哈希已核对，且已明确接受覆盖目标数据。绝不要把占位符容器名直接复制执行。

```powershell
docker cp C:\path\to\knowledge_system.dump <target-container>:/tmp/knowledge_system.dump
docker exec <target-container> pg_restore -U knowledge -d knowledge_system --clean --if-exists --no-owner /tmp/knowledge_system.dump
```

恢复后检查：

```powershell
docker exec <target-container> psql -U knowledge -d knowledge_system -X -c "SELECT version FROM knowledge.schema_migrations ORDER BY applied_at;"
docker exec <target-container> psql -U knowledge -d knowledge_system -X -c "SELECT count(*) FROM knowledge.documents; SELECT count(*) FROM knowledge.claims;"
```

然后启动 API 并验证 `/health`、抽样 Evidence 追溯和 approved-only 检索。

## 迁移：严格单写者

1. 一个变更对应一个新的、不可变 SQL 文件，例如 `002_add_review_after.sql`；绝不改写已经应用的 `001_init.sql`。
2. 在共享 `Y:` 上先声明编号和文件所有权，其他电脑/Agent 不得并发创建或执行迁移。
3. 停止写入、备份、在临时空库和当前版本副本上各验证一次升级。
4. 由唯一操作者在维护窗口执行；迁移本身使用事务，并在成功后写入 `knowledge.schema_migrations`。
5. 执行后核对版本、约束、索引、数据量和 API smoke test。
6. 失败时保留日志并按迁移设计回滚或从备份恢复；禁止通过删除 volume“重来”。

当前质量门禁迁移为 `003_claim_quality_gate.sql`；市场信号基础迁移为 `004_market_signals.sql`；公开关键词采集任务迁移为 `005_signal_crawl_jobs.sql`。004 新增独立的帖子/评论/回复信号表，005 新增采集任务和运行审计表。应用后应至少核对：

```powershell
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT version FROM knowledge.schema_migrations ORDER BY applied_at;"
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT to_regclass('knowledge.claim_quality_reviews'), to_regclass('knowledge.quality_review_runs'), to_regclass('knowledge.quality_review_items');"
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT to_regclass('knowledge.market_signals');"
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT to_regclass('knowledge.market_signal_crawl_jobs'), to_regclass('knowledge.market_signal_crawl_runs');"
docker exec knowledge-postgres psql -U knowledge -d knowledge_system -X -c "SELECT review_status, count(*) FROM knowledge.claims GROUP BY review_status ORDER BY review_status;"
```

历史数据执行 AI 质量筛选时，先在前端或 API 创建 dry-run 并等待终态，记录 run ID、成功/失败文档数和建议拒绝数。确认抽样和阈值后再单独执行 apply；不要把“启动 dry-run”和“应用拒绝”合成无人确认的一步。dry-run 前后 `review_status` 计数必须一致。误拒绝只能通过逐条 restore 撤销，不能直接批量改 SQL，因为恢复操作需要保留审计。

## 常见故障

### `503 database unreachable`

检查容器、端口、`KNOWLEDGE_DATABASE_URL`、密码 URL 编码和本机防火墙。不要在日志中打印完整连接串。

### `503 schema uninitialized`

确认连接的是 `knowledge_system` 而非业务库，查询 `knowledge.schema_migrations`。新空库按编号依次执行 `db/migrations/001_init.sql`、`002_batch_ingestion.sql`、`003_claim_quality_gate.sql`、`004_market_signals.sql` 和 `005_signal_crawl_jobs.sql`；非空库先备份并由迁移单写者判断，不能让应用自动建表。

### Compose 报名称或端口冲突

如果手工 `knowledge-postgres` 仍存在，这是预期保护。停止尝试，按“从手工容器切换到 Compose”先备份和协调；不要删除冲突容器。

### 共享盘文件突然变化

立即停止写操作，记录时间和文件哈希，与另一台电脑确认所有权。不要用格式化、覆盖复制或 Git reset 消除未知修改。

### 导入使用确定性回退

先用 `/health` 排除 API、数据库或 schema 故障，再检查导入响应的 `extraction_method`、`model`、`warning` 和安全脱敏后的后端日志。新导入成功使用 AI 时 `extraction_method="ai"`；回退时为 `deterministic_fallback`，`model` 为 `null`，`warning` 提供无敏感信息的原因；重复导入时这三个字段均为 `null`。常见回退原因是 `.env` 被保存成 `.env.txt`、后端未重启、变量名拼错、模型未对账号开放、Base URL 缺少 `/v1`、请求超时或 Provider 返回的结构未通过 Evidence 校验。

回退不是自动批准：确定性提取生成的 Claim 同样保持 `pending`，必须人工审核。不要为了消除回退而关闭 Evidence 校验，也不要把 API Key 放到请求正文或前端。若 Provider 持续失败，可暂时保留回退路径继续录入，待配置修复后用新的正文/受控重提取流程验证 AI；重复导入相同正文不会创建第二组 Claim。

### AI 质量任务长时间 processing

质量 Worker 每篇文档调用一次 Provider；单次请求受 `KNOWLEDGE_AI_TIMEOUT_SECONDS` 和 Provider 重试预算影响，因此 processing 可能持续数分钟。先查询 run 和 items，不要因页面刷新重复创建任务。进程重启后，过期租约会恢复；达到三次任务尝试后 item 标记 failed，run 仍完成并保留 `counts.failed`，其他成功结果仍可检查和应用。

若要停止，调用该 run 的 `/cancel`；系统会取消未开始项目，正在进行的请求会在当前尝试结束后收敛。不要通过重启数据库取消任务。Provider 修复后可新建 `include_reviewed=false` 的 dry-run，只会选择尚未分类或上次错误的 pending 卡片。

### URL 导入失败

先确认链接是无需登录即可访问的具体文章页，并使用 `https://` 或 `http://` 标准端口。系统会拒绝本机、私网、保留地址、非 HTML、超过 3 MiB、超过 3 次重定向、20 秒内无法完成以及识别正文不足 100 字的页面。纯 JavaScript、验证码或登录墙页面请展开前端的“手动粘贴正文”兜底，不要关闭地址安全校验。

## 永久禁止的捷径

除非已备份、明确选择永久删除全部知识，并另行获得授权，否则不要执行：

```text
docker rm -v knowledge-postgres
docker volume rm seo20-knowledge-db-data
docker volume rm knowledge-system-postgres-data
docker compose down -v
```

同样禁止把数据库绑定到 `0.0.0.0`、把真实密码写进仓库、绕过批量预览的范围与数量上限，以及在没有检索评估时引入 pgvector。
