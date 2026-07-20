# SEO 2.0 项目迁移手册

日期：2026-07-20
适用版本：本地检查点 `3c79998 checkpoint project for migration`
迁移方式：将 `seo2.0` 文件夹压缩后复制到另一台电脑，再解压运行

## 0. 迁移目标

迁移完成后，目标电脑应能独立运行：

```text
SEO Workbench 主服务
  ├─ PostgreSQL：业务库
  ├─ Redis：任务/缓存依赖
  ├─ FastAPI：8000
  └─ React/Vite 前端：5173 或生产静态文件

独立 Knowledge 服务（可选但建议保留）
  ├─ Knowledge PostgreSQL：5434
  ├─ Knowledge API：8010
  └─ Knowledge 前端：5174
```

本项目当前仍保留这些边界：

- 站点地图使用本地上传覆盖，不使用远程 URL 抓取；
- 主站知识画像和主站策略仍需人工确认；
- `AUTOMATION_ENABLED` 迁移初期必须保持 `false`；
- 外站发布前保留人工审批；
- Knowledge 服务与主业务数据库物理隔离。

## 1. 压缩包准备

### 1.1 建议纳入压缩包

纳入：

- `app/`、`frontend/src/`、`knowledge/`；
- `db/`、`scripts/`、`workflows/`；
- `docs/`、`TXT/`、`PROJECT_PROGRESS.md`；
- `requirements.txt`、`frontend/package.json`、锁文件；
- `knowledge/backend/requirements.txt`、`knowledge/frontend/package.json`；
- `config/*.local.json`（仅在压缩包使用加密保护时纳入）。

### 1.2 不建议纳入压缩包

以下内容在目标电脑重新生成：

```text
.venv/
frontend/node_modules/
frontend/dist/
knowledge/backend/.venv/
knowledge/frontend/node_modules/
logs/
.pytest_cache/
**/__pycache__/
```

以下是数据，不是代码：

```text
exports/
```

如果要恢复当前 Knowledge Claim 数据，需要单独传输 `exports/knowledge_claims_2026-07-18`，或者直接迁移 Knowledge PostgreSQL 备份。

### 1.3 必须单独保护的文件

这些文件包含密钥、Token 或 Service Account 私钥，不要上传到公开网盘、Git 或聊天窗口：

```text
.env
knowledge/.env
config/ai-stages.local.json
config/blog-sites.local.json
config/google-data-sources.local.json
config/image-providers.local.json
config/main-sites.local.json
config/product-assets.local.json
config/serpapi.local.json
config/wp-sites.local.json
```

可以将它们放进加密压缩包，或在目标电脑上重新填写。

## 2. 目标电脑依赖

建议版本：

- Windows 10/11；
- PowerShell 5+ 或 PowerShell 7；
- Python 3.11+；
- Node.js 20+；
- npm；
- Docker Desktop；
- Git 可选，压缩包迁移不依赖 Git。

检查：

```powershell
python --version
node --version
npm --version
docker --version
docker compose version
```

## 3. 解压与安装 Python/Node 依赖

进入解压后的项目根目录：

```powershell
Set-Location D:\实际路径\seo2.0
```

主服务：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

主前端：

```powershell
npm --prefix frontend ci
```

Knowledge 后端：

```powershell
python -m venv knowledge\backend\.venv
.\knowledge\backend\.venv\Scripts\python.exe -m pip install --upgrade pip
.\knowledge\backend\.venv\Scripts\python.exe -m pip install -r knowledge\backend\requirements.txt
```

Knowledge 前端：

```powershell
npm --prefix knowledge\frontend ci
```

## 4. 主服务配置

复制配置模板：

```powershell
Copy-Item .env.example .env
```

至少修改：

```dotenv
APP_ENV=dev
APP_PORT=8000

DATABASE_URL=postgresql+asyncpg://seo:密码@127.0.0.1:5433/seo_workbench
REDIS_URL=redis://127.0.0.1:6379/0
```

根据实际使用情况填写：

