# Codex SEO 自主运营后端需求文档 V1

## 1. 文档信息

- 文档状态：待开发
- 目标版本：V1
- 主要使用方：Codex SEO 运营 Agent
- 适用范围：全部业务及业务下所有已接入站点
- 编写日期：2026-07-27
- 现有实现基线：`a29c1ae`
- 需求优先级：P0，上线阻塞

## 2. 背景

平台已经具备站点、关键词、GSC、GA4、SERP、内容审计、文章生成与发布、商品和分类 SEO 更新、图片上传、策略生成、审批、执行及效果观察等基础能力。

目前 Codex 可以在人工协助下调用这些能力，但还不能安全地将“执行今日策略”作为一个长期稳定、可重复、可审计的统一操作。主要缺口包括：

1. 第二次连续全站 Hold 的补证触发语义不准确；
2. PostgreSQL 迁移及并发门禁缺少真实环境验收；
3. 缺少面向 Agent 的统一策略运行入口和稳定状态机；
4. 写操作的幂等、回读、异常、观察和回滚尚未形成统一契约；
5. Agent 无法通过一个接口完整获知新站点的读取、写入、风险及约束能力。

本需求的目标是建立一套供 Codex 使用的受控自主运营后端，使 Codex 能够发现所有业务站点、采集证据、制定策略、执行允许的动作、验证结果、记录异常并进入效果观察。

## 3. 产品目标

用户下达“执行今日策略”后，Codex 只需调用一个统一入口，即可完成：

```text
发现业务及全部站点
→ 检查站点能力和连接状态
→ 获取或刷新证据
→ 为每个站点生成决策
→ 分配每日执行预算
→ 自动执行低风险动作
→ 将高风险动作送审
→ 回读验证远端结果
→ 记录日志与异常
→ 加入效果观察
→ 返回本轮交付清单
```

### 3.1 成功标准

- 单次策略运行有唯一 `run_id`，全链路可查询；
- 同一幂等键重复调用不会重复发布或重复修改；
- 每个被发现的有效站点都有一个明确结果：执行、待审批、Hold、配置修复或失败；
- 所有写操作均保存执行前快照、提交内容、远端响应和回读结果；
- 第二次连续全站 Hold 才触发一次补证，并基于补证结果重新决策；
- 所有已执行动作自动进入效果观察；
- Agent 可以通过站点能力接口判断可读、可写、需审批和禁止操作；
- 生产异常能够被机器读取，并给出解锁条件和重试信息。

## 4. 非目标

本版本不包括：

- 自动删除文章、产品、分类或站点；
- 自动修改价格、库存、Variant、分类成员关系；
- 自动修改 URL、slug、canonical 或重定向；
- 自动绕过审批、内容质量或高风险门禁；
- 保存或输出模型私有思维链；
- 为满足本需求而重复建设现有站点、文章、任务或效果观察数据结构；
- 未经明确评审增加新的业务表。

## 5. 设计原则

1. **Agent 优先**：接口必须返回结构化状态、错误和下一步操作，不依赖解析自然语言。
2. **复用现有结构**：优先使用现有 `seo_agent.tasks` 的 `payload`、`decision`、`logs`、状态和关联字段。
3. **证据先于决策**：策略必须引用不可变的证据快照，不能使用来源不明的实时值。
4. **写后必读**：远端 API 返回成功不等于执行成功，必须回读验证。
5. **默认安全**：能力未知、证据过期、归属不明或回读不一致时停止执行。
6. **幂等执行**：重试、超时和进程重启不能产生重复内容或重复更新。
7. **完整审计**：记录可审计的决策依据，不记录模型私有思维链。
8. **逐步放权**：先 dry-run，再审批执行，最终才允许低风险动作自动执行。

## 6. 术语

- 业务：一个品牌或运营主体，如 `exdivo`。
- 站点：业务下的主站、品牌博客、垂直博客或其他流量站。
- 策略运行：从发现站点到产生最终执行及观察结果的一次完整流程。
- 全站 Hold：本轮业务范围内所有有效站点均没有可安全执行的动作。
- 补证：刷新 GSC、GA4、SERP、产品、分类、文章及 On-page 等证据。
- 能力清单：某站点支持的读取、写入、审批、风险和副作用声明。
- 回读：写操作后再次读取远端资源并校验实际状态。
- 结构性零基线：新 URL 发布前不存在时记录的零值，不是发布后实测零值。

