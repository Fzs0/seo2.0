# AI 主导 SEO 自主运营正式链路目标

- 日期：2026-07-31
- 状态：目标完成；凭据轮换按用户决定延后为目标外安全行动
- 优先级：P1
- 目标使用者：Codex / 后端开发 Agent / 验收 Agent
- 修改范围：AI 策略运行接口、正式计划、调度门禁、候选池退役、日志与测试
- 明确不在范围内：前端交互改造；前端仅作为只读数据看板
- 2026-07-31 产品决策：地区化真实 SERP/Computer Use 与 SEMrush GUI 自动采集
  暂缓。不同地区结果不可混用；后续只有在目标国家/地区、语言、设备和采集网络
  明确后才恢复。本轮继续支持统一 Evidence Adapter、固定快照、GSC、GA4、
  Site API、已保存 SerpAPI 快照和明确标注的公开资料，不把暂缓项作为上线门禁。
- 背景总结：`docs/AI_LED_SEO_STRATEGY_SUMMARY_2026-07-31.md`

## 1. 可直接设置的 Goal

> 在不恢复旧发布旁路、不让候选池或固定评分替 AI 选题、不放松统一 Action、
> 审批、能力、幂等和独立回读门禁的前提下，将 SEO 2.0 收口为“AI 主导自主运营，
> 后端提供安全执行保障，人工只处理例外”的唯一正式链路。AI 必须能够针对每个业务
> 及其全部流量站点，从当前站点、产品、文章、页面、GSC、GA4、关键词库可选参考、
> 实时 SERP、历史执行效果，以及经用户授权后通过 Computer Use 从 SEMrush 等已登录
> 研究工具获取的数据中独立研究用户需求，自主决定新写文章、更新文章、
> On-page 修复、Hold 或配置修复，并负责内容、图片、执行编排、发布验收和效果学习。
> 后端不得重新选题，只能验证 business/site/目标身份、能力、受保护字段、同 URL/
> 同意图冷却、在途冲突、风险来源、幂等和执行容量；所有合格但未立即执行的动作必须
> 保存为 Deferred。候选池必须退出所有新策略的研究、排名、授权、Plan、Action 和
> 调度路径。整个调用范围为零可执行和零延后动作时，必须进入不可绕过的 Zero Action
> Review；研究不足时要求 AI 通过第二证据通道补证；非硬阻塞站点必须继续寻找
> 安全学习动作，但不得强制制造文章。最终完成
> `Scope Discovery → Evidence Snapshot → AI Research Portfolio → Proposed Actions
> → Backend Safety Review → Zero Action Review → Formal Plan → Unified Action
> → Preview → Approval → Execute → Independent Readback → Observation
> → AI Strategy Learning`，通过 PostgreSQL 17、接口级、并发、幂等、扩站和真实
> 连接器只读回读验收，并删除能够绕过该链路的旧决策入口。

## 2. 目标业务结果

完成后，用户只需下达：

```text
/执行策略
```

或指定业务、站点、页面的目标，AI 即可：

1. 自动发现调用范围内全部业务和流量站点；
2. 刷新当前证据；
3. 独立判断每站应新写、更新、On-page、Hold 还是配置修复；
4. 自主决定执行波次和延后动作；
5. 生成文章、SEO 字段、封面图、内容图和 Alt；
6. 通过正确站点 Adapter 执行；
7. 独立回读并检查公开结果；
8. 建立 7/14/28/56/90 天观察；
9. 从可比较结果中改进下一轮策略；
10. 在日志和数据库中完整说明做了什么、为什么做、结果如何。

人工默认不逐条选题、不逐篇写作、不逐站触发。只有高风险、重大业务方向或无法从
现有证据判断的例外才需要人工介入。

## 3. 不可变原则

### 3.1 AI 拥有编辑和运营决策权

以下事项只能由 AI 决定：

