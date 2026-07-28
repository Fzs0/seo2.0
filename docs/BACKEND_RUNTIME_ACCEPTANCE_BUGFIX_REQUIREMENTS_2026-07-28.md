# 后端真实运行态验收 Bug 修复需求

日期：2026-07-28
来源：后端 V2 第一轮真实运行态验收
优先级：P1
适用范围：Strategy Run、文章发布、Shopify 连接器、公开 URL 与效果观察
本轮不包含：Shopify 图片上传（ANOM-03，保留至第二轮）

## 0. 修复与复验状态

2026-07-28 本轮修复和运行态复验已完成：

- BUG-01：已修复。JSON/payload 时间在 SQL 绑定前统一转换为带时区 `datetime`；无效、缺失或 naive 时间返回稳定 `invalid_aware_timestamp`。
- BUG-02：已修复。新增事务内公开 URL 协调服务和可重复调用的站点修复接口；article、已完成 execution、已完成 publish、非 canceled effect 使用同一业务公开 URL。
- BUG-03：已修复。Shopify 查询支持 `blog_id + handle` 约束，并使用 cursor 分页直至命中目标 Blog 或完整穷尽；`blogHandle` 缺失时统一默认 `news`。
- BUG-04：已修复。连接器、publish task、execution、Strategy Action 和异常记录统一表达 `confirmed_applied`、`confirmed_absent`、`partially_applied`、`unknown_remote_state`；不确定状态阻塞执行、禁止自动重试且不重复发送 mutation。
- 数据库迁移：无。本轮复用现有字段，在 task payload/decision 中保存协调及远端状态证据。
- 全量测试：`556 passed, 13 skipped`。
- PostgreSQL 17 发布门禁：`13 passed`，新增严格时间绑定及三站四处 URL 持久化/幂等测试。

Strategy Run 正式接口复验：

| 业务 | 新 Run ID | 状态 | 发现/决策 |
|---|---|---|---|
| Exdivo | `a3aaa0e6-ba1f-495c-93c3-a3569ada1212` | `awaiting_approval` | 6 / 6 |
| Avinoti | `a060e8c5-40f8-4bdb-8895-895f34fdc8ec` | `awaiting_approval` | 1 / 1 |
| HealthyOxy | `f5088787-d162-4a80-a4d4-0d0ae7bdbad5` | `blocked` | 1 / 1 |

本地生产形态数据库 URL 协调复验：

- Exdivo：检查 11 条关联任务，不一致 0，内部域名 0。
- Avinoti：检查 14 条关联任务，不一致 0，内部域名 0。
- HealthyOxy：检查 20 条关联任务，不一致 0，内部域名 0。
- 三站修复接口第二次执行均为 `changed=0`。
- HealthyOxy 真实只读 handle 查询返回 GID `gid://shopify/Article/573682253937`、Blog `gid://shopify/Blog/91378810993`、handle `news` 和业务公开 URL。

复验期间没有批准 Action、执行文章发布或进行其他远端写入。

## 1. 背景与验收结论

后端服务、PostgreSQL 17、基础 Action 状态机、站点能力快照和部分连接器功能已经可用。

本轮已验证：

- 后端健康检查正常；
- PostgreSQL 版本为 17.10；
- PG17 发布门禁 `9 passed`；
- 全量测试 `546 passed, 9 skipped`；
- Exdivo、Avinoti、HealthyOxy 的现有远端文章均可只读回读；
- OEMApps 文章状态已在连接器边界完成归一化；
- HealthyOxy Shopify 正常 handle 查重路径可用；
- 本轮没有执行文章发布或其他远端写入。

但完整 Strategy Run 仍不能稳定运行，当前存在四个需要后端修复的问题。

## 2. BUG-01：Strategy Run 时间戳参数类型错误

### 2.1 严重级别

P1，完全阻断 Strategy Run。

### 2.2 当前问题

三个业务的 Strategy Run 通过正式 `/retry` 和 `/start` 接口重放后全部失败：

```text
asyncpg.exceptions.DataError:
expected datetime.date or datetime.datetime, got str
```

涉及位置：

- `app/services/strategy_service.py:184`
- `app/services/strategy_service.py:202`

当前参数值来自 JSON 或任务 payload，为 ISO 8601 字符串：

```text
2026-07-27T13:54:12.774115+00:00
```

当前 SQL 使用：

```sql
CAST(:source_audit_scanned_at AS timestamptz)
```