---

# 7. 功能需求一：修正第二次连续 Hold 状态机

## 7.1 目标

补证只能由“已确认的第二次连续全站 Hold”触发，不能根据策略运行次数、并发请求数量或预估序号提前触发。

## 7.2 正确流程

```text
开始策略运行
→ 使用当前证据完成全部站点决策
→ 计算 all_hold
→ 在同一业务锁内读取并更新权威 Hold 状态
→ 非 all_hold：连续次数归零
→ all_hold 且次数 0→1：保存第一次 Hold，不补证
→ all_hold 且次数 1→2：领取唯一 refresh_token
→ 释放数据库锁
→ 执行外部补证
→ 再次获取锁并完成 refresh_token
→ 加载最新审计批次
→ 基于新证据重新生成候选和决策
→ 保存最终分析、计划及站点覆盖矩阵
→ all_hold 且次数 2→3：生成一次 strategy_stagnation
```

## 7.3 状态要求

权威 Hold 状态至少包含：

```json
{
  "business_id": "...",
  "business_consecutive_hold_count": 2,
  "site_consecutive_hold_counts": {
    "site_id": 2
  },
  "registered_run_ids": [],
  "refresh": {
    "status": "pending|completed|failed",
    "refresh_token": "...",
    "trigger_hold_transition": "1_to_2",
    "claimed_by_run_id": "...",
    "claimed_at": "...",
    "expires_at": "...",
    "completed_at": "...",
    "degraded": false,
    "results": []
  },
  "stagnation_emitted_for_counts": []
}
```

## 7.4 并发与恢复

- 同一 `business_id` 的 Hold 状态转换必须使用事务锁或业务 advisory lock；
- `run_id` 必须幂等，同一运行重复完成不能重复计数；
- `refresh_token` 只能被一个运行领取并完成一次；
- 外部补证期间不得长期持有数据库事务锁；
- Token 超时后允许安全接管，旧 Token 必须失效；
- 等待其他运行补证时必须有超时及明确错误；
- 补证失败必须记录所有数据源的状态、错误和决策影响；
- 补证失败不得绕过质量、安全或高风险门禁。

## 7.5 补证范围

补证至少覆盖：

- GSC；
- GA4；
- 实时 SERP；
- 产品；
- 分类；
- 文章；
- 首页、产品页、分类页 On-page 审计；
- 发布频率；
- 内容缺口；
- 数据源连接状态。

## 7.6 验收用例

1. 第一次连续全站 Hold 不刷新；
2. 第二次连续全站 Hold只刷新一次；
3. 第二次运行存在可执行动作时不刷新且 Hold 次数归零；
4. 两个并发运行不能把“运行序号二”误判为“第二次 Hold”；
5. 并发情况下只有一个 Token 拥有者；
6. Token 超时后新 Token 可接管，旧 Token 无法提交结果；
7. 补证后策略引用最新 `content_audit_batch`；
8. 补证后必须重新决策，不能沿用旧候选；
9. 第三次连续 Hold 只生成一条 `strategy_stagnation`；
10. 非 Hold 运行后，新一轮 Hold 从 1 重新计数。

---

# 8. 功能需求二：真实 PostgreSQL 验收环境

## 8.1 目标

为迁移、并发协调和审批门禁提供可重复的真实 PostgreSQL 集成测试，不能用 Mock 结果代替上线验收。

## 8.2 测试矩阵

必须分别在 PostgreSQL 14、15、16 的一次性数据库中运行：

- `031_invalidate_late_strategy_effect_baselines.sql`；
- `032_classify_legacy_new_article_zero_baselines.sql`；
- Hold 并发状态机；
- 最新审计批次审批门禁；
- 任务删除与备份保留；
- 重复迁移和重复执行。

## 8.3 环境要求

