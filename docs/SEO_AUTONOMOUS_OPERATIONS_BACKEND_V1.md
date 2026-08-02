# SEO 自主运营后端 V1 实现说明

实现基线：`a29c1ae`。本版本复用 `seo_agent.tasks` 的现有字段，不增加表、字段或索引。

## 架构

- `strategy_run_service`：统一运行身份、状态、事件、取消、重试和幂等。
- `strategy_action_service`：统一动作预览、精确审批、执行、回读和回滚门禁。
- `strategy_exception_service`：按稳定根因指纹聚合异常。
- `site_capability_service`：只读聚合站点、连接器、权限、健康和副作用。
- 旧 `strategy_hold_service` 已退役；重复 Hold 统一进入 `research_revision_required`，由 AI 在取得新证据后重新研究。历史数据库记录与迁移保留用于审计。
- 现有 `strategy_service`、文章发布器、On-page 执行器及效果服务继续作为领域适配器。

所有新增持久化记录均使用 `task_type=review`，实际类型位于
`payload.kind`：`strategy_run`、`strategy_run_event`、`strategy_action`、
`strategy_exception`、`strategy_hold_coordination`。

## 运行状态机

```text
queued
  -> discovering_sites
  -> checking_capabilities
  -> gathering_evidence
  -> ai_researching -> proposed_actions_submitted -> safety_reviewing
  -> planning
  -> zero_action_reviewing -> research_revision_required -> ai_researching
  -> awaiting_approval | executing | observing
  -> verifying
  -> completed | partial | blocked | failed | canceled
```

终态不可继续转换。状态更新使用当前状态比较，避免并发覆盖。取消是协作式取消：
只阻止尚未开始的阶段或动作，不伪造已执行动作的回滚。

## Hold 状态机

```text
完成全站决策 -> 计算 all_hold -> 获取业务锁
非 Hold: 计数归零
0 -> 1: 保存第一次 Hold
1 -> 2: 领取唯一 refresh_token，释放锁
外部只读补证 -> token 完成 -> 加载最新审计 -> 重新决策
补证后出现动作: 计数归零
补证后仍 Hold: 保持第二次 Hold
2 -> 3: 只生成一次 strategy_stagnation
```

补证期间的其他运行等待同一 token，不提前计数。token 带租约，超时后可由新
token 接管，旧 token 不可提交。

## 公共 API

### 策略运行

- `POST /api/v1/strategy-runs`
- `GET /api/v1/strategy-runs`
- `GET /api/v1/strategy-runs/{run_id}`
- `GET /api/v1/strategy-runs/{run_id}/events`
- `POST /api/v1/strategy-runs/{run_id}/start`
- `POST /api/v1/strategy-runs/{run_id}/cancel`
- `POST /api/v1/strategy-runs/{run_id}/retry`

当前创建接口只开放 `mode=dry_run`。相同业务和幂等键返回同一 `run_id`；
`start` 使用持久化 CAS 领取运行并完成全站本地规划，重复调用不会重复执行 planner。

### 策略动作

- `GET /api/v1/strategy-actions/{action_id}`
- `POST /api/v1/strategy-actions/{action_id}/preview`
- `POST /api/v1/strategy-actions/{action_id}/approve`
- `POST /api/v1/strategy-actions/{action_id}/execute`
- `POST /api/v1/strategy-actions/{action_id}/rollback-preview`
- `POST /api/v1/strategy-actions/{action_id}/rollback`

审批同时绑定 `snapshot_hash` 和 `patch_hash`。默认动作适配器为安全阻塞器；
未注入明确站点适配器时不会执行远端写入。返回语义限定为
`created|updated|already_applied|blocked|failed|readback_mismatch`。

### 站点能力

- `GET /api/v1/businesses/{business_id}/sites/capabilities`
- `GET /api/v1/sites/{site_id}/capabilities`

能力接口只读取本地持久化事实，不测试连接器、不执行同步、不发起远端请求。
未声明动作默认 `forbidden`；产品 Variant、分类成员及首页影响均显式声明。

## 安全与回滚

- URL、slug、canonical、redirect、删除、价格、库存、Variant 和分类成员变更
  不通过通用动作接口开放。
- 模型生成 patch 必须提供精确 provider/model；人工 patch 的模型字段必须为 null。
- HTTP 2xx 不等于成功；回读不一致记录 P1 异常且不创建正向观察。
- 回滚预览只读取已保存快照；真实回滚默认阻塞，必须先接入站点能力、审批、
  幂等和回读适配器。

生产开放顺序：

1. dry-run；
2. 人工审批 preview/execute；
3. 仅对明确声明的低风险动作开放自动执行；
4. 每个阶段保留一键关闭自动执行的配置；
5. 不通过回滚接口处理价格、库存、Variant、成员关系或删除操作。

## 已知限制

- 统一运行通过显式 `start` 推进；尚未接入独立常驻队列 worker。
- 统一动作默认阻塞，真实文章和 On-page adapter 需要逐站显式注入后才能写入。
- 在不增加唯一索引的约束下，幂等依赖所有调用统一经过服务层 advisory lock。
- 全局身份认证及 business scope 授权仍沿用当前应用边界；生产开放前必须补齐。
- PRD 要求的人工审批连续 7 天和低风险自动执行连续 14 天属于上线观察门禁，
  不能由本地自动化测试替代。