- 用户需求和搜索意图；
- Run-local 研究方向；
- 新写、更新、On-page、Hold 或配置修复；
- 目标文章、页面、产品或分类；
- 内容结构、正文、SEO 字段和图片方案；
- 优先级、执行波次和延后条件；
- 被拒绝的替代方案；
- 发布后是否需要继续优化；
- 效果属于 exploring、promising、validated、inconclusive 或 failed；
- 后续策略如何变化。

后端不得通过关键词量、KD、候选分数或固定权重覆盖 AI 的编辑判断。

### 3.2 后端只拥有硬门禁和执行权

后端可以：

- 允许具体动作进入执行；
- 因明确能力、安全、身份、冷却、冲突或远端不确定性降级；
- 把超过安全执行容量的合格动作转为 Deferred；
- 阻止错误站点、错误对象、未声明字段或未知远端状态；
- 要求 AI 补充研究；
- 执行、回读、恢复和追踪。

后端不可以：

- 选择另一个主题代替 AI 主题；
- 用候选池生成 Action；
- 把站点级低流量解释成无需求；
- 用一个旧 Action 阻塞整站；
- 丢弃合格但当前不能执行的动作；
- 把 Configuration Repair 静默改成普通 Hold。

### 3.3 人工只处理例外

人工介入条件必须明确、稳定并可审计：

- 医疗、健康、法规、尼古丁和重大安全声明；
- 大规模或不可轻易恢复的修改；
- 品牌定位和业务优先级冲突；
- AI 无法确认的商品事实；
- 远端状态不确定或跨业务风险；
- 系统首次开放某个新平台的写能力。

普通低风险动作在完成观察期和门禁验收后应允许自动执行。

## 4. 正式数据来源

### 4.1 AI 当前证据

AI 可以使用：

- 业务、站点、语言、市场和连接器信息；
- 当前产品、分类、文章、页面和媒体；
- GSC 页面和查询数据；
- GA4 落地页数据；
- 实时 SERP 或公开搜索回退；
- 当前官方或权威资料；
- 历史 Strategy、Action、Execution 和 Observation；
- 历史文章和 On-page 效果；
- 关键词库，且仅作为可选研究材料；
- 经用户授权后，通过 Computer Use 从 SEMrush 或其他已登录研究工具获取的当前
  域名、关键词、竞争和趋势数据。

### 4.2 候选池退役

候选池不得出现在新 Interface 的输入中。新链路必须满足：

- 新 Run 不读取候选池；
- 新策略不写候选池；
- `candidate_id` 不参与新 Strategy；
- 候选状态和分数不参与排名；
- 候选不能创建 Formal Plan 或 Action；
- 候选为空不影响 AI 研究；
- 候选存在不影响 AI 当前选择。

历史候选数据只读保留，直到旧 Run、旧 Action 和历史日志完成归档。不得为退役
候选池而破坏历史外键、JSON 引用或审计证据。

### 4.3 关键词库边界

`keyword_id` 可以完全为空。关键词库只允许：

- 提供历史表达和主题参考；
- 帮助 AI 形成需要实时验证的研究问题；
- 作为日志中的可选 evidence reference。

关键词库不能：

- 直接选择动作；
- 直接决定搜索意图；
- 直接授予执行权；
- 因无记录而阻止运行；
- 覆盖实时 SERP、当前产品和分析证据。

### 4.4 SEMrush GUI 研究边界

SEMrush 是可选 Evidence Source，不是策略入口。只有用户明确授权或当前任务明确要求
使用时，AI 才通过 Computer Use 操作现有 Windows 会话。

允许的研究动作包括：

- 查看 Domain Overview；
- 查看 Organic Research、Positions、Pages、Competitors 和 Keyword Gap；
- 设置与目标站点匹配的国家/地区、语言、设备和时间范围；
- 查看趋势、SERP Features、排名变化和估算流量；
- 在当前授权范围内导出与本轮研究直接相关的数据；
- 保存必要截图或导出工件用于复核。

默认禁止：