- 测试数据库名称必须包含 `test`、`temp` 或 `tmp`；
- 测试开始前创建隔离 schema，结束后销毁；
- 严禁连接开发和生产数据库；
- CI 通过矩阵环境变量注入 DSN；
- 日志不得打印完整 DSN、密码或 Token；
- 任何版本未运行都不能将迁移标记为已验收。

## 8.4 数据断言

迁移测试至少覆盖：

- `null` 时间；
- 空字符串时间；
- 非法时间；
- 越界时间；
- 正常时间；
- 发布后采集的无效旧文基线；
- 新文章结构性零基线；
- 原始 payload 和 decision 备份；
- 迁移重复执行不重复备份、不重复修改；
- 删除原任务不受备份表阻止；
- 非目标数据保持不变。

## 8.5 CI 输出

CI 必须输出结构化验收摘要：

```json
{
  "postgresql_versions": {
    "14": "passed",
    "15": "passed",
    "16": "passed"
  },
  "migration_031": "passed",
  "migration_032": "passed",
  "hold_concurrency": "passed",
  "audit_approval_gate": "passed"
}
```

## 8.6 验收标准

- PG14、PG15、PG16 均有真实通过记录；
- 所有迁移在每个版本连续执行两次；
- Hold 测试使用多个真实数据库连接并发运行；
- 审批门禁证明旧审计策略失败、最新审计策略通过；
- CI 缺少任何 DSN 时应明确失败或将发布门禁标记为不通过，不能静默跳过后继续部署。

---

# 9. 功能需求三：统一策略运行接口和状态机

## 9.1 目标

提供一个面向 Codex 的统一编排入口。Codex 不需要了解内部服务调用顺序，也不需要直接拼装多个底层接口。

## 9.2 创建策略运行

```http
POST /api/v1/strategy-runs
```

请求：

```json
{
  "business_id": "exdivo",
  "site_ids": null,
  "scope": "all_sites",
  "mode": "dry_run|preview|execute|observe",
  "requested_by": "codex",
  "idempotency_key": "2026-07-27-exdivo-daily",
  "action_budget": 4,
  "site_quotas": {},
  "approval_policy": "use_site_capabilities"
}
```

响应：

```json
{
  "ok": true,
  "run_id": "...",
  "status": "queued",
  "business_id": "exdivo",
  "mode": "execute",
  "idempotency_key": "...",
  "created_at": "...",
  "next_action": "poll"
}
```

## 9.3 查询及控制接口

```http
GET  /api/v1/strategy-runs/{run_id}
GET  /api/v1/strategy-runs/{run_id}/events
POST /api/v1/strategy-runs/{run_id}/cancel
POST /api/v1/strategy-runs/{run_id}/retry
```

可选列表接口：

```http
GET /api/v1/strategy-runs?business_id=...&status=...&from=...&to=...
```

## 9.4 固定状态机

运行状态只能使用以下枚举：

```text
queued
discovering_sites
checking_capabilities
gathering_evidence
planning
refreshing_evidence
replanning
awaiting_approval
executing
verifying
observing
completed
partial
blocked
failed
canceled
```

终态：

- `completed`：所有计划动作达到预期终态；
- `partial`：部分动作成功，部分失败或阻塞；
- `blocked`：无执行错误，但缺少配置、证据或审批；
- `failed`：编排或执行发生不可接受错误；
- `canceled`：收到取消请求且停止后续动作。

## 9.5 运行结果

查询结果至少返回：

```json
{
  "run_id": "...",
  "status": "partial",
  "current_stage": "verifying",
  "business_id": "...",
  "discovered_site_count": 5,
  "decided_site_count": 5,
  "evidence_snapshot_id": "...",
  "source_audit_batch_id": "...",
  "analysis_batch_id": "...",
  "plan_id": "...",
  "counts": {
    "executed": 2,
    "awaiting_approval": 1,
    "hold": 1,
    "configuration_repair": 1,
    "failed": 0
  },
  "site_results": [],
  "exceptions": [],
  "observation_ids": [],
  "started_at": "...",
  "finished_at": "...",
  "next_action": "approve|retry|observe|none"
}
```

