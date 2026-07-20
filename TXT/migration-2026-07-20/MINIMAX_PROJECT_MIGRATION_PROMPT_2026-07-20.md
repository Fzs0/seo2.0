# MiniMax 项目整体迁移提示词

将下面整段提示词复制给 MiniMax，并把它的工作目录设置为解压后的 `seo2.0` 根目录。

```text
你现在是这个 SEO 2.0 项目的迁移工程师。目标是在当前这台新电脑上，把已经从压缩包解压的 seo2.0 项目恢复到可运行、可验收状态。

项目背景：
- 项目目录：当前工作目录就是 seo2.0 根目录。
- 代码检查点：3c79998 checkpoint project for migration。
- 主服务：FastAPI + PostgreSQL + Redis，默认端口 8000 / 5433 / 6379。
- 独立 Knowledge 服务：FastAPI + PostgreSQL，默认端口 8010 / 5434。
- 主前端：frontend，默认开发端口 5173。
- Knowledge 前端：knowledge/frontend，默认端口 5174。
- 站点地图现在只使用本地上传覆盖，不要恢复远程 URL 抓取功能。
- 主站知识画像、主站策略和自动发布仍需要人工确认，迁移期间不得擅自开启自动执行。

必须先阅读：
1. PROJECT_PROGRESS.md
2. TXT/migration-2026-07-20/SEO2_MIGRATION_GUIDE_2026-07-20.md
3. TXT/migration-2026-07-20/migration-checklist-2026-07-20.md
4. docs/STRATEGY_POLICY_V1_DRAFT.md
5. db/README.md
6. knowledge/README.md

执行原则：
- 先检查，再修改；不要盲目重构业务代码。
- 使用 PowerShell 命令，除非项目已有脚本明确要求 Git Bash。
- 保留已有代码和用户配置，不删除数据库、不运行 reset/清空脚本。
- 不输出、打印或回显任何 API Key、密码、Token、Service Account private_key。
- 如果发现 .env 或 config/*.local.json 缺失，生成缺失清单并请求用户补充，不要编造密钥。
- 如果目标电脑仅本机使用，可以保持 127.0.0.1；如果用户明确要求局域网访问，才修改监听地址和 CORS。
- 数据库和 Redis 不能为了方便暴露到公网。
- 不把 AUTOMATION_ENABLED 改成 true；除非用户明确要求并且人工验收已完成。
- 不新增远程 Sitemap 抓取逻辑。

第一阶段：环境检查
1. 检查 Python 3.11+、Node.js 20+、npm、Docker Desktop、docker compose。
2. 检查 .env、knowledge/.env 和 config/*.local.json 是否存在；只报告文件名和缺失项，不打印内容。
3. 检查是否存在 .venv、node_modules；不存在就创建，不要复制其他电脑的虚拟环境。
4. 检查当前提交和工作区状态。

第二阶段：安装依赖
执行：
- python -m venv .venv
- .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
- npm --prefix frontend ci
- python -m venv knowledge\\backend\\.venv
- .\\knowledge\\backend\\.venv\\Scripts\\python.exe -m pip install -r knowledge\\backend\\requirements.txt
- npm --prefix knowledge\\frontend ci

如果安装失败，记录准确错误和缺失的系统依赖，不要修改 requirements.txt 来绕过错误。

第三阶段：配置环境
1. 从 .env.example 创建 .env。
2. 从 knowledge/.env.example 创建 knowledge/.env。
3. 根据当前电脑填写数据库、Redis、AI、SERP、图片、Shopify、Knowledge AI 配置。
4. 如果本机使用，保留 127.0.0.1；不要主动开放公网。
5. 如果要从局域网访问，才将 API 监听地址改为 0.0.0.0，设置 VITE_API_BASE_URL，并把 CORS 改为明确的前端局域网地址。
6. 生产/迁移初期确保 AUTOMATION_ENABLED=false。

第四阶段：启动基础设施
1. 在项目根目录运行 docker compose up -d postgres redis。
2. 检查 docker compose ps 和 PostgreSQL/Redis health。
3. 在 knowledge 目录运行 docker compose --env-file .env up -d postgres。
4. 如果是全新业务数据库，按 db/migrations 文件名顺序执行 001 到最新 migration，并运行 node db/scripts/seed_rule_baseline.mjs。
5. 如果是已有数据迁移，优先使用 pg_dump/pg_restore；恢复后不要运行清空脚本，也不要重复导入同一批数据。
6. 如果用户提供了 Knowledge 数据库备份，恢复它；如果只有 exports/knowledge_claims_2026-07-18，则先检查目标 schema 和导入脚本，再执行一次幂等导入。

第五阶段：同步站点与产品配置
1. 检查 config/main-sites.local.json、blog-sites.local.json、wp-sites.local.json、product-assets.local.json。
2. 只有在数据库和 Docker 容器可用后，才运行现有同步脚本。
3. 注意 scripts/sync_sites_and_products.py 和 db/scripts/seed_rule_baseline.mjs 默认使用本地 Docker 容器名、数据库名、用户名和密码；如果本机配置不同，先调整环境变量/脚本参数或用等价 SQL，不要误写到错误数据库。
4. 验证 sites、products、posts 等数量，不要只看脚本退出码。

第六阶段：启动应用
1. 启动 Knowledge API：.\\knowledge\\start-backend.ps1
2. 调用 http://127.0.0.1:8010/api/v1/knowledge/health。
3. 启动主后端：.\\start-backend.bat start
4. 主前端先运行 npm --prefix frontend run build，再运行 npm --prefix frontend run dev 验证。
5. 如果需要 Knowledge 前端，再启动 knowledge/start-frontend.ps1 或使用已构建静态文件。

第七阶段：业务验收
按顺序验证：
1. 主 API 文档和健康状态。
2. Knowledge health。
3. Google GSC/GA4 数据源探活。
4. 站点连接器探活。
5. 主站页面库存和 products/collections/pages/posts 分类。
6. 本地上传 .xml、.txt、.gz；验证单文件覆盖和多文件首个覆盖、后续合并。
7. 生成主站知识画像；确认结果是 draft，不要自动批准。
8. 不要直接打开主站策略或自动发布。先报告可以生成策略的前置条件。

最终报告必须包含：
- 目标电脑和项目路径；
- Python/Node/Docker 版本；
- 哪些环境变量已配置，不能展示值；
- 主数据库和 Knowledge 数据库是否可用；
- 执行过的 migration 编号；
- 主服务、Knowledge 服务、前端地址；
- 测试命令和结果；
- 站点/产品/文章数量核对结果；
- 未完成项、阻塞项和需要用户提供的配置；
- 是否修改了代码、哪些文件被修改；
- 明确声明 AUTOMATION_ENABLED 当前值和是否执行过真实外站写入。

完成标准：
- 主服务和前端可运行；
- 数据库 schema 与当前代码匹配；
- Knowledge 服务如果启用则 health 正常；
- 关键配置没有泄露；
- 本地 Sitemap 上传覆盖可用；
- 项目测试和构建结果已报告；
- 没有执行未经用户批准的外站发布、数据库清空或自动化开启。
```
