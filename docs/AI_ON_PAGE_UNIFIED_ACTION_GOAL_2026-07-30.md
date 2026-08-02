# AI On-page SEO 统一 Action 接入目标

- 日期：2026-07-30
- 状态：已完成（2026-07-30）
- 优先级：P1
- 目标使用者：Codex / 后端开发 Agent
- 修改范围：后端 AI 策略正式链路、连接器能力契约、测试与技术文档
- 明确不在范围内：前端交互改造；前端仅作为只读数据看板
- 验收报告：`docs/AI_ON_PAGE_UNIFIED_ACTION_ACCEPTANCE_2026-07-30.md`

## 1. 可直接设置的 Goal

> 在不恢复任何旧 On-page 旁路写接口、不修改前端页面、不绕过统一审批与回读机制的前提下，把首页、产品页和分类页 On-page SEO 接入现有 AI 正式链路。AI 仍以 `on_page_fix` 表达编辑决策，但正式执行时必须转换为 `homepage_seo`、`product_seo`、`category_seo` 或其他已声明的具体 Action，并按照站点的 `connector_type + action_type` 选择唯一正确的 Adapter。OEMApps 应支持现有首页、产品和分类 SEO 写入；Shopify 第一阶段仅支持现有产品 SEO 写入；WordPress 博客元数据继续使用 `update_article`；Custom OpenAPI 只有在能力契约明确声明写入字段和独立回读能力时才能执行。任何未声明能力、错误站点归属、错误页面身份、缺失回读、能力变化或远端状态不确定，都必须默认 Hold/阻塞，禁止调用其他平台接口兜底。最终完成 `自主研究 → Run-local option → Formal Plan → 具体 On-page Action → preview → approve → execute → independent readback → observation → Run 收口`，并通过全量测试、PG17 隔离数据库门禁及各平台场景验收。

## 2. 业务结果

完成后，Codex 可以针对每个业务及其全部站点自主决定：

- 新写文章；
- 更新文章；
- 优化首页 SEO；
- 优化产品页 SEO；
- 优化分类页 SEO；
- Hold；
- 配置修复。

执行 On-page SEO 时必须满足：

1. AI 先诊断，再形成带证据的本轮策略。
2. 策略必须进入 Formal Plan。
3. 只有 `execute_now` 的正式策略才能创建统一 Action。
4. Action 必须绑定准确的业务、站点、连接器、页面类型和远端对象。
5. 系统必须根据站点平台选择正确写入 Adapter。
6. 写入后必须通过独立回读确认。
7. 只有回读完全匹配才创建正向效果观察。

## 3. 当前事实与缺口

### 3.1 已有能力

- 已有统一 Strategy Action 生命周期：
  `preview → approve → execute → readback → observation`。
- 已有能力快照、审批哈希、幂等、租约、异常和回读不一致保护。
- OEMApps 已有首页、产品、分类 SEO 的受保护预览与执行实现。
- Shopify 已有产品 SEO 读取、乐观并发检查、更新和回读能力。
- 文章 `new_article`、`update_article` 已接入统一 Action。

### 3.2 当前缺口

- `UNIFIED_ACTION_TYPES` 当前只允许 `new_article` 和 `update_article`。
- AI 可以生成 `on_page_fix` 决策，但正式 Run 会把它安全转为 Hold。
- 旧 `strategy_on_page_execution.py` 已删除；On-page 统一由 `StrategyActionAdapterRouter` 与平台专属 Action Adapter 执行。
- 能力契约使用 `product_seo`、`category_seo`、`homepage_seo` 等具体动作，而策略层仍可能只检查笼统的 `on_page_fix`。
- Shopify 产品 SEO 能力存在于独立路径，尚未接入统一 Strategy Action Adapter。
- Run-local option 尚未完整表达 On-page 目标身份。

## 4. 核心设计原则

### 4.1 区分“编辑决策”和“执行动作”

- 编辑决策：`on_page_fix`
- 正式执行动作：
  - 首页：`homepage_seo`
  - 产品页：`product_seo`
  - 分类/集合页：`category_seo`
  - 产品图片 Alt：如单独执行，使用 `product_image_alt`

`on_page_fix` 不直接调用远端接口。系统必须先根据目标页面转换为具体 Action。

### 4.2 平台差异隐藏在 Adapter 后面

调用方只理解统一 `ActionAdapter` Interface：

- `preview(action, patch)`
- `execute(action)`
- `recover(action)`