## 9.6 站点覆盖要求

- 默认发现该业务下所有有效站点，包括主站和全部博客站；
- 每个发现站点必须产生一条最终决策；
- 不允许因为某站无候选而从覆盖矩阵中消失；
- 站点结果必须是执行、待审批、Hold、配置修复或失败之一；
- `discovered_site_count` 必须等于 `decided_site_count`，否则运行不得标记完成。

## 9.7 事件流

事件至少包含：

```json
{
  "event_id": "...",
  "run_id": "...",
  "stage": "gathering_evidence",
  "event_type": "stage_started|stage_completed|warning|error|action",
  "site_id": null,
  "message": "GSC refresh completed",
  "data": {},
  "created_at": "..."
}
```

不得在事件中写入密钥、完整授权头或模型私有思维链。

## 9.8 幂等要求

- 同一业务、同一 `idempotency_key` 只能创建一个有效运行；
- 重复请求返回原 `run_id` 和当前状态；
- 已完成运行不能通过相同键重新执行；
- `retry` 创建新的 attempt，但保留原 `run_id` 的关联关系；
- 每个子动作必须派生稳定的子幂等键。

---

# 10. 功能需求四：统一执行安全闭环

## 10.1 目标

所有文章发布和 On-page 更新遵守同一套“预览、审批、执行、回读、异常、观察、回滚”契约。

## 10.2 动作类型

至少支持：

- `new_article`；
- `update_article`；
- `homepage_seo`；
- `product_seo`；
- `category_seo`；
- `product_image_alt`；
- `hold`；
- `configuration_repair`；
- `observe_only`。

## 10.3 统一动作记录

每个动作至少包含：

```json
{
  "action_id": "...",
  "run_id": "...",
  "business_id": "...",
  "site_id": "...",
  "action_type": "product_seo",
  "target_url": "...",
  "target_asset_id": "...",
  "status": "awaiting_approval",
  "idempotency_key": "...",
  "risk_level": "low|medium|high|forbidden",
  "approval_requirement": "auto|approval_required|forbidden",
  "evidence_snapshot_id": "...",
  "strategy_fingerprint": "...",
  "before_snapshot": {},
  "proposed_patch": {},
  "submitted_patch": null,
  "remote_response": null,
  "readback": null,
  "rollback_snapshot": {},
  "observation_id": null
}
```

## 10.4 写操作流程

```text
校验站点能力
→ 校验目标 URL 归属
→ 读取远端当前快照
→ 生成 snapshot_hash
→ 生成 proposed_patch
→ 执行风险与审批门禁
→ 使用 idempotency_key 写入
→ 保存远端响应
→ 重新读取远端资源
→ 对比 submitted_patch 与 readback
→ 一致则 completed
→ 不一致则 failed/readback_mismatch
→ 创建效果观察
```

## 10.5 统一返回语义

写接口必须明确返回：

```text
created
updated
already_applied
blocked
failed
readback_mismatch
```

不得只根据 HTTP 2xx 标记成功。

## 10.6 执行前快照和回滚

必须保存：

- title；
- description；
- keywords；
- ALT；
- 正文；
- 图片引用；
- canonical 相关字段；
- 远端版本号或更新时间；
- snapshot hash。

回滚要求：

- 支持生成回滚预览；
- 回滚也是一次受控写操作；
- 回滚必须经过站点能力和审批门禁；
- 回滚后必须回读；
- 禁止使用回滚绕过禁止操作。

建议接口：

```http
POST /api/v1/strategy-actions/{action_id}/rollback-preview
POST /api/v1/strategy-actions/{action_id}/rollback
```

## 10.7 模型与人工来源

模型生成内容必须记录：

- `generation_mode=model`；
- `generation_provider`；
- 精确 `generation_model`；
- `generation_run_id`；
- 可审计的提示词摘要或生成任务引用。

人工内容必须记录：

- `generation_mode=manual`；
- provider、model 和 generation_run_id 为 `null`。

禁止伪造、猜测或使用模糊模型名。

## 10.8 异常中心

统一异常类型：