- 修改 SEMrush 账户、套餐、账单、用户、API Key 或项目设置；
- 购买额度、升级套餐或发起其他付费动作；
- 把第三方估算当作本站真实点击、会话、收入或转化；
- 把 SEMrush 主题直接转换为 Strategy 或 Action；
- 将登录凭据、Cookie、Token 或敏感账户信息写入日志；
- 因 SEMrush 不可用而停止全部策略研究。

Computer Use 只负责操作界面和获取证据，AI 仍负责解释数据、比较来源和制定策略。

### 4.5 通用 Evidence Source Interface

所有第一方、第三方、GUI、接口和公开搜索证据统一表示为：

```json
{
  "source_type": "gsc|ga4|site_api|serpapi|public_search|semrush_ui|other",
  "source_name": "SEMrush Organic Research",
  "captured_at": "2026-07-31T10:00:00+08:00",
  "data_window": {
    "start": "optional",
    "end": "optional"
  },
  "market": "US",
  "language": "en",
  "device": "desktop",
  "dimensions": [],
  "filters": {},
  "freshness": "current|recent|lagging|unknown",
  "fact_scope": "product_fact|first_party_performance|user_behavior|intent|competitor_estimate",
  "artifact_refs": [],
  "collection_status": "success|partial|empty|failed",
  "limitations": [],
  "decision_use": "..."
}
```

新增数据源时，只增加 Evidence Adapter 和来源契约，不修改 AI 策略动作枚举或正式
决策主流程。GUI 来源使用 Computer Use Adapter；接口来源使用对应 HTTP/数据库
Adapter；测试使用固定快照 Adapter。

### 4.6 数据来源优先级不是固定总排名

证据优先级必须按待回答问题决定：

| 待回答问题 | 主要证据 | 辅助证据 |
| --- | --- | --- |
| 商品、价格、库存、规格和页面身份 | 当前站点/商品/分类 API | 公开页面回读 |
| 本站真实展示、点击、查询和页面表现 | GSC | SEMrush 估算和排名趋势 |
| 本站真实会话和参与行为 | GA4 | 其他已验证第一方分析 |
| 当前搜索意图、页面类型和结果组成 | 实时 SERP/公开搜索 | SEMrush 关键词与竞争页面 |
| 竞争对手、关键词差距和市场规模估算 | SEMrush 等第三方工具 | 关键词库、实时 SERP |
| 历史策略效果 | Effect/Observation + GSC/GA4 | 公开页面、第三方趋势 |

来源冲突时，AI 必须比较：

- 指标定义；
- 国家/地区、语言和设备；
- 时间窗口和抓取时间；
- URL、查询和站点粒度；
- 第一方事实与第三方估算的性质；
- 数据是否为空、滞后或采集失败。

无法解释的实质冲突不得被静默平均或多数投票。AI 应补证、降低置信度、Deferred，
或在重大业务影响时升级人工。

## 5. 目标 Module 和 Interface

建立一个深 Module：`AutonomousStrategyOrchestrator`。

### 5.1 外部 Interface

建议只保留三个入口：

```text
capture_research(scope, evidence_snapshot, research_portfolio)
    → research_receipt

submit_proposed_actions(run_id, proposed_actions)
    → reviewed_plan

review_zero_action(run_id)
    → zero_action_review
```

Interface 隐藏：

- 数据表和 JSON payload 结构；
- 关键词和历史记录格式；
- 平台连接器差异；
- URL、意图和目标身份规范化；
- 冷却和冲突算法；
- 调度和安全上限；
- Formal Plan 和 Action 表结构；
- 观察任务创建。

调用方和测试均通过相同 Interface 验证最终行为。

### 5.2 Research Portfolio

每个站点必须有独立研究记录：

```json
{
  "site_id": "uuid",
  "site_language": "de",
  "site_market": "DE",
  "evidence_snapshot_id": "uuid-or-stable-hash",
  "research_questions": [],
  "material_options": [],
  "hard_blockers": [],
  "sources_attempted": [],
  "evidence_sources": [],
  "missing_evidence": [],
  "research_conclusion": "..."
}
```

要求：

