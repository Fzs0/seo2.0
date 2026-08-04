# AI URL/主题级冷却与整站 Hold 契约

日期：2026-08-01

## 目标流程

```text
某个 URL 或主题正在冷却
→ 只排除这个具体目标
→ 继续研究同站其他现有页面和新主题
→ 逐项记录具体机会、证据、结果和原因
→ 仍有合格机会时必须进入 Proposed Action
→ 所有具体机会均不合格后，才允许整站 Hold
```

## Research Portfolio 必填内容

每个 `material_options` 项必须是具体机会，不能只写“新文章”“更新文章”或
“On-page”这种动作类别。每项必须包含：

- `option_id`；
- `action`：`new_article`、`update_article` 或 `on_page_fix`；
- `target_identity`：已有页面必须有 URL/远程 ID/本地 ID，新主题必须有
  `intent_key + topic_cluster`；
- `outcome`：`qualified`、`rejected` 或 `blocked`；
- `reason` 与 `evidence_refs`；
- 冷却或活动观察阻塞时的 `blocker_code` 和 `block_scope`。

`block_scope` 只能是 `url` 或 `topic`。不得用某个目标的冷却推导出整站冷却。

每站还必须提交 `opportunity_exhaustion`：

- `surfaces_checked` 至少覆盖 `existing_articles`、`new_topics`、
  `product_pages`、`category_pages` 与 `on_page`；
- `evaluated_option_ids` 必须与该站全部具体机会 ID 完全一致；
- `conclusion` 说明为何已经完成本轮机会穷尽。

## Hold 门禁

后端按站点独立审查 Hold，即使同一组合中的其他站点已有 `execute_now` 或
`deferred`，也不能跳过 Hold 站点。

以下任一情况返回 `research_revision_required`：

- 只有泛化动作类别，没有具体 URL 或主题；
- 未研究现有文章、新主题、产品页、分类页和 On-page；
- 有机会未被逐项评估；
- 仍有 `qualified` 机会却提交整站 Hold；
- 把 URL/主题冷却声明成站点级阻塞。

即使所有已列机会均为 `rejected` 或合法的目标级 `blocked`，只要不存在有效的
整站硬阻塞，也必须返回 `SAFE_EXPERIMENT_REQUIRED`，继续寻找安全学习动作。
目标级阻塞不得升级为整站 Hold。

## 稳定错误码

- `CONCRETE_OPPORTUNITY_REQUIRED`
- `CONCRETE_OPPORTUNITY_EXHAUSTION_REQUIRED`
- `QUALIFIED_OPPORTUNITY_NOT_SCHEDULED`
- `COOLDOWN_SCOPE_OVERBROAD`
- `SAFE_EXPERIMENT_REQUIRED`
- `SITE_HARD_BLOCKER_INVALID`

## 兼容性

本契约不新增数据库表或字段；Research Portfolio 继续存储在现有 Strategy Run
JSON payload 中。契约版本升级为 `ai-led-strategy-v2`，旧审批与旧研究快照不得直接
复用于新计划。