但 asyncpg 在 SQL 执行前已经要求绑定值是 Python `datetime`，所以 SQL CAST 无法修复驱动层参数类型错误。

### 2.3 期望效果

1. 从 JSON、任务 payload 或 API 获得时间值后，进入 SQL 前统一转换为带时区的 Python `datetime`。
2. 所有 Strategy Run、分析批次恢复和审计批次查询路径使用同一个时间解析函数。
3. 支持：
   - 已带时区的 `datetime`；
   - ISO 8601 字符串；
   - `Z` 结尾的 UTC 字符串。
4. 无效、缺失或无时区时间返回稳定业务错误码，不能直接暴露 DBAPIError。
5. 不依赖 SQL CAST 修复 Python 参数类型。

### 2.4 复验 Run

修复后重新 retry：

| 业务 | 当前失败 Run ID |
|---|---|
| Exdivo | `09223408-96ac-4ce3-b540-d646daddc14f` |
| Avinoti | `6fd0bc2d-adb1-4e68-8646-ac5ac1f68214` |
| HealthyOxy | `17fdd48f-8254-4144-932f-f329a89b6f95` |

### 2.5 验收标准

- 不再出现 asyncpg 时间参数错误。
- `decided_site_count` 不再为 0。
- 每个发现的站点都有以下决策之一：
  - `execute`
  - `awaiting_approval`
  - `hold`
  - `configuration_repair`
  - `failed`
- Run 至少进入 `awaiting_approval`、`blocked`、`partial` 或正常完成状态。
- 增加真实 PG17 集成测试，不能只使用 Mock Session。
- 覆盖首次运行、失败重试和从持久化 payload 恢复三个路径。

## 3. BUG-02：文章四处公开 URL 不一致

### 3.1 严重级别

P1，会导致 GSC、GA4、索引和效果观察归因错误。

### 3.2 当前问题

文章发布后，以下四处 URL 没有完全统一：

1. `seo_agent.articles.published_url`
2. execution task `target_url`
3. publish task `target_url`
4. effect task `target_url`

真实数据库核验结果：

| 业务 | Article | Execution | Publish Task | Effect | 是否一致 |
|---|---|---|---|---|---|
| Exdivo | `exdivo.com/blogs/{slug}` | 同左 | 同左 | 同左 | 是 |
| Avinoti | `/blogs/detail/{id}` | `/blogs/detail/{id}` | `/blogs/{slug}` | `/blogs/detail/{id}` | 否 |
| HealthyOxy | `healthyoxy.com` | `healthyoxy.com` | `*.myshopify.com` | `healthyoxy.com` | 否 |

现有 URL 一致性测试只多次调用同一个纯 URL 解析函数，没有经过四类数据库持久化链路，因此不能证明真实端到端一致。

### 3.3 期望效果

建立唯一的公开 URL 协调流程：

```text
远端发布或同步回读
→ 解析 canonical_public_url
→ 校验 canonical host
→ 更新 article
→ 更新 execution
→ 更新 publish task
→ 更新 effect task
```

要求：

1. Exdivo 只允许 `exdivo.com`。
2. Avinoti 只允许 `avinoti.shop`。
3. HealthyOxy 只允许 `healthyoxy.com`。
4. `jcysaas.cn`、`myshopify.com` 等内部地址只能保存在：
   - `remote_detail_url`；
   - connector raw response；
   - decision/evidence 审计字段。
5. 内部地址不能写入效果观察的 `target_url`。
6. post sync 发现 canonical URL变化时，也必须协调相关记录。
7. 公开 URL 无法确认或 host 不在站点白名单时：
   - Action 转为 `configuration_repair` 或 Hold；
   - 创建 P1 异常；
   - 不创建正向效果观察。

### 3.4 验收标准

增加真实 PostgreSQL 17 持久化测试：

1. 插入 article、execution、publish task、effect task。
2. 经过实际发布服务或同步服务。
3. 断言四处公开 URL 完全相同。
4. 覆盖：
   - Exdivo OEMApps；
   - Avinoti OEMApps；
   - HealthyOxy Shopify。
5. 覆盖 OEMApps detail URL 到 slug canonical URL 的转换。
6. 覆盖 Shopify `myshopify.com` 到业务公开域名的转换。
7. 为现有 Avinoti、HealthyOxy 不一致记录提供幂等修复任务，不使用一次性人工 SQL。