```dotenv
AI_KEYWORD_ANALYSIS_BASE_URL=...
AI_KEYWORD_ANALYSIS_KEY=...
AI_KEYWORD_ANALYSIS_MODEL=...
AI_BRIEF_GENERATION_BASE_URL=...
AI_BRIEF_GENERATION_KEY=...
AI_BRIEF_GENERATION_MODEL=...
AI_ARTICLE_GENERATION_BASE_URL=...
AI_ARTICLE_GENERATION_KEY=...
AI_ARTICLE_GENERATION_MODEL=...
SERPAPI_KEY=...
IMAGE_PEXELS_KEY=...
IMAGE_UNSPLASH_KEY=...
IMAGE_PIXABAY_KEY=...
SHOPIFY_CLIENT_ID=...
SHOPIFY_CLIENT_SECRET=...
```

迁移初期保持：

```dotenv
AUTOMATION_ENABLED=false
```

### 4.1 Google 数据源

复制 `config/google-data-sources.local.json` 到目标项目的 `config/`。如果放在别处，设置：

```dotenv
GOOGLE_DATA_SOURCES_PATH=D:\安全路径\google-data-sources.local.json
```

不要把 Service Account 的 `private_key` 写入 `.env`、前端、日志或文档。

### 4.2 本地配置文件

将以下配置文件复制到目标项目 `config/`，或在目标电脑重新填写：

```text
ai-stages.local.json       AI 阶段、模型和 Key
blog-sites.local.json      自定义博客站点配置
google-data-sources.local.json  GSC/GA4/Service Account
image-providers.local.json 图片服务 Key
main-sites.local.json      主站配置
product-assets.local.json  产品数据和素材
serpapi.local.json         SERP API Key
wp-sites.local.json        WordPress 配置
```

## 5. 主业务 PostgreSQL 与 Redis

### 5.1 新环境

项目根目录的 `docker-compose.yml` 默认启动：

- PostgreSQL `127.0.0.1:5433`；
- Redis `127.0.0.1:6379`。

启动：

```powershell
docker compose up -d postgres redis
docker compose ps
```

### 5.2 初始化全新业务库

数据库迁移文件位于 `db/migrations/`，必须按文件名顺序执行 `001` 到最新编号。

最简单的方式是在 Git Bash 中运行：

```bash
bash scripts/start.sh
```

或者按 [db/README.md](../../db/README.md) 手动执行全部 SQL，再运行：

```powershell
node db/scripts/seed_rule_baseline.mjs
```

注意：`psql` 使用 `postgresql://...` 连接串；FastAPI 使用 `postgresql+asyncpg://...` 连接串。

### 5.3 恢复已有业务数据

如果需要保留原电脑的数据，优先迁移 PostgreSQL 备份，不要只复制 Docker 项目文件夹，因为 Docker volume 通常不在项目目录内。

迁移前需要备份：

- sites 和 products；
- posts、articles；
- keywords、SERP、GSC、GA4；
- tasks、策略计划、执行记录、效果记录；
- `knowledge_profile`。

恢复已有数据库后，不要重复执行会清空数据的脚本。只补执行目标版本缺失的幂等 migration，并验证 migration 编号。

## 6. 独立 Knowledge 服务

复制配置：

```powershell
Copy-Item knowledge\.env.example knowledge\.env
```

填写：

```dotenv
KNOWLEDGE_DB_PASSWORD=强随机密码
KNOWLEDGE_DATABASE_URL=postgresql+asyncpg://knowledge:密码@127.0.0.1:5434/knowledge_system
KNOWLEDGE_AI_BASE_URL=...
KNOWLEDGE_AI_API_KEY=...
KNOWLEDGE_AI_MODEL=...
```

新建 Knowledge 数据库：

```powershell
docker compose --env-file knowledge\.env -f knowledge\docker-compose.yml up -d postgres
```

启动 Knowledge API：

```powershell
.\knowledge\start-backend.ps1
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/v1/knowledge/health
```

如果要保留原 Knowledge 数据，使用数据库备份恢复；如果只迁移 Claim 导出，则使用 `exports/knowledge_claims_2026-07-18`，并先确认目标数据库结构和导入脚本。

## 7. 前端配置

主前端构建时设置 API 地址：

```powershell
$env:VITE_API_BASE_URL='http://127.0.0.1:8000'
npm --prefix frontend run build
```