平台、鉴权、请求体、远端 ID、乐观锁、回读格式和副作用确认都属于 Adapter 的 Implementation。

正式 Seam 位于统一 Strategy Action 与平台 Adapter 之间。新增站点平台时，只增加：

1. 能力契约；
2. Adapter；
3. 平台验收测试。

不得修改策略主流程来硬编码某个新站点。

### 4.3 默认禁止

- 未声明能力：禁止。
- 没有独立读取能力：禁止。
- 无法确认目标对象属于当前站点：禁止。
- 远端写入结果不确定：禁止自动重试。
- 不支持的页面类型：Hold。
- 不得尝试其他平台接口作为兜底。

## 5. 平台与动作路由矩阵

| 平台/连接器 | `homepage_seo` | `product_seo` | `category_seo` | 处理原则 |
| --- | --- | --- | --- | --- |
| OEMApps | 支持 | 支持 | 支持 | 复用现有受保护写入器 |
| Shopify | 第一阶段 Hold | 支持 | 第一阶段 Hold | 仅接入当前已有且可独立回读的产品 SEO |
| WordPress / 博客站 | 不适用 | 不适用 | 不适用 | 文章 TDK 继续走 `update_article` |
| Custom OpenAPI | 按声明决定 | 按声明决定 | 按声明决定 | 必须存在明确的写入、字段白名单和回读契约 |
| 未知平台 | Hold | Hold | Hold | 不推测、不兜底 |

能力矩阵必须由当前站点和连接器配置动态生成，禁止根据域名字符串猜测。

## 6. 正式数据流

```text
Codex 当前研究
  → run-local option(action=on_page_fix)
  → 解析页面身份
  → 转换为具体 action_type
  → 检查 Site Capability Snapshot
  → Formal Plan
  → Unified Strategy Action
  → Adapter Router
  → 平台 Adapter.preview
  → 人工/策略审批
  → 平台 Adapter.execute
  → 平台 Adapter 独立 GET/readback
  → 统一字段比较
  → Observation
  → Strategy Run 收口
```

## 7. Run-local option 与目标身份

On-page 选项必须能够表达并验证：

- `site_id`
- `action = on_page_fix`
- `action_type`
- `page_type`
- `target_asset_id` 或稳定远端 ID
- `target_url`
- `connector_id`（适用时）
- `reason`
- `user_intent`
- `evidence`
- `schedule_class`
- `priority`
- `risk_level`
- `expected_fields`

最低目标身份规则：

| 页面类型 | 必需身份 |
| --- | --- |
| 首页 | `site_id + page_type=homepage + canonical host` |
| 产品页 | `site_id + product remote ID + target_url` |
| 分类页 | `site_id + collection/category remote ID + target_url` |

目标 URL、远端 ID 和本地同步对象必须属于同一个 `business_id + site_id`。

如果现有 `seo_agent.tasks` JSON 已能保存这些字段，优先复用现有结构，不新增数据库字段或 migration。只有现有结构确实无法表达正式关系时，才提出 migration，并给出不可替代的理由。

## 8. Adapter Router

新增或完善一个深 Module，负责根据 Action 和能力快照返回唯一 Adapter。

建议路由键：

```text
(connector_type, action_type)
```

第一阶段至少注册：

```text
(oemapps, homepage_seo)
(oemapps, product_seo)
(oemapps, category_seo)
(shopify, product_seo)
```

路由要求：

- 不允许模糊匹配；
- 不允许按域名猜平台；
- 不允许默认选择 OEMApps；
- 不允许未注册平台落入 Custom OpenAPI；
- 找不到 Adapter 时返回稳定 blocker，不触发远端请求；
- Adapter 选择结果及版本必须进入预览和执行审计。

## 9. Site Capability Snapshot

能力快照必须按具体 Action 声明：

- 是否可读取；
- 是否可写入；
- 是否支持独立回读；
- 可修改字段；
- 受保护字段；
- 已知副作用；
- 是否要求额外确认；
- Adapter 标识和版本；
- 检查时间；
- capability snapshot hash。

审批必须绑定：

- capability snapshot hash；
- before snapshot hash；
- patch hash；
- target identity；
- Adapter identity。

上述任一项变化后，旧审批立即失效，必须重新 Preview。

## 10. 字段白名单

第一阶段仅允许已有安全写接口支持的字段。

### 首页

- `title`（仅平台已支持时）
- `meta_title`
- `meta_description`
- `meta_keywords`