## 4. BUG-03：Shopify 查重可能漏掉目标 Blog

### 4.1 严重级别

P1，存在重复发布风险。

### 4.2 当前问题

涉及位置：

```text
app/clients/publishers.py:998
```

当前 Shopify 查询方式：

```graphql
articles(first: 2, query: "handle:...")
```

查询完成后，再在本地按 `blog handle` 过滤。

如果多个 Blog 存在相同 article handle，而目标 Blog 对应文章没有出现在前两条结果中，系统会误判目标文章不存在，并可能再次发送创建请求。

### 4.3 期望效果

Shopify 文章唯一身份必须是：

```text
shop + blog_id/blog_handle + article_handle
```

要求：

1. 优先在 Shopify 查询中同时约束 article handle 和目标 Blog。
2. 如果 Shopify 查询语法不能直接限定 Blog，则必须分页，直到：
   - 找到目标 Blog 中的文章；或
   - 完整确认结果集中不存在目标文章。
3. 不允许固定查询两条后直接判断不存在。
4. `blogHandle` 未显式配置时，查重、发布和公开 URL生成必须使用相同默认值，例如 `news`。
5. 查重结果必须返回：
   - Shopify Article GID；
   - article handle；
   - blog ID；
   - blog handle；
   - `isPublished`；
   - 业务公开 URL。

### 4.4 验收标准

覆盖以下场景：

1. 一个 Blog 中存在目标 handle。
2. 多个 Blog 中存在相同 handle。
3. 目标 Blog 的文章排在查询结果第三条以后。
4. 目标 Blog 中不存在文章。
5. `blogHandle` 显式配置。
6. `blogHandle` 缺失，使用统一默认值。
7. 相同幂等键重复调用不创建第二篇。
8. HealthyOxy 真实只读查询仍能返回已有文章 GID。

## 5. BUG-04：Shopify 超时恢复未区分未知远端状态

### 5.1 严重级别

P1，存在盲目重试和重复发布风险。

### 5.2 当前问题

当前实现已经做到：

- 创建 mutation 设置为单次发送；
- 创建异常后按 handle 执行远端只读回读。

但异常后的状态分类不完整。以下情况可能都变成普通失败：

- 查询成功且结果为空；
- 查询接口失败；
- 查询超时；
- 权限不足；
- Shopify 暂时不可用；
- 找到同 handle 文章，但 Blog 或字段不一致。

系统无法可靠判断远端到底有没有创建成功。

### 5.3 期望效果

创建异常后必须返回以下稳定状态之一：

| 状态 | 含义 | 系统行为 |
|---|---|---|
| `confirmed_applied` | 回读找到目标 Blog 中的目标文章 | 恢复本地成功 |
| `confirmed_absent` | 查询成功并完整确认不存在 | 允许新执行 Token 重试 |
| `partially_applied` | 找到文章但关键字段不完整或不一致 | P1，禁止自动重试 |
| `unknown_remote_state` | 回读失败、超时、权限不足或无法确认 | P1，禁止自动重试 |

要求：

1. `unknown_remote_state` 写入：
   - Strategy Action；
   - publish task；
   - Strategy Run；
   - 异常记录。
2. 不允许把“查询失败”解释为“文章不存在”。
3. 只有 `confirmed_absent` 才能再次发送创建 mutation。
4. `confirmed_applied` 必须保存远端 GID、公开 URL 和回读证据。
5. `partially_applied` 和 `unknown_remote_state` 不创建正向观察。
6. 后续人工恢复也必须先重新回读。

### 5.4 验收标准

测试以下场景：

1. mutation 成功，但客户端收到超时。
2. mutation 超时，回读找到目标文章。
3. mutation 超时，回读成功并确认目标文章不存在。
4. mutation 超时，回读再次超时。
5. mutation 超时，回读权限失败。
6. 回读找到文章但 Blog 不匹配。
7. 回读找到文章但标题或关键字段不匹配。
8. 所有不确定状态均不会发送第二次创建 mutation。
9. 相同幂等键恢复后返回相同 GID。

## 6. 修复后的目标流程

后端修复完成后，完整执行流程应为：

```text
创建或重试 Strategy Run
→ 枚举业务下全部启用站点
→ 获取能力快照和数据证据
→ 为每个站点生成明确决策
→ 创建统一 Strategy Action
→ 人工审批
→ 幂等执行
→ 远端回读和不确定状态分类
→ 生成并校验公开 canonical URL
→ 协调四处数据库 URL
→ 创建效果观察
→ 输出完整 Run 结果
```