- 不使用固定数量强迫生成填充选项；
- 非硬阻塞站点必须比较足够多且有实质差异的方向；
- 每个方向必须说明搜索意图、目标对象、当前证据和主要风险；
- 数据缺失必须记录尝试过的来源和决策影响；
- 一次全局 SERP 查询不能证明多个语言、市场和定位不同的站点没有机会；
- GUI、接口、数据库和公开搜索证据必须使用统一来源记录并保留采集状态。

### 5.3 Proposed Action

```json
{
  "site_id": "uuid",
  "action": "new_article|update_article|on_page_fix|hold|configuration_repair",
  "action_type": "optional concrete on-page action",
  "target_identity": {
    "target_url": "optional",
    "remote_object_id": "optional",
    "local_object_id": "optional",
    "intent_key": "optional",
    "topic_cluster": "optional"
  },
  "schedule_request": "execute_now|deferred|hold|configuration_repair",
  "topic": "optional",
  "title": "optional",
  "user_intent": "...",
  "decision_reason": "...",
  "evidence_refs": [],
  "alternatives_considered": [],
  "hypothesis": "...",
  "success_metrics": [],
  "reevaluation_condition": "optional",
  "priority": "P0|P1|P2|P3|Hold",
  "risk_level": "low|medium|high"
}
```

不得包含 `candidate_id`。`keyword_id` 不进入核心 Interface；如需审计关键词来源，
只在通用 `evidence_refs` 中保存，不参与后端准入或调度。

## 6. AI 职责

`AutonomousStrategyOrchestrator` 的 AI 实现负责：

### 6.1 发现和研究

- 发现全部当前范围站点；
- 获取并比较当前数据；
- 识别需求、页面缺口、内容缺口和技术缺口；
- 进行语言、市场和站点定位匹配的 SERP 研究；
- 在用户授权的任务中通过 Computer Use 操作 SEMrush 等研究工具；
- 选择真正有助于当前问题的报告、筛选条件、国家/地区、设备和时间范围；
- 区分产品事实、分析事实、外部意图证据和编辑推断；
- 识别第一方实际数据与第三方估算的差异；
- 在多来源冲突时解释选择、补证或降低置信度；
- 检测内容蚕食和站内链接机会；
- 识别是否应先进行 On-page 或配置修复。

### 6.2 策略选择

- 独立选择动作；
- 决定目标对象；
- 说明被拒绝方案；
- 决定现在执行或延后；
- 在后端返回硬门禁时重新研究，而不是等待人工替 AI 选题；
- 仅在存在有效整站硬阻塞时选择站点级 Hold；目标级阻塞后继续研究其他动作。

### 6.3 生成和 QA

- 使用当前 OpenAI/Codex 能力生成最终文本；
- 使用当前 OpenAI 图片能力或经过验证的站内产品图；
- 生成封面图、内容图和 Alt；
- 检查标题、Meta、H1/H2/H3、正文、FAQ、图片、链接和事实；
- 高风险事实必须使用当前权威来源；
- 不生成虚构商品事实、效果保证或法规结论。

### 6.4 执行编排和验收

- 决定执行波次；
- 为 Action 准备正式 patch；
- 读取后端门禁结果并重新规划；
- 检查独立回读；
- 检查公开页面；
- 对异常决定修复、延后或升级人工；
- 不因本地记账失败重复远程 PUT/POST。

### 6.5 效果学习

- 在 7/14/28/56/90 天解释页面级结果；
- 区分展示、排名、CTR、参与度、转化和收录；
- 保留失败和不确定结果；
- 只在触发条件和指标可比较时复用成功策略；
- 更新下一次 Research Portfolio，而不是生成新的固定候选池。

## 7. 后端职责

### 7.1 Evidence Snapshot

后端保存 AI 使用的当前证据引用、时间窗口、粒度、数据源状态和稳定哈希。后端不根据
快照替 AI 选题。

Evidence Snapshot 必须保留来源类型、采集方式、市场、语言、设备、筛选条件、新鲜度、
工件引用、失败状态和限制。`semrush_ui` 必须与 `serpapi`、`public_search` 和数据库
关键词记录明确区分，不得伪装成 API 数据或第一方数据。

