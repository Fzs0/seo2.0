# SEO 自主运营后端 V2

本版本定位为“人工审批执行版”，不是无审批生产自动化。全局身份认证和
business scope 授权完成前，生产真实写适配器必须保持禁用。

## 安全边界

- 仅支持 PostgreSQL 17；应用启动和 CI 发布门禁都会检查主版本。
- 默认 Action adapter 返回稳定阻塞结果，不执行远端写入。
- 文章和基础 SEO 字段只有在能力快照声明可写、预览哈希和补丁哈希均获审批时
  才能交给显式注入的 adapter。
- URL、slug、canonical、redirect、删除、价格、库存、Variant、分类成员关系
  及未声明字段始终禁止。
- 回滚和无审批自动执行仍返回明确的禁止原因。

## Strategy Run

Run 使用现有 `seo_agent.tasks.payload` 持久化，不增加数据库结构。支持创建、
开始、查询、事件、取消和重试。阶段输出、事件、站点清单、能力快照和覆盖矩阵
均持久化，重启后从未完成阶段继续，已完成阶段不会重复执行。

```text
queued
→ discovering_sites
→ checking_capabilities
→ gathering_evidence
→ planning
→ refreshing_evidence → replanning
→ awaiting_approval
→ executing
→ verifying
→ observing
→ completed | partial | blocked | failed | canceled
```

`all_sites` 枚举业务下全部启用站点，`selected_sites` 逐一处理所有请求站点。
完成前同时校验站点数量和 site_id 集合；任一发现站点缺少
`execute | awaiting_approval | hold | configuration_repair | failed` 决策时，
Run 不得完成。

## Site Capabilities

能力阶段调用本地 Site Capabilities 服务并保存完整快照。快照包含读写/审批政策、
允许字段、已知副作用、连接器健康、数据新鲜度、配置问题、规范域名，以及忽略
刷新时间后计算的 `capability_snapshot_hash`。

未声明能力、异常连接器和未允许字段默认禁止。能力政策变化会改变哈希，使旧审批
失效并要求重新 preview。

## Action 闭环

```text
planned → previewed → approved → executing → verifying
        → completed | blocked | failed
```

审批绑定 `snapshot_hash + patch_hash + capability_snapshot_hash`。执行保存 Token、
领取时间、租约到期时间、尝试次数和心跳。平台比较
`approved_patch / submitted_patch / remote readback`，对 HTML、空值和声明的只读
字段做规范化，并输出逐字段 `expected / submitted / actual / match`。

关键字段不一致时返回 `readback_mismatch`，创建 P1 异常，Run 只能进入 partial
或 failed，并且不创建正向观察。一致时，平台按 action 幂等创建第 7、14、28、
56 天观察计划。

租约超时后 `/recover` 先做远端只读分类：

- `confirmed_not_applied`：允许新 Token 重试；
- `confirmed_applied`：补写本地完成状态；
- `partially_applied`：阻塞并创建异常；
- `unknown`：禁止自动重试。

旧 Token 或过期 Token 不能提交结果。

## API 契约

Run、Action 和 Capabilities 成功及失败统一返回：

```json
{
  "ok": true,
  "request_id": "request-id",
  "data": {},
  "error": null
}
```

写接口要求 `Idempotency-Key`（Run 创建和重试在请求体中携带）。校验错误也使用
稳定错误码，不依赖错误文本判断 HTTP 状态。

## PostgreSQL 17 发布门禁

仅可指向数据库名含 `test`、`temp` 或 `tmp` 的一次性数据库：

```powershell
$env:SEO_PG17_TEST_DSN = "postgresql://seo:password@127.0.0.1:5432/seo_v2_test"
.\scripts\test-pg17-release-gate.ps1
```

该门禁执行全量 migration、031/032 双跑、Hold 多连接并发、Token 超时接管、
最新审计批次审批门禁和相同幂等键并发创建。缺少连接时命令硬失败。

## 当前限制

- `/start` 是同步、可恢复的显式执行入口，尚未提供独立常驻队列 worker。
- 真实 adapter 只能在完成全局认证和 business scope 授权后按站点注入。
- Action 子操作接受幂等键，并由动作状态和执行 Token 防止重复写；尚未保存每个
  preview/approve 请求的独立幂等回执。
- 恢复分类的远端只读调用当前仍可能占用动作事务锁；这是安全但保守的实现。
- 真实回滚和无审批自动执行继续阻塞。