```text
configuration_error
connector_error
data_quality_error
strategy_stagnation
generation_error
image_upload_error
publish_error
readback_mismatch
tracking_error
migration_error
capability_mismatch
```

异常记录至少包含：

```json
{
  "exception_id": "...",
  "run_id": "...",
  "action_id": "...",
  "business_id": "...",
  "site_id": "...",
  "type": "readback_mismatch",
  "stage": "verifying",
  "severity": "P1",
  "summary": "...",
  "raw_error": "...",
  "remote_write_occurred": true,
  "retryable": false,
  "responsibility_type": "connector_owner",
  "unlock_condition": "...",
  "first_seen_at": "...",
  "last_seen_at": "...",
  "occurrence_count": 1
}
```

同一根因应聚合重复次数，不应无限创建重复异常。

## 10.9 效果观察

所有已完成动作自动进入观察：

- 新文章：结构性零基线；
- 更新文章及 On-page：发布前真实基线；
- 观察窗口：7、14、28、56 天；
- GSC 使用规范化 URL 页面级数据；
- GA4 使用落地页数据；
- 结论枚举：`positive|negative|neutral|inconclusive`；
- 记录执行模型、策略、主题、目标 URL 和证据快照；
- 回读失败或基线无效时不得生成虚假效果结论。

## 10.10 自动执行权限

权限应按业务、站点和动作类型配置：

```json
{
  "new_article": "auto",
  "update_article": "auto",
  "product_meta": "approval_required",
  "product_alt": "auto",
  "category_meta": "approval_required",
  "homepage_seo": "approval_required",
  "redirect": "forbidden",
  "delete_content": "forbidden"
}
```

默认规则：

- 未声明能力：禁止自动执行；
- URL、slug、canonical、redirect：必须审批或禁止；
- 删除、价格、库存、Variant、分类关系：禁止；
- 受监管/YMYL内容：必须通过权威来源门禁；
- 首页大幅修改：必须审批。

---

# 11. 功能需求五：站点能力清单

## 11.1 目标

Codex 能在不阅读后端代码的情况下，判断一个新接入站点支持哪些数据、动作、审批和副作用。

## 11.2 接口

```http
GET /api/v1/businesses/{business_id}/sites/capabilities
GET /api/v1/sites/{site_id}/capabilities
```

## 11.3 返回结构

```json
{
  "business_id": "exdivo",
  "generated_at": "...",
  "sites": [
    {
      "site_id": "...",
      "site_name": "...",
      "site_role": "main|brand_blog|vertical_blog|traffic_site",
      "status": "active",
      "strategy_enabled": true,
      "domain": "https://example.com",
      "canonical_hosts": ["example.com"],
      "market": "US",
      "language_code": "en",
      "content_scope": [],
      "connectors": {
        "products": {"read": true, "write": true, "last_success_at": "..."},
        "collections": {"read": true, "write": true, "last_success_at": "..."},
        "articles": {"read": true, "write": true, "last_success_at": "..."},
        "homepage": {"read": true, "write": true, "last_success_at": "..."},
        "images": {"upload": true, "last_success_at": "..."},
        "gsc": {"read": true, "last_success_at": "..."},
        "ga4": {"read": true, "last_success_at": "..."},
        "serp": {"read": true, "last_success_at": "..."}
      },
      "supported_actions": {},
      "supported_fields": {},
      "side_effects": {},
      "approval_policy": {},
      "publishing": {},
      "data_freshness": {},
      "configuration_issues": []
    }
  ]
}
```

## 11.4 必须声明的能力

每个站点必须明确：

- 站点角色和内容定位；
- 允许及禁止的主题范围；
- 产品、分类、文章、首页是否可读、可写；
- 图片是否可上传；
- 支持哪些 SEO 字段；
- 文章发布、更新、删除能力；
- 公开 URL 和 canonical URL 规则；
- 允许的 canonical host；
- 写产品 SEO 是否会重建 Variant；
- 写分类 SEO 是否会重置成员关系或排序；
- 写首页 SEO 的影响范围；
- GSC、GA4、SERP连接状态；
- 最近一次成功同步时间；
- 数据过期阈值；
- 自动执行、需审批和禁止操作；
- 当前缺失配置和解锁条件。