### 7.2 Target Identity

目标身份必须由后端规范化。

更新现有对象：

```text
business_id + site_id + canonical_url/remote_object_id + action_type
```

新文章：

```text
business_id + site_id + language + market + normalized_intent_key
```

AI 提供主题和意图，后端生成并保存稳定冲突身份。低置信度意图匹配不得自动认定为
重复；应进入 Deferred、补证或人工确认。

### 7.3 Scope Conflict

以下情况才构成冲突：

- 同一 URL 正在执行或观察；
- 同一远端对象存在未终止 Action；
- 同一站点内高度重叠的意图已有新文章 Action；
- 远端状态不确定；
- 同一幂等身份重复提交。

以下情况不构成冲突：

- 同站不同 URL；
- 同站不同意图；
- 文章观察与无关产品页 SEO；
- 一个站点的 Action 与另一个站点的动作；
- 历史候选记录。

### 7.4 Safety Gate

后端只检查：

- business/site scope；
- 连接器和 Adapter；
- 能力快照；
- 目标对象归属；
- 允许字段和禁写字段；
- 当前远端版本；
- 媒体上传和封面能力；
- 风险来源要求；
- 幂等和远端不确定状态；
- 独立回读能力。

输出：

```text
allowed
deferred
hold
configuration_repair
research_revision_required
```

每个非 `allowed` 结果必须包含稳定原因码、证据和解锁条件。

### 7.5 Scheduler

- 保存所有合格动作；
- `action_budget` 只作为安全上限；
- 上限外动作进入 Deferred；
- 每站最多一个正在进行的远程写入；
- 当前波次完成独立回读后才能开始下一波；
- 不同动作类型分别计算成本和风险；
- 不把 Deferred 改成 Hold；
- 不因零动作自动降低门禁。

## 8. Zero Action Review

### 8.1 触发条件

```text
execute_now_count + deferred_count == 0
```

### 8.2 必查项目

1. Scope 内全部流量站点是否进入 Research Portfolio；
2. 非硬阻塞站点是否比较了有实质差异的方向；
3. Hold 是否有当前站点级证据；
4. 冷却是否命中相同 URL/意图；
5. 旧 Action 是否命中相同目标；
6. 是否把低 GSC/GA4 错当成无需求；
7. 是否执行了匹配语言和市场的第二证据通道；
8. 用户已授权且 SEMrush 对当前决策有实际价值时，是否正确采集或记录不可用原因；
9. 五类动作是否真实开放；
10. 合格动作是否因安全上限或站点配额丢失；
11. Configuration Repair 是否被正确保留；
12. AI 是否解释了为什么更新、新写和 On-page 都不合适。

### 8.3 复审结果

```text
research_revision_required
hard_blocked
configuration_skipped
```

- `research_revision_required`：返回缺失证据，AI 自动补充研究并重新提交；
- `hard_blocked`：经过验证的整站身份、能力或写入安全问题造成真实阻塞；
- `configuration_skipped`：站点配置修复已被明确记录，本轮不执行远程内容动作。

2026-08-03 起，非硬阻塞站点不再允许通过 `all_hold_review_passed` 完成 Run。
零动作复审返回 `SAFE_EXPERIMENT_REQUIRED`，要求继续研究不同 URL、主题或动作类型。

## 9. 正式状态和数据流

```text
queued
→ discovering_sites
→ gathering_evidence
→ ai_researching
→ proposed_actions_submitted
→ safety_reviewing
→ research_revision_required → ai_researching
→ planning
→ zero_action_reviewing（仅零动作时）
→ awaiting_approval
→ executing
→ verifying
→ observing
→ completed | partial | blocked | failed | canceled
```

如现有 `seo_agent.tasks.payload` 和 Run events 能表达新增阶段，优先复用现有结构，
不新增数据库表或字段。只有现有持久化无法支持恢复和审计时，才允许提出 migration。