如果前端和 API 在同一台电脑但不同端口，保持上述配置即可。

如果使用局域网访问，例如目标电脑 IP 为 `192.168.1.20`：

```powershell
$env:VITE_API_BASE_URL='http://192.168.1.20:8000'
npm --prefix frontend run build
```

Knowledge 前端单独构建时设置：

```powershell
$env:VITE_KNOWLEDGE_API_URL='http://127.0.0.1:8010'
npm --prefix knowledge\frontend run build
```

## 8. 本机访问与局域网访问

### 8.1 只在目标电脑本机使用

保持默认绑定：

```text
主后端 127.0.0.1:8000
Knowledge API 127.0.0.1:8010
数据库 127.0.0.1:5433/5434
Redis 127.0.0.1:6379
```

这是最简单、最安全的迁移方式。

### 8.2 允许同一局域网其他设备访问

需要同时处理：

1. 将 FastAPI/Knowledge API 的监听地址改为 `0.0.0.0`；
2. 将前端 `VITE_*_API_URL` 改为目标电脑的局域网 IP；
3. 开放 Windows 防火墙中的前端/API端口；
4. 将生产 CORS 改为允许指定前端来源；
5. 数据库和 Redis 仍只绑定 `127.0.0.1`，不要对局域网开放。

当前代码默认 CORS 主要面向本机开发。若采用局域网跨域访问，MiniMax 需要修改 `app/main.py` 和 `knowledge/backend/app/config.py`，将允许来源改为明确的局域网前端地址。不要为了省事开放数据库端口。

### 8.3 反向代理部署

推荐用 Nginx/Caddy 将前端和 API 放到同一域名：

```text
https://seo.example.com/       → frontend
https://seo.example.com/api/   → FastAPI 8000
https://seo.example.com/knowledge/ → Knowledge API 8010（可选）
```

同源部署可以减少 CORS 配置，也不需要公开 8000、8010、5433、5434 和 6379。

## 9. 启动顺序

```text
1. Docker Desktop
2. 主 PostgreSQL + Redis
3. 主数据库 migration/restore
4. Knowledge PostgreSQL（如果启用）
5. Knowledge API（如果启用）
6. SEO Workbench FastAPI
7. 主前端
8. Google、站点连接器和 AI 探活
```

主后端：

```powershell
.\start-backend.bat start
```

主前端开发模式：

```powershell
npm --prefix frontend run dev
```

## 10. 迁移后验收

### 基础服务

- `docker compose ps` 中 PostgreSQL 和 Redis healthy；
- 主 API `/docs` 能打开；
- Knowledge `/api/v1/knowledge/health` 正常；
- 前端请求的 API 地址不是 `127.0.0.1` 的错误地址。

### 业务数据

- sites、products、posts、articles、keywords 数量符合原环境；
- GSC/GA4 数据源探活通过；
- 站点连接器鉴权通过；
- 主站索引页面数量正确；
- 本地上传 `.xml/.txt/.gz` 可以覆盖和合并。

### 策略安全

- 主站知识画像先生成 `draft`；
- 人工确认后再生成策略；
- `AUTOMATION_ENABLED=false` 时不会自动执行；
- 首条策略人工批准后，核对生成、发布回读和效果记录；
- 未确认知识或缺少证据的候选进入 `Hold`。

## 11. 回滚与故障处理

- 代码回滚：切回已验证的提交或恢复压缩包备份；
- 数据库回滚：使用迁移前的 PostgreSQL 备份恢复，不要生产环境直接 DROP；
- 配置回滚：恢复目标电脑的 `.env` 和 `config/*.local.json` 备份；
- Knowledge 数据回滚：恢复独立 Knowledge 数据库备份；
- 外站发布异常：先关闭 `AUTOMATION_ENABLED`，保留本地任务记录，不要直接删除 tasks。

## 12. 迁移禁止事项

- 不把真实 `.env`、Service Account 私钥、站点 Token 提交 Git；
- 不把数据库和 Redis 端口暴露公网；
- 不把 `AUTOMATION_ENABLED` 作为迁移成功的证明；
- 不在没有备份的情况下运行 reset/清空脚本；
- 不因为迁移方便而重新启用已经移除的远程 Sitemap URL 抓取。