### 产品

- `meta_title`
- `meta_description`
- `meta_keywords`（仅平台支持时）
- `image_alts`（建议作为独立 Action 或明确子类型）

### 分类

- `meta_title`
- `meta_description`
- `meta_keywords`

禁止字段继续包括：

- URL、slug、handle；
- canonical；
- redirect；
- 价格；
- 库存；
- Variant；
- 分类成员关系；
- 删除；
- 未声明业务字段。

## 11. Preview

Preview 必须：

1. 重新读取当前远端对象或可信同步对象。
2. 确认业务、站点、连接器、页面和远端 ID 一致。
3. 过滤字段白名单。
4. 保存完整 before snapshot。
5. 计算 expected snapshot hash。
6. 生成 proposed patch 和 patch hash。
7. 保存模型来源；模型 ID 未暴露时使用 `not_exposed_by_runtime`，不得编造。
8. 不执行任何远端写入。

## 12. Execute

Execute 必须：

1. 验证审批、patch hash、snapshot hash、能力快照和 Adapter 身份。
2. 在 PUT/mutation 前重新读取远端状态。
3. 发现远端状态变化时阻塞并要求重新 Preview。
4. 每个 Action 只发送一次远端写入。
5. 记录 submitted patch 和 remote response。
6. 立即执行独立回读。
7. 使用统一字段规范化后比较 approved patch 与 readback。

禁止因为本地落账失败而重复远端 PUT。

## 13. 平台副作用

必须保留现有平台保护：

- OEMApps 产品更新可能重建 Variant 时，需要显式确认。
- OEMApps 分类更新可能重置成员置顶状态时，需要显式确认。
- 首页写入需要显式确认。
- Shopify 只能提交 SEO 字段 mutation，不得携带价格、库存、Variant 或其他商业字段。

AI 不得自行绕过副作用确认。

## 14. 回读、异常与恢复

### 成功

只有以下条件全部满足才完成 Action：

- 远端对象身份一致；
- 字段规范化比较一致；
- 公开 URL 属于正确站点；
- 没有未声明字段变化；
- 本地 Action、Strategy、Run 关系一致。

### 回读不一致

- 结果：`readback_mismatch`
- 创建 P1 异常；
- 不创建正向 Observation；
- Run 进入 `partial` 或 `failed`，按现有状态机决定；
- 不自动重复写入。

### 远端状态不确定

- 标记 `unknown_remote_state`；
- 只允许只读恢复；
- 精确回读确认已写入时，修复本地关系；
- 确认未写入时仍不得复用旧审批直接重写，应重新 Preview；
- 无法确认时保持阻塞。

## 15. 效果观察

回读成功后，为实际页面 URL 建立页面级观察：

- T+0：HTTP、title、description、canonical、robots、页面可访问性；
- 7 天；
- 14 天；
- 28 天；
- 56 天；
- 90 天。

观察必须保存：

- Action ID；
- Strategy Run ID；
- site ID；
- 页面类型；
- target URL；
- before/after hash；
- GSC 页面维度 baseline；
- GA4 landing-page baseline；
- 下一检查时间。

首页、产品页和分类页必须分别统计，不得只使用站点汇总指标代替页面效果。

## 16. 实施阶段

### 阶段 A：统一类型与目标身份

- 扩展 Run-local option 的 On-page 目标字段。
- 将 `on_page_fix` 转换为具体 `action_type`。
- 校验 business/site/target 归属。
- Formal Plan 保存具体 Action 身份。

### 阶段 B：Adapter Router

- 增加统一路由 Module。
- 接入 OEMApps 三类 Adapter。
- 接入 Shopify Product SEO Adapter。
- 未支持组合返回 Hold。

### 阶段 C：统一生命周期

- On-page 使用现有 Strategy Action preview/approve/execute/recover。
- 接入能力快照、哈希、幂等、租约和异常。
- 完成独立回读与统一字段比较。

### 阶段 D：效果观察与 Run 收口

- 创建页面级 Observation。
- Action 完成后自动协调父 Run。
- Hold、失败、部分成功的计数和状态准确。

### 阶段 E：清理和文档

- 不恢复旧 On-page 写路由。
- 将旧执行 Implementation 收入 Adapter 内部或删除重复代码。
- 更新 API 文档、V2 说明和项目进度。

## 17. 必须通过的验收场景

### OEMApps