## 11.5 副作用契约

例如：

```json
{
  "product_seo": {
    "variant_recreation_possible": true,
    "requires_variant_confirmation": true
  },
  "category_seo": {
    "membership_reset_possible": true,
    "requires_membership_confirmation": true
  },
  "homepage_seo": {
    "requires_explicit_confirmation": true
  }
}
```

Agent 不得通过失败后试错的方式发现副作用。

## 11.6 能力健康状态

每项能力返回：

```text
available
degraded
unavailable
misconfigured
forbidden
```

并返回：

- `checked_at`；
- `last_success_at`；
- `error_code`；
- `error_summary`；
- `unlock_condition`。

## 11.7 验收用例

1. 新增业务后可一次获取该业务全部站点；
2. 主站及所有博客站均返回；
3. 未配置连接器显示 `misconfigured`，不能显示可写；
4. 不支持写入的站点显示 `forbidden` 或 `unavailable`；
5. 产品 Variant 和分类成员关系副作用可机器读取；
6. 域名、canonical host 和公开 URL规则明确；
7. 能力接口只读，不触发远端写入；
8. 能力变化后下一次策略运行使用新结果，不沿用旧权限。

---

# 12. 数据与持久化要求

## 12.1 复用现有数据库

优先复用：

- `seo_agent.tasks`；
- 现有站点、文章、产品、分类和连接器数据；
- 现有效果观察任务；
- `payload`、`decision`、`logs` 等 JSON 字段。

建议以现有任务类型扩展：

```text
strategy_run
strategy_run_event
strategy_action
strategy_exception
strategy_hold_coordination
strategy_effect
```

如现有结构无法满足唯一性、并发或查询性能，后端必须先提交 ADR，说明：

- 为什么现有结构不能满足；
- 新增表或索引的最小范围；
- 数据迁移和回滚方式；
- 对现有 API 的兼容影响。

## 12.2 数据约束

- `run_id`、`action_id`、`exception_id` 使用真实唯一 ID；
- `idempotency_key` 在规定作用域内唯一；
- URL 存储前必须规范化并校验站点归属；
- 时间统一使用带时区 UTC；
- 状态字段只能使用文档定义枚举；
- 数据源快照必须记录采集时间和新鲜度；
- provider/model 不得使用模糊占位值；
- 密钥和授权头不得进入任务 JSON 或日志。

# 13. API 通用规范