## 7. 总体验收标准

本轮修复必须达到：

- Exdivo、Avinoti、HealthyOxy Strategy Run 均能越过 planning。
- 每个发现的站点都有明确决策，不允许静默遗漏。
- 任何网络超时都不会导致重复文章。
- 任何内部域名都不会进入效果追踪。
- article、execution、publish task、effect task 的公开 URL 完全一致。
- 不确定远端状态默认停止，不盲目重试。
- 单站失败不阻断其他站点落下独立结果。
- 全量测试继续通过。
- PG17 发布门禁继续通过。
- 增加真实 PostgreSQL 17 集成测试证据。
- 复验期间保持远端写入为 0，除非另行批准使用测试店。

## 8. 后端交付物

后端提交复验时应提供：

1. 修改文件列表。
2. 数据库迁移说明；如果没有迁移，明确写明。
3. 新增和修改的测试列表。
4. 全量测试结果。
5. PG17 发布门禁结果。
6. 三个 Strategy Run 的重放结果。
7. 三站四处 URL 的数据库查询证据。
8. Shopify 超时恢复状态分类测试证据。
9. 已知限制。
10. 未执行真实远端写入的声明。

## 9. 第二批根因修复与运行态复验（2026-07-28）

已完成：

- `google_sync_log.trigger` 统一改为 `text`，CHECK 同时支持
  `manual / scheduled / retry / strategy_hold_refresh`；新增幂等升级迁移
  `033_google_sync_trigger_contract.sql`，并同步更新空库迁移 005。
- GSC 与 GA4 每个来源使用独立数据库事务；来源失败会保留首错，不再污染调用
  Session 或阻断后续来源、站点。
- 建立唯一 RemoteOutcome policy。`unknown_remote_state`、
  `partially_applied`、`identity_conflict` 统一映射为 blocked、P1、禁止自动
  重试、禁止正向观察。
- Shopify 回读按 blog GID、blog handle、article handle 三元身份校验；冲突
  稳定返回 `SHOPIFY_ARTICLE_IDENTITY_CONFLICT`。
- PG17 故障注入已证明 Shopify 创建超时且回读不确定时，Publish Task、
  Execution、Strategy Action、Strategy Run 与 P1 Exception 完整关联；再次
  发布在本地审批链处被拒绝，mutation 计数保持 1。
- PG17 发布/同步入口测试已覆盖 Exdivo、Avinoti、HealthyOxy 的 Article、
  Execution、Publish Task、Effect Task 四处公开 URL 一致。
- 首次 Run 失败后通过 retry 从数据库 payload 恢复 root run、attempt、业务、
  scope、mode、预算、配额和审批策略。

验证结果：

```text
全量测试：568 passed, 16 skipped
PG17 发布门禁：14 passed
后端：http://127.0.0.1:8000
PG17：PostgreSQL 17，google_sync_log.trigger=text
source_drift=false
```

真实 Strategy Run：

| 业务 | Run ID | 状态 | 说明 |
|---|---|---|---|
| Exdivo | `ea2d267c-b6f7-4271-abbb-90bd2b9ec141` | `awaiting_approval` | 6/6 站点有决策 |
| Avinoti | `2806325c-1e30-4757-900a-be98fafff9c3` | `awaiting_approval` | 1/1 站点有决策 |
| HealthyOxy | `a12a696e-80d3-4f1b-b106-55288806fe96` | `blocked` | 事务污染已消失；GSC、GA4 刷新成功，但 Shopify connection 仅有 `write_content`，缺少产品读取权限，`product_facts` 与 `authoritative_sources` 安全门禁仍阻止审批 |

HealthyOxy 的刷新恢复还暴露并修复了两个真实运行缺陷：

- 失败/过期的 Hold refresh 此前只允许在 1→2 边沿领取，计数超过 2 后永远
  无法重试；现在下一次确认 Hold 可领取新 token。
- supersede 查询同一 asyncpg 参数同时用于 UUID 与 text 比较，已对 text 一侧
  显式转换。

本轮没有执行任何真实远端写入。HealthyOxy 仍需为现有 Shopify connection
增加产品只读权限并补齐权威来源证据，才能在不绕过安全门禁的前提下达到
`awaiting_approval`。