## 10. Formal Plan 和 Action 不变量

- 一个 Run 只有一个当前有效 Formal Plan；
- Research Portfolio 和 Proposed Actions 必须绑定 Run；
- 每个 `execute_now` 选择一个正式 Strategy；
- 每个正式 Strategy 最多创建一个 Unified Action；
- Unified Action 必须引用 Run、Plan、Strategy、business、site 和 action type；
- Hold、Configuration Repair 和未选择选项不创建远程 Action；
- Deferred 必须完整保存并带重新评估条件；
- Action 必须绑定能力、before 和 patch 哈希；
- 能力、目标或 patch 变化使旧审批失效；
- 回读不一致不创建正向 Observation；
- 远端状态不确定禁止自动重试。

## 11. Configuration Repair 和站点覆盖修复

### 11.1 Configuration Repair

从 Proposed Action 到 Formal Plan、site result、Run counts 和日志必须始终保持：

```text
configuration_repair → configuration_repair
```

不得降成 generic Hold。必须有接口级和 PG17 回归测试。

### 11.2 全流量站点覆盖

`portfolio_daily` 必须发现：

- 全部 active 主站；
- 全部 active 博客站；
- 其他已接入且可承接自然流量的站点。

`strategy_enabled=false` 不得使站点消失。该站应形成：

- 明确的 Configuration Repair；
- 或带业务政策证据的 Explicit Exclusion。

Run 完成前必须对比精确 site_id 集合，而不只比较数量。

## 12. 候选池退役计划

### 阶段 A：停止影响

- 新链路禁止读取候选池；
- 新 Interface 删除 `candidate_id`；
- 候选不参与日志研究来源；
- Action 创建拒绝仅由候选授权的请求。

### 阶段 B：停止新写

- 停止创建新候选；
- 停止更新候选状态以驱动策略；
- 前端如仍展示，仅标记为历史只读数据。

### 阶段 C：删除旧业务逻辑

- 删除候选排名、转 Strategy、转 Action 和自动选中逻辑；
- 删除临时脚本中的候选依赖；
- 删除仅服务候选池的新运行入口；
- 删除已被正式 Interface 覆盖的旧测试。

### 阶段 D：历史归档

- 保留历史数据和审计读取；
- 验证旧 Run、Action、Effect 和日志仍可查询；
- 只有获得单独的数据保留决策后，才考虑物理删除历史数据。

## 13. 稳定错误码

至少提供：

| 错误码 | 结果 |
| --- | --- |
| `RESEARCH_PORTFOLIO_INCOMPLETE` | 要求 AI 补充范围 |
| `RESEARCH_EVIDENCE_INSUFFICIENT` | `research_revision_required` |
| `EVIDENCE_SOURCE_UNAVAILABLE` | 记录失败并使用允许的替代来源 |
| `EVIDENCE_PROVENANCE_INCOMPLETE` | 要求补齐来源、窗口和工件 |
| `EVIDENCE_CONFLICT_UNRESOLVED` | Deferred、补证或人工确认 |
| `SEMRUSH_UI_AUTH_REQUIRED` | 等待用户恢复已授权登录会话 |
| `ALL_HOLD_REVIEW_REQUIRED` | 禁止 Run 直接收口 |
| `ACTION_SPACE_ARTIFICIALLY_RESTRICTED` | 拒绝预先缩小动作空间 |
| `SITE_OUT_OF_SCOPE` | 拒绝选项 |
| `TARGET_IDENTITY_UNRESOLVED` | Hold 或补证 |
| `TARGET_CONFLICT_ACTIVE` | Deferred |
| `TARGET_REMOTE_UNCERTAIN` | Hold + P1 |
| `CAPABILITY_MISSING` | Configuration Repair |
| `PROTECTED_FIELD_REQUESTED` | 拒绝执行 |
| `SAFETY_CEILING_EXCEEDED` | Deferred |
| `PORTFOLIO_COVERAGE_INCOMPLETE` | 阻止 Run 收口 |
| `CONFIGURATION_REPAIR_LOST` | P1/P2 合同异常 |
| `SAFE_EXPERIMENT_REQUIRED` | 继续研究并提交安全学习动作，或提供有效整站硬阻塞 |
| `SITE_HARD_BLOCKER_INVALID` | 修正整站硬阻塞的结构和原因码 |