所有新接口统一返回：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "READBACK_MISMATCH",
    "message": "Remote state does not match submitted patch.",
    "retryable": false,
    "stage": "verifying",
    "details": {}
  },
  "request_id": "..."
}
```

要求：

- 错误码稳定、可枚举；
- HTTP 状态码与业务状态一致；
- 不以中文错误文本作为程序判断依据；
- 列表接口支持分页；
- 时间采用 ISO 8601；
- 所有写接口接收幂等键；
- 所有响应返回 `request_id`；
- OpenAPI 文档同步更新。

# 14. 可观测性要求

必须能够按以下维度检索：

- `run_id`；
- `action_id`；
- `business_id`；
- `site_id`；
- `request_id`；
- `idempotency_key`；
- `stage`；
- `status`；
- `exception_type`。

核心指标：

- 策略运行成功率；
- 各阶段耗时；
- 各数据源刷新成功率；
- 自动执行和待审批数量；
- 写操作回读不一致率；
- 幂等命中次数；
- Hold 连续次数；
- 补证次数及降级率；
- 异常数量及平均解锁时间；
- 已执行动作进入观察池的覆盖率。

# 15. 安全要求

- 所有接口验证业务和站点归属；
- 目标 URL 必须属于站点允许域名；
- 服务端重新执行权限判断，不信任调用方传入权限；
- 生产写操作必须有审计记录；
- API Token、数据库 DSN、授权头和用户凭证不得写日志；
- 生产迁移与测试迁移环境严格隔离；
- 取消操作只停止未开始动作，不伪造已执行动作回滚；
- 禁止 Agent 通过通用 patch 接口修改未声明字段。

# 16. 分阶段交付

## 阶段 A：状态机和数据库验证

- 修正第二次连续 Hold 触发语义；
- 完成 Token 防重、超时和接管；
- 建立 PG14/15/16 CI矩阵；
- 运行迁移、并发和审批门禁测试。

完成条件：第 7、8 节全部通过。

## 阶段 B：统一策略运行入口

- 创建、查询、事件、取消、重试接口；
- 固定状态机；
- 全站覆盖矩阵；
- 统一幂等键。

完成条件：Codex 可使用一个入口完成 dry-run。

## 阶段 C：统一执行闭环

- 统一动作记录；
- 写后回读；
- 异常中心；
- 效果观察；
- 回滚预览和受控回滚。

完成条件：preview和人工审批执行能够端到端闭环。

## 阶段 D：站点能力清单

- 能力读取接口；
- 副作用声明；
- 权限与连接健康；
- 新站点自动发现。

完成条件：Codex 无需阅读代码即可决定允许动作。

## 阶段 E：受控自动化

- 低风险动作自动执行；
- 高风险动作进入审批；
- 运行异常自动生成异常记录；
- 自动进入7/14/28/56天观察。

完成条件：连续运行14天无重复发布、跨站写入、静默失败或虚假成功。

# 17. 总体验收场景

## 场景一：正常执行

1. 调用统一接口；
2. 发现业务全部站点；
3. 获取能力和证据；
4. 为每站产生决策；
5. 自动执行允许动作；
6. 回读一致；
7. 创建观察任务；
8. 运行状态为 `completed`。

## 场景二：部分待审批

- 低风险动作完成；
- 首页或分类修改进入 `awaiting_approval`；
- 总运行状态为 `partial` 或 `awaiting_approval`；
- 批准后继续原运行，不重复低风险动作。

## 场景三：第二次连续 Hold

- 第一次全站 Hold 不刷新；
- 第二次决策确认全站 Hold 后领取 Token；
- 刷新一次；
- 使用最新审计重新决策；
- 新候选可执行则 Hold次数归零；
- 无候选则保存第二次 Hold。

## 场景四：并发执行

- 两个相同幂等键返回同一运行；
- 两个不同运行不能重复领取同一 Hold刷新；
- 同一文章或 URL 不会被重复写入；
- 状态计数不丢失。

## 场景五：回读不一致

- 远端 PUT 返回成功；
- GET 回读与提交内容不一致；
- 动作标记 `readback_mismatch`；
- 运行不得标记完整成功；
- 创建P1异常；
- 不进入正向效果观察。

## 场景六：新站点接入

- 能力接口返回站点角色、连接器、权限和副作用；
- 缺配置时只生成 `configuration_repair`；
- 配置补齐后下一次运行自动进入候选计算；
- 无需修改 Codex 调用流程。

# 18. 后端交付物

后端完成后必须提供：

1. 实现说明及架构决策；
2. 修改文件清单；
3. OpenAPI 文档；
4. 状态机说明；
5. 数据结构及索引说明；
6. PG14、15、16真实测试结果；
7. Hold并发和最新审计门禁测试结果；
8. 统一运行接口端到端示例；
9. 幂等重复调用证明；
10. 写后回读及不一致案例；
11. 异常和观察记录示例；
12. 站点能力接口示例；
13. 生产发布和回滚方案；
14. 已知限制。

# 19. 最终上线门禁

同时满足以下条件才能开放给 Codex：

- 五项功能全部完成；
- 全量测试无失败；
- PG14、15、16真实验收通过；
- 所有集成测试在发布流水线中不可静默跳过；
- 统一入口 dry-run通过；
- preview和审批执行端到端通过；
- 重复调用不重复写入；
- 写后回读不一致不会报成功；
- 所有完成动作进入观察池；
- 新站点能力可被机器读取；
- 生产密钥未进入日志；
- 先以人工审批模式连续运行7天；
- 再以低风险自动执行模式连续运行14天；
- 期间无跨站写入、重复发布、静默失败或虚假成功。
