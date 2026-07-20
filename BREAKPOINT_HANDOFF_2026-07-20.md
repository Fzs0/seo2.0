# SEO Workbench 断点交接（2026-07-20）

下一个窗口先读：

1. `PROJECT_PROGRESS.md`
2. `docs/STRATEGY_POLICY_V1_DRAFT.md`
3. `docs/MAIN_SITE_ECOMMERCE_SEO_V1.md`

## 当前主线

主站 `exdivo` 不直接套用博客站的普通策略。主站的产品页、分类页负责商业搜索意图，博客文章负责购买前教育，并链接回一个明确的产品页或分类页。

## 已完成

- Strategy Builder 专用导入、页面簇校验、按簇 AI 分站和库存关联已完成本地验证。
- 真实 Strategy Builder 数据最近一次只读状态：`keywords=1,274`、`tasks=224`、`articles=0`、`serp_snapshots=10`、`posts=135`、`post_analyses=292`。
- 主站四类索引 `products / collections / pages / posts` 支持一次选择、合并和 URL 去重；页面读取失败暂不阻塞索引库存。
- 新增“主站内容”只读页面和 `/api/v1/sites/{site_id}/main-content` 接口，按 URL 分为产品页、分类页、支持文章和其他页面。
- 最新项目根目录测试 `223 passed`；前端 build、Python compileall、git diff 检查通过。
- 未启动、停止或重启后端；最新代码尚未部署到当前后端进程。

## 下一步唯一推荐动作

用户手动通过根目录 `start-backend.bat` 重启后，打开左侧“主站内容”验收 `exdivo`：

1. 产品页数量；
2. 分类页数量；
3. 支持文章数量；
4. 四类索引文件是否全部显示；
5. 页面职责是否正确。

确认后再实现：

```text
GSC/SERP 查询
→ 归属到产品页/分类页
→ 识别内容缺口
→ 创建主站内容候选
→ 绑定 conversion_target_url
→ 复用 Brief/大纲/文章/QA 生成草稿
→ 人工审核后发布
```

## 明确禁止

- 不要打开主站 `strategy_enabled` 让它混入博客站策略。
- 不要恢复旧的直接关键词生文接口。
- 不要先批量修复 80 个页面的 title、description、H1。
- 不要启动、停止或重启后端；如需加载最新代码，只能由用户运行根目录 `start-backend.bat`。
- 暂时不要扫描或修改 `knowledge/`。