未知错误默认安全失败，不得降成普通 Hold。

## 14. 必须通过的验收场景

### 14.1 AI 自主性

1. 不提供候选池和关键词 ID，AI 仍能创建正式文章策略。
2. 关键词库包含高搜索量旧主题，AI 可以因当前证据冲突而拒绝。
3. 后端不能用其他主题替换 AI 主题。
4. AI 可以在同一站点选择文章、On-page 或 Hold。

### 14.2 冷却和冲突

5. Avinoti 旧 URL 在 28 天冷却中，不重叠的新意图仍可执行。
6. 同 URL 更新进入 Deferred。
7. 同站旧 Action 只阻止相同 URL/意图。
8. 一个站点观察任务不影响其他站点和其他动作类型。
9. 低置信度意图冲突不会直接整站 Hold。

### 14.3 研究质量

10. 德语站 GSC 数据少但未做德语需求研究时，不允许以“无需求”Hold。
11. 一次英文 SERP 查询不能支持多个不同站点全量 Hold。
12. GSC 为空但产品和 SERP 有需求时，允许新写或更新。
13. 外部搜索失败时，日志保存失败证据和降级路径。
14. 无整站硬阻塞且已列方向均不合格时，返回 `SAFE_EXPERIMENT_REQUIRED` 继续研究。
15. 地区化 SEMrush/搜索 GUI 真实采集已由用户暂缓，不计入本轮上线门禁；恢复后
    AI 必须保存 `source_type=semrush_ui`、市场、语言、设备、采集网络、时间、
    筛选条件和证据工件。
16. 地区化 GUI 采集恢复后，如未登录、页面失败或额度受限，不执行账户或付费操作；
    记录失败并继续使用 GSC、GA4、站点 API、匹配地区的已保存快照或权威资料。
17. SEMrush 估算与 GSC 实际数据冲突时，AI 不覆盖 GSC；根据指标用途说明差异。
18. 新增其他授权研究工具时，只增加 Evidence Adapter，不修改策略动作主流程。

### 14.4 调度和持久化

19. 10 个合格动作、容量 3：3 个 Execute Now、7 个 Deferred、0 丢失。
20. Configuration Repair 在 Plan、Run、counts 和日志中保持原类型。
21. `strategy_enabled=false` 的活动站点形成修复或显式排除记录。
22. 每个 Execute Now Strategy 只创建一个 Action。
23. Deferred、Hold 和未选择策略不创建远程 Action。
24. 同幂等键相同输入返回原结果；不同输入稳定冲突。

### 14.5 扩站

25. 新增第 N 个业务和多个站点后，覆盖矩阵自动包含全部目标站点。
26. 新平台只新增能力配置和 Adapter，不修改 AI 决策主流程。
27. OEMApps、Shopify、WordPress 和 Custom OpenAPI 使用各自正确 Adapter。
28. 一个站点能力缺失不阻塞其他站点。

### 14.6 执行和观察

29. 生成和媒体上传前完成 Formal Plan、Strategy、Action 和能力预检。
30. 写入后独立回读正文、SEO、图片、封面、Alt、日期和公开 URL。
31. 回读不一致创建异常且不建立正向观察。
32. 远端已成功但客户端超时时，先只读恢复，不重复写入。
33. 完成动作建立 7/14/28/56/90 天观察。
34. AI 能读取观察结果并形成下一轮 Research Portfolio。

## 15. 测试要求

### 15.1 Interface 级测试

测试 `AutonomousStrategyOrchestrator` 的可观察输入输出，不依赖内部函数或评分实现。

### 15.2 PostgreSQL 17

必须使用 PostgreSQL 17 验证：

