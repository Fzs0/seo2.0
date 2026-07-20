# SEO 2.0 迁移前检查点

日期：2026-07-20
项目目录：`D:\桌面\seo2.0`
当前分支：`master`

## 1. 本次检查点范围

本次提交包含当前项目代码、前端、后端、数据库迁移、测试、Knowledge 服务和项目文档，用于后续迁移前恢复项目状态。

不纳入代码提交：

- `.codex/`：本机工作区产物；
- `exports/`：知识数据导出快照，作为独立迁移数据处理；
- `.env`、本地密钥和构建产物。

## 2. 本地运行要求

- Windows PowerShell；
- Python：`.venv\Scripts\python.exe`；
- Node.js/npm：用于 `frontend`；
- PostgreSQL：按项目环境变量连接；
- 独立 Knowledge 服务：按 `knowledge/docker-compose.yml` 和 `docs/API.md` 配置；
- 主后端：`start-backend.bat start`；
- 后端状态：`start-backend.bat status`；
- 前端构建：`npm --prefix frontend run build`。

## 3. 数据库迁移注意事项

- 迁移前备份业务数据库；
- 按 `db/migrations` 顺序执行迁移；
- 重点检查站点策略范围、站点知识、文章分析、关键词业务范围和 Knowledge Claims 相关迁移；
- `exports/knowledge_claims_2026-07-18` 不应当被当作代码自动写入生产库，需单独确认导入目标和审核状态；
- 迁移完成后核对站点、任务、文章、关键词、策略和知识数据数量。

## 4. 当前代码验证

- `tests/test_site_index_service.py`：4 passed；
- Python `compileall`：通过；
- 前端 TypeScript/Vite production build：通过；
- `git diff --check`：通过，只有既有换行格式提示；
- 后端和数据库真实迁移：本检查点未执行；
- 远端发布和跨环境验收：本检查点未执行。

## 5. 迁移后的首轮验收

1. 启动后端并确认 `/docs` 或健康检查可用；
2. 验证主站本地上传覆盖 `.xml/.txt/.gz`；
3. 在“主站内容”确认产品页、分类页、支持文章数量；
4. 生成主站知识画像并人工确认；
5. 暂不打开主站策略自动执行；
6. 先完成主站商业页面到支持文章的策略适配，再生成第一条低风险候选；
7. 人工批准一条候选，核对生成、发布回读和效果记录。

## 6. 状态边界

本文件记录的是迁移前代码检查点，不代表已部署。部署、数据库迁移、Knowledge 服务接入和真实外站发布都必须单独验收。