1. 首页 SEO Preview、审批、执行、回读、Observation。
2. 产品 SEO Preview、审批、执行、回读、Observation。
3. 分类 SEO Preview、审批、执行、回读、Observation。
4. Variant 副作用未确认时 PUT 前阻塞。
5. 分类成员副作用未确认时 PUT 前阻塞。

### Shopify

1. 产品 SEO 使用 Shopify Adapter，不调用 OEMApps。
2. mutation 只包含 SEO 白名单字段。
3. 更新前 snapshot 已变化时阻塞。
4. 回读不一致时 P1 且无正向 Observation。
5. 首页和分类未支持时进入 Hold，远端调用次数为 0。

### WordPress

1. 博客文章 TDK 继续走 `update_article`。
2. 不创建 `product_seo`、`category_seo` 或 `homepage_seo` Action。

### Custom OpenAPI

1. 明确声明能力、字段和回读时可以创建匹配 Action。
2. 只声明读取时 Hold。
3. 未声明 Action 时 Hold。
4. 不得落入 OEMApps 或 Shopify Adapter。

### 通用安全

1. 跨业务目标拒绝。
2. 跨站目标拒绝。
3. target URL 与远端 ID 冲突时拒绝。
4. capability snapshot 变化使旧审批失效。
5. 同幂等键同请求返回原结果。
6. 同幂等键不同请求拒绝。
7. 超时后不重复写入。
8. 恢复只做读操作。
9. 回读不一致生成一次异常，不重复记两次。
10. 只有 Formal Plan 中 `execute_now` 的策略创建 Action。
11. deferred、Hold、configuration repair 不创建可执行 Action。
12. 每个站点同一时间最多一个远端写入。

## 18. PG17 正式生命周期验收

使用明确命名的临时 PostgreSQL 17 数据库完成：

```text
create Run
→ submit run-local on-page option
→ start Run
→ persist Formal Plan
→ create concrete Action
→ preview
→ approve
→ execute through fake/no-network platform Adapter
→ independent readback
→ Observation
→ parent Run completed
```

至少增加以下 PG17 用例：

- OEMApps `product_seo` 完整生命周期；
- Shopify `product_seo` 完整生命周期；
- capability change；
- readback mismatch；
- unknown remote state；
- 并发相同幂等键；
- 缺失 Adapter 默认 Hold。

PG17 用例不得连接生产数据库，也不得执行真实远端写入。

## 19. 代码质量门禁

必须完成：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\test-pg17-release-gate.ps1 -Dsn <disposable-pg17-dsn>
.\.venv\Scripts\python.exe -m py_compile <changed-python-files>
git diff --check
```

如果前端未修改，无需改变前端页面；但现有前端生产构建不得因后端类型变更而失败。

## 20. 交付物

- On-page 统一 Adapter Router；
- OEMApps On-page Adapter；
- Shopify Product SEO Adapter；
- 具体 Action 类型与能力契约；
- Run-local On-page 目标身份校验；
- Formal Plan → Action 一对一绑定；
- 回读、恢复和 Observation；
- 单元测试；
- PG17 集成测试；
- API 文档；
- V2 实现说明；
- 项目进度更新；
- 验收报告，包含测试命令、实际结果和未支持矩阵。

## 21. 非目标

- 不修改前端页面交互；
- 不恢复旧 On-page 写路由；
- 不新增无必要的数据库表或字段；
- 不实现 Shopify 首页或分类 SEO，除非开发期间发现已有可验证写入与独立回读能力；
- 不给 WordPress 增加虚构的产品/分类能力；
- 不允许 AI 修改价格、库存、Variant、slug、canonical、redirect 或成员关系；
- 不执行生产远端写入作为自动测试。

## 22. 完成定义

只有同时满足以下条件才可将 Goal 标记为完成：

1. AI 可以提交无 candidate/keyword 依赖的 On-page run-local option。
2. Formal Plan 将编辑决策转换为正确的具体 Action。
3. OEMApps 首页、产品、分类可以走统一 Action。
4. Shopify 产品 SEO 可以走统一 Action。
5. 不支持的平台/页面组合准确 Hold，且远端调用为 0。
6. 不存在跨平台错误路由或默认兜底。
7. Preview、审批、Execute、回读、异常、恢复、Observation 和 Run 收口完整。
8. 全量测试及 PG17 门禁通过。
9. 前端保持只读看板定位，不承担 AI 策略执行。
10. 没有真实生产写入、未解决 P0/P1 或不确定远端状态。
