# Exdivo Social Publisher

从 SEO Workbench 中独立出来的本地社媒发布器。它只负责：

1. 管理社媒内容包、Hubstudio 环境绑定和发布任务；
2. 打开 Hubstudio 环境并回填内容；
3. 在页面展示待发布、需处理和成功/失败状态；
4. 由用户点击一次“全部发布”后，逐项提交并验证真实帖子 URL。

支持 Reddit、Quora、X、YouTube、TikTok、Facebook 和 Instagram。发布器不需要这些平台的官方发布 API，也不保存平台账号密码。

## macOS 要求

- Apple Silicon 或 Intel Mac；
- Hubstudio macOS 客户端，建议 V3.35.0 或更高；
- Hubstudio Local API 运行在 `127.0.0.1:6873`；
- Python 3.11 或更高；
- Node.js 20 或更高；
- Docker Desktop（只用于本地 PostgreSQL）。

Hubstudio 官方提供 macOS 客户端，其 Local API 可通过 `containerCode` 打开环境并返回 Puppeteer 使用的 `debuggingPort`。

## 目录

```text
social-publisher/
├── backend/      # 独立 FastAPI 后端，只依赖 social schema
├── database/     # 自包含 PostgreSQL 表结构
├── executor/     # Puppeteer 执行器和七个平台适配器
├── extension/    # 可选的 Hubstudio 扩展、心跳和任务记录
├── frontend/     # 独立发布页面
└── scripts/      # macOS 安装、启动和媒体路径迁移
```

运行链路：

```text
发布页面
  → 本地 FastAPI
  → Hubstudio Local API（打开 containerCode）
  → Puppeteer 执行器（回填并保留页面）
  → 用户点击“全部发布”
  → 平台适配器点击并验证真实帖子 URL
  → PostgreSQL 保存结果
```

## 第一次安装

将整个 `social-publisher` 目录和需要上传的视频复制到 Mac，然后执行：

```bash
cd social-publisher
bash scripts/setup-macos.sh
```

安装脚本会：

- 创建 `.venv`；
- 安装精简后的 Python 依赖；
- 为数据库加密和执行器生成本地密钥；
- 启动 PostgreSQL；
- 初始化独立的 `social` schema；
- 安装前端和执行器依赖。

然后启动：

```bash
bash scripts/start-macos.sh
```

浏览器会打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。后端接口文档位于 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

## Hubstudio 设置

1. 在 Mac 安装并登录 Hubstudio。
2. 在 Hubstudio 中启用 Local API HTTP 模式，确认端口为 `6873`。
3. 获取 `app_id`、`app_secret` 和 `group_code`。
4. 通过后端 `/docs` 创建并测试社媒连接，或按 `MIGRATION.md` 迁移现有的 `social` 数据。
5. 确认每个绑定的 `containerCode` 与 Mac 上 Hubstudio 环境一致。

Hubstudio 环境必须使用 ChroBrowser；FireBrowser 无法提供该自动化流程需要的调试端口。

## 视频文件

数据库中的 `media[].path` 必须是 Mac 上存在的绝对路径，例如：

```text
/Users/alice/Exdivo/social-video/video-01.mp4
```

从 Windows 迁移后可批量改写路径：

```bash
.venv/bin/python scripts/rewrite_media_paths.py \
  'C:\Users\PC\Desktop\seo2.0\social-video' \
  '/Users/alice/Exdivo/social-video'
```

发布前应保证不同账号使用不同视频；发布器不会替换已经分配好的媒体文件。

## 使用流程

1. 在页面输入业务标识，例如 `exdivo`。
2. 检查 Hubstudio 环境是否在线。
3. 通过任务接口创建内容包和发布任务，或迁移现有任务。
4. 调用单个任务的 `/prepare` 接口完成回填。
5. 页面出现“可发布”后，人工检查各平台页面。
6. 点击“全部发布”。
7. 页面显示逐项成功、失败和真实帖子 URL。

最终按钮只处理状态为 `awaiting_review` 的任务，不会发布“需要处理”的项目。

## 扩展

`extension/` 可以作为未打开后端页面时的在线心跳与任务阶段记录工具。安装方式：

1. 在 Hubstudio 扩展管理中选择“加载已解压的扩展”；
2. 选择 `extension/`；
3. 在发布页面生成十分钟有效的配对码；
4. 在对应 Hubstudio 环境的扩展弹窗中输入配对码。

真实回填和最终发布由 `executor/` 完成；扩展不是第二套发布执行器。

## 安全约束

- 后端、执行器、数据库端口只绑定 `127.0.0.1`；
- 执行器请求使用独立的长随机密钥；
- Hubstudio 凭证使用 Fernet 加密后入库；
- 准备令牌有过期时间，重启执行器后需要重新回填；
- 发布结果必须通过平台域名和真实帖子 URL 校验；
- 不要把 `.env`、数据库备份或 Hubstudio 凭证提交到 Git。

## 测试

```bash
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
(cd executor && npm test)
(cd extension && node --test tests/*.test.js)
(cd frontend && npm test && npm run build)
```

## 当前限制

- 平台 DOM 或风控验证变化时仍可能要求人工处理；
- CAPTCHA、账号停用和登录失效不能自动绕过；
- “回填”与“最终发布”之间不能重启执行器；
- Hubstudio Local API 必须在与发布器相同的 Mac 上运行。