- Run/Plan/Strategy/Action/Observation 血缘；
- 同目标并发竞争；
- 幂等键并发；
- Deferred 完整保存；
- Configuration Repair 类型保持；
- Run 覆盖集合；
- 零动作复审恢复；
- 租约、超时、旧 Token 和事务回滚。

### 15.3 Adapter

- 生产使用当前 HTTP/数据库/连接器 Adapter；
- 测试使用内存或固定快照 Adapter；
- SERP/GSC/GA4 测试不得依赖真实额度和网络；
- SEMrush/GUI 研究测试使用固定截图、导出夹具或内存 Evidence Adapter，不依赖真实
  登录、套餐、额度和页面稳定性；
- 每个正式平台至少有一个端到端只读回读验收；
- 远程写入只在用户授权的受控验收中执行。

## 16. 灰度和上线阶段

### 阶段 1：影子运行

- 新链路生成 Research Portfolio 和 Proposed Actions；
- 后端完成 Safety Review 和 Zero Action Review；
- 不创建远程 Action；
- 对比旧链路结果并记录误阻断、误执行和全量 Hold 比例。

### 阶段 2：人工审批

- 所有 Execute Now 动作进入审批；
- 验证 AI 选题、目标身份、事实、媒体和后端原因码；
- 运行至少一个完整效果检查点。

### 阶段 3：低风险自动执行

- 允许已验收的文章、媒体和基础 SEO 字段自动执行；
- 高风险内容继续审批；
- 监控错误站点、重复写入、回读不一致和策略停滞。

### 阶段 4：全业务扩展

- 新业务先完成站点身份、能力、Adapter 和只读回读验收；
- 自动进入统一覆盖矩阵；
- 不复制站点专属决策逻辑；
- 删除旧候选池和临时脚本决策旁路。

## 17. 完成定义

只有同时满足以下条件才能宣称目标完成：

1. AI 在没有候选池的情况下能完成完整研究和正式策略选择。
2. AI 是唯一编辑决策者，后端只执行硬门禁和安全调度。
3. 冷却和冲突精确到 URL、对象或意图，不存在整站误锁。
4. 全量零动作必须完成 Zero Action Review。
5. 研究不足返回补证，不伪装成“没有机会”。
6. 所有合格 Deferred 动作完整保存。
7. Configuration Repair 不再丢失。
8. 全部活动流量站点均有正式结果或显式排除。
9. 候选池已退出所有新策略和 Action 路径。
10. 关键词库保持可选且不拥有决策权。
11. SEMrush 和未来研究工具通过通用 Evidence Adapter 接入，不形成新的候选池。
12. 通用 Evidence Adapter 能表达 GUI 证据来源、窗口、市场、语言、设备、
    筛选条件、时间和可复核工件；地区化 GUI 的真实采集按用户决定暂缓。
13. 多来源冲突按问题类型、第一方性质和新鲜度解释或补证。
14. 新业务和平台通过 Adapter 与能力配置接入，不修改决策主流程。
15. Unified Action 的预览、审批、执行、回读、异常和观察闭环通过。
16. PostgreSQL 17、全量测试、并发测试和真实连接器只读验收通过。
17. 旧临时决策旁路已删除，正式运行只剩一条链路。
18. 文档、日志、异常和效果数据与数据库实际状态一致。

## 18. 最终正式链路

```text
AI：
发现 → 研究 → 选题 → 决策 → 生成 → 编排 → QA → 验收 → 学习

后端：
验证 → 持久化 → 调度 → 执行 → 回读 → 恢复 → 观察

人工：
设置目标 → 处理高风险 → 处理重大业务例外 → 查看结果
```

正式链路只有：

```text
AI Research Portfolio
→ AI Proposed Actions
→ Backend Safety Review
→ Zero Action Review
→ Formal Plan
→ Unified Action
→ Verified Execution
→ Effect Observation
→ AI Strategy Learning
```

不得保留候选池驱动、固定评分驱动、临时脚本预设 Hold 或平台旁路写入作为第二条
生产链路。
